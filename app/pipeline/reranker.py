"""
리랭커 - 초기 검색 결과를 Cross-Encoder로 재순위화합니다.
BAAI/bge-reranker-v2-m3 사용 (CPU 전용)
"""

import logging
import re
from typing import List, Optional

from app.config import settings
from app.models import QueryAnalysis, SearchResult

logger = logging.getLogger(__name__)

# Phase 2 Step B (2026-04-12): 청크 텍스트에서 URL 존재 여부 감지용 패턴.
# asks_url 엔티티가 있을 때 URL 포함 청크에 가산점 부여.
_URL_IN_CHUNK_PATTERN = re.compile(
    r"https?://|\b[a-z0-9][a-z0-9.-]*\.(?:bufs\.ac\.kr|go\.kr|ac\.kr|or\.kr|com)\b",
    re.IGNORECASE,
)


# Phase 4 (2026-04-12): Near-deduplication — 앞 100자가 동일한 중복 청크 제거.
# 같은 PDF 페이지를 다른 chunk_size로 split할 때 발생하는 중복 대응.
# 최고 점수 청크(이미 sorted)를 우선 보존 → recall 영향 없음.
def _dedup_near_similar(scored_list: list, prefix_len: int = 100) -> list:
    """앞 prefix_len자가 동일한 중복 청크 제거 (같은 PDF 페이지 split 대응)."""
    seen, deduped = set(), []
    for score, result in scored_list:
        prefix = (result.text or "")[:prefix_len].strip()
        if prefix not in seen:
            seen.add(prefix)
            deduped.append((score, result))
    return deduped


# Phase 4 (2026-04-12): Score Gap Pruning (knee detection).
# 연속 점수 간 최대 상대 낙폭 >= 25% 지점에서 절단.
# score_range < 1.0이면 분포가 좁으므로 컷 안 함 (recall 보호).
# 예) [3.2, 2.9, 0.4, 0.3]: range=2.9, gap 2.9→0.4=2.5 → 86% → index 2 반환.
def _find_knee_cut(scored_list: list, min_keep: int = 3) -> int:
    """연속 점수 간 최대 상대 낙폭 >= 25% 지점에서 절단. 안전 컷 없으면 len 반환."""
    if len(scored_list) <= min_keep:
        return len(scored_list)
    scores = [s for s, _ in scored_list]
    score_range = scores[0] - scores[-1]
    if score_range < 1.0:
        # 분포가 좁음 → 노이즈/정답 구분 불가 → 컷 안 함
        return len(scored_list)
    best_gap, cut_at = 0.0, len(scored_list)
    for i in range(min_keep, len(scored_list)):
        rel = (scores[i - 1] - scores[i]) / score_range
        if rel > best_gap and rel > 0.25:
            best_gap, cut_at = rel, i
    return cut_at


class Reranker:
    """
    [역할] Vector 검색 후보를 Cross-Encoder로 재순위화
    [모델] BAAI/bge-reranker-v2-m3
    [방식] (query, passage) 쌍을 입력받아 관련도 점수 산출
    """

    def __init__(self, model=None):
        self._model = model

    @property
    def model(self):
        if self._model is None:
            # shared_resources 싱글톤에서 이미 로드된 모델 사용
            from app.shared_resources import get_reranker_model
            self._model = get_reranker_model()
        return self._model

    def rerank(
        self,
        query: str,
        results: List[SearchResult],
        top_k: int = None,
        analysis: Optional[QueryAnalysis] = None,
    ) -> List[SearchResult]:
        """
        검색 결과를 query와의 관련도 기준으로 재순위화합니다.

        Args:
            query: 사용자 쿼리
            results: 초기 검색 결과 리스트
            top_k: 반환할 상위 결과 수 (None이면 settings.reranker.top_k)
            analysis: 선택적 QueryAnalysis. entities["asks_url"]이 True면
                URL 포함 청크에 추가 가산점 적용 (Phase 2 Step B).

        Returns:
            재순위화된 SearchResult 리스트
        """
        if not results:
            return results

        top_k = top_k or settings.reranker.top_k

        # None/비문자열 텍스트 방어 (ChromaDB에서 None document 반환 시)
        valid = [(i, r) for i, r in enumerate(results) if r.text and isinstance(r.text, str)]
        if not valid:
            return results[:top_k]
        pairs = [[query, r.text] for _, r in valid]
        raw_scores = self.model.predict(pairs)

        # Tier 기반 doc_type 가중치:
        # Tier 1 (domestic, guide) = 공식 학사 PDF + 홈페이지 가이드 → 최우선
        #   - Tier 1a (domestic, PDF 학사안내) = **우선** (+22%)
        #   - Tier 1b (guide, 학생포털 스크랩) = 약간 후순위 (+18%)
        # Tier 2 (faq, 고정공지) = FAQ + 고정(📌) 공지 → 동등 경쟁
        # Tier 3 (기타) = 일반 공지, 장학, timetable 등 → boost 없음
        # 2026-04-11 수정 (버그 #6): FAQ boost 10% → 5%로 축소.
        # 이유: FAQ 청크가 과도한 boost로 PDF 원문을 top-5 밖으로 밀어내는 현상 방어.
        # 2026-04-12 수정 (Phase 2 Step A'): Tier 1 내 domestic > guide 하위 정책.
        # 이유: 사용자 지시 "소스 간 차이가 발생하면 2026학년도1학기학사안내.pdf 우선".
        # sc02 (장학금 12학점 vs 15학점) 해결. domestic과 guide의 bonus 차이(4%p)는
        # 단일 청크 경합 시 PDF 우선을 유도하되, guide에만 있는 URL/신청처 정보는
        # 여전히 top-K에 포함될 정도로 절제된 prior.
        _TIER1_DOMESTIC = "domestic"
        _TIER1_GUIDE = "guide"
        top_raw = max(raw_scores) if len(raw_scores) else 0.0
        tier1_domestic_bonus = abs(top_raw) * 0.22 if top_raw != 0 else 0.0
        tier1_guide_bonus = abs(top_raw) * 0.18 if top_raw != 0 else 0.0
        tier2_bonus = abs(top_raw) * 0.05 if top_raw != 0 else 0.0  # 0.10 → 0.05
        # Phase 2 Step B (2026-04-12): URL-aware boost (2단계).
        # (1) Tier 1 청크에 URL이 있으면 소폭(+4%p) 추가 가산 — 같은 Tier 내 URL 우선.
        # (2) Tier 3+ 청크에 URL이 있으면 Tier 1 guide 수준(+18%)으로 격상 —
        #     notice_attachment(KOSAF 공지), scholarship 등이 domestic과 경쟁 가능하게 함.
        # sc01: retrieval top-3에 KOSAF notice_attachment가 있으나 Tier 3이라 밀림 → 격상.
        url_add_bonus = abs(top_raw) * 0.04 if top_raw != 0 else 0.0  # Tier 1 URL 추가
        url_promotion_bonus = tier1_guide_bonus  # Tier 3+ URL 격상
        asks_url = bool(analysis and analysis.entities.get("asks_url"))

        # Phase 3 Step 2 (2026-04-12): Tier 1 내 source_rank tiebreak.
        # 동일 doc_type(대부분 domestic) 내에서 학사안내 PDF(rank=1)와
        # 신입생 가이드북(rank=2)이 경합할 때, 학사안내를 우선하도록 소폭 가산.
        # 사용자 지시: "상반되는 데이터가 있다면 2026-1학기 학사안내가 우선적용"
        # 2%p는 Phase 2의 domestic/guide 차이(4%p)보다 작은 수준으로, 같은 질문에
        # 학사안내가 top-1/2에 배치되되 가이드북이 top-3~5로 함께 포함되는 선.
        source_rank_bonus = abs(top_raw) * 0.02 if top_raw != 0 else 0.0

        boosted_scored = []
        for raw, (_, r) in zip(raw_scores, valid):
            s = float(raw)
            dt = r.metadata.get("doc_type", "")
            is_tier1 = dt in (_TIER1_DOMESTIC, _TIER1_GUIDE)
            url_has = asks_url and bool(_URL_IN_CHUNK_PATTERN.search(r.text or ""))
            rank = int(r.metadata.get("source_rank", 2) or 2)

            if dt == _TIER1_DOMESTIC:
                s += tier1_domestic_bonus
                if url_has:
                    s += url_add_bonus
                # Tier 1 domestic 내 source_rank=1(학사안내)에 소폭 가산
                if rank == 1:
                    s += source_rank_bonus
            elif dt == _TIER1_GUIDE:
                s += tier1_guide_bonus
                if url_has:
                    s += url_add_bonus
                if rank == 1:
                    s += source_rank_bonus
            elif dt == "faq":
                s += tier2_bonus
            elif dt == "notice" and r.metadata.get("is_pinned"):
                s += tier2_bonus
            elif url_has and not is_tier1:
                # asks_url 질문에 대한 Tier 3+ URL 청크 격상
                s += url_promotion_bonus
            boosted_scored.append((s, r))

        scored = sorted(boosted_scored, key=lambda x: x[0], reverse=True)

        # Phase 4 (2026-04-12): 리랭커 레벨 near-dedup 제거.
        # context_merger가 이미 120자 prefix 기반 dedup을 수행하므로 reranker 수준 dedup은
        # 불필요하고 오히려 rank 7~10의 relevant 청크를 제거할 위험.
        # (g01 GRADUATION_REQ 졸업학점 청크 회귀 사례 확인 후 제외)
        # scored = _dedup_near_similar(scored)  # 보류

        # 이중 컷오프: 동적 + 절대 하한
        reranked = []
        top_score = scored[0][0] if scored else 0.0
        relative_threshold = top_score * 0.5 if top_score > 0 else -float("inf")
        absolute_floor = -3.0  # 기존 유지: -2.5로 올리면 g01 graduation chunk 회귀 발생
        effective_threshold = max(relative_threshold, absolute_floor)

        for score, result in scored[:top_k]:
            if score < effective_threshold and len(reranked) >= 3:
                break
            result.score = float(score)
            reranked.append(result)

        # 2026-04-11 수정 (버그 #6): FAQ/PDF diversity guarantee.
        # top-k가 전부 FAQ로만 채워지면 PDF 원문이 검색 결과에 포함되지 않아
        # LLM이 FAQ의 짧은 답변만 보고 생성. a01(대체/동일과목) 원인 해결용.
        # top-k 중 PDF가 0개이고 scored 리스트에 PDF 후보가 있으면 최상위 PDF
        # 1개를 강제 삽입.
        def _is_pdf(result):
            dt = (result.metadata or {}).get("doc_type", "")
            # "PDF 원문"으로 간주되는 doc_type: Tier 1 (domestic/guide) + 기타 PDF 파생
            return dt in (_TIER1_DOMESTIC, _TIER1_GUIDE, "scholarship", "timetable", "notice_attachment")

        if reranked and not any(_is_pdf(r) for r in reranked):
            # PDF 후보 탐색 (scored 리스트에서 reranked에 없는 최상위 PDF)
            for score, result in scored:
                if result in reranked:
                    continue
                if _is_pdf(result) and score >= absolute_floor:
                    result.score = float(score)
                    # 마지막 자리에 삽입 (기존 FAQ 최하위를 대체)
                    if len(reranked) >= top_k:
                        reranked[-1] = result
                    else:
                        reranked.append(result)
                    logger.debug("reranker: forced PDF inclusion (diversity guard)")
                    break

        logger.debug(
            "리랭킹: %d개 후보 → %d개 선택 (top=%.3f, rel_th=%.3f, eff_th=%.3f)",
            len(results),
            len(reranked),
            top_score,
            relative_threshold,
            effective_threshold,
        )
        return reranked
