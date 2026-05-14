"""
컨텍스트 통합기 - Vector/Graph 검색 결과를 하나의 프롬프트용 컨텍스트로 병합
CPU 전용, ~2ms 처리

원칙 2: 인텐트별 적응형 RRF 가중치 + 컨텍스트 예산

## 컨텍스트 우선순위 (2026-04-16 확정)

merged.formatted_context 구성 시 아래 순서로 예산(max_chars) 소비:

1. **transcript_context** — 학업성적사정표 기반 개인 데이터 (chat.py `_enrich_analysis`가
   외부에서 생성해 `merge(transcript_context=...)` 인자로 주입). LLM 전송 전
   `PIIRedactor.redact_for_llm()` 명시 처리 완료된 문자열.
2. **direct_answer** — 그래프·FAQ의 `direct_answer` 플래그 청크 (IDF·stem coverage
   게이트 통과한 고신뢰 답변). `_try_extract_direct_answer` 폴백 포함.
3. **FAQ 승격 청크** — 그래프 IDF·특이성 점수 통과한 FAQ (`promoted_faq`).
4. **벡터 domestic/guide 청크** — 공식 PDF (학사안내, 가이드북). 문서 우선순위
   `source_rank=1`이 2위(가이드북)보다 RRF 결합 단계에서 우선.
5. **벡터 notice/scholarship 청크** — 일반 공지·장학 (RRF 순위 그대로).
6. **redirect FAQ** — "어디서 확인/문의" 안내형 (본문 재료 아니므로 최후순위).

참고: `transcript_context`는 merge 내부가 아닌 호출자(chat.py)가 생성·마스킹·주입하므로
이 파일에서는 raw 추가만 수행하고 우선순위 보호는 호출 순서로 달성된다.
"""

import logging
import re
from typing import List, Optional

from app.models import SearchResult, MergedContext, Intent, QuestionType
from app.pipeline.answer_units import aligns as _answer_unit_aligns

logger = logging.getLogger(__name__)

# 대략적인 토큰 추정: 한국어 1글자 ≈ 1.5 토큰 (즉 1토큰 ≈ 0.67글자)
TOKENS_PER_CHAR = 1.5
_DEFAULT_CONTEXT_TOKENS = 1200

# ── 원칙 2: 인텐트별 적응형 설정 ────────────────────────────────
# (graph_weight, vector_weight) — Tier 1 (domestic+guide 벡터) 우선 정책 반영
#
# 가중치 재조정 근거 (2026-04-10 정보부족 진단):
# - 그래프 핸들러 결과의 score는 고정값(1.0~1.3)이라 실제 관련성 신호가 아님.
#   RRF는 rank 기반이므로 graph_weight가 높으면 제네릭 FAQ가 벡터의 정답 청크를 밀어냄.
# - 벡터에 Tier 1(domestic/guide) 공식 PDF가 포함되므로 학번별 표·다조건 규정 질문
#   (q019, q022, q044, q050 등)은 벡터 우선이 정답률에 유리.
# - SCHEDULE만 예외: 그래프의 학사일정 노드가 실제 정답을 보유.
_INTENT_WEIGHTS = {
    Intent.SCHEDULE:         (2.0, 0.8),   # 유지: 그래프(학사일정) 직접 답변
    Intent.ALTERNATIVE:      (1.2, 1.2),   # 유지: FAQ·PDF 동등
    # GRADUATION_REQ: 그래프에 `2022학번 내국인 졸업요건`(졸업학점 130) 같은
    # 학번별 구조화 노드가 있으므로 그래프를 다시 동등 이상으로 둠.
    # q042 회귀 교훈: 벡터 우선으로 바꿨더니 그래프의 정답 노드가 밀려났음.
    # gw=2.0으로 상향: tier1 벡터(vw=1.3×1.3 boost=0.0277)보다 높아야
    # 그래프 노드(0.0328)가 컨텍스트 선두에 들어감 (budget 소진 전 보장)
    Intent.GRADUATION_REQ:   (2.0, 1.3),   # 그래프 확실 우선, 벡터 보조
    Intent.REGISTRATION:     (1.0, 1.5),   # 그래프 편향 완화 유지
    # EARLY_GRADUATION도 그래프에 학번별 졸업요건 노드 사용 — 그래프 우선
    Intent.EARLY_GRADUATION: (1.4, 1.3),
    Intent.MAJOR_CHANGE:     (1.0, 1.5),   # 학번별 표 분산 대응 (벡터 우선 유지)
    Intent.SCHOLARSHIP:      (0.8, 1.5),   # 그래프에 장학 노드 없음
    Intent.LEAVE_OF_ABSENCE: (1.0, 1.5),   # 벡터 PDF 우선
    Intent.COURSE_INFO:      (0.8, 1.5),   # 시간표 벡터 청크 우선
    # P4 (2026-04-17): GENERAL 가중치 상향 — 범용 질의에서 그래프 기여도 회복.
    # 이전: graph=0.8로 벡터에 완전히 묻힘 → 전반적 규정 질의에서 그래프 노드 미활용.
    # 개선: graph=1.2, vector=1.3 → 동등 경쟁 + 벡터 약우선.
    Intent.GENERAL:          (1.2, 1.3),   # Global 테마 요약에도 그래프 활용
}
_DEFAULT_WEIGHTS = (1.2, 1.2)  # Intent 미지정 시 기본: 동등 경쟁

# ── 원칙 2: QuestionType별 가중치 변조 (graph_mod, vector_mod) ──
# 토픽 Intent 가중치에 곱하여 질문 유형에 따라 vector/graph 밸런스를 동적 조정
_QT_WEIGHT_MODIFIERS = {
    QuestionType.OVERVIEW:    (0.8, 1.2),   # 개요 → 벡터(PDF) 강화, 그래프↓
    QuestionType.FACTOID:     (1.0, 1.0),   # 사실 → 기본값
    QuestionType.PROCEDURAL:  (0.9, 1.1),   # 절차 → 벡터 약간↑
    QuestionType.REASONING:   (1.3, 0.7),   # 추론 → 그래프↑ 벡터↓
}
_DEFAULT_QT_MOD = (1.0, 1.0)

# 인텐트별 컨텍스트 토큰 예산 — 단답형은 작게, 복합형은 크게
# 2026-04-10 q042 회귀 조사 결과: GRADUATION_REQ/MAJOR_CHANGE/REGISTRATION은
# 벡터(변경사항 표) + 그래프(학번별 구조화 노드) 양쪽을 모두 담아야
# LLM이 학번별 정답을 올바르게 합성할 수 있음. 따라서 예산을 여유있게 상향.
_INTENT_BUDGET = {
    Intent.SCHEDULE:       1000,
    Intent.ALTERNATIVE:    800,
    Intent.GRADUATION_REQ: 1800,   # 1400 → 1800: 그래프 + 벡터 병행 수용
    Intent.REGISTRATION:   1500,   # 1200 → 1500
    Intent.COURSE_INFO:    1200,
    Intent.SCHOLARSHIP:    1200,   # 1000 → 1200
    Intent.LEAVE_OF_ABSENCE: 1200, # 1000 → 1200
    Intent.EARLY_GRADUATION: 1600, # 1200 → 1600
    Intent.MAJOR_CHANGE:   1600,   # 1200 → 1600: 다년도 표 수용
    Intent.TRANSCRIPT:     1600,
}

# RRF 상수
_RRF_K = 10

# Adaptive Score-Gap Thresholding 파라미터 (2026-04-10 medium 실패 진단)
# RRF 병합 결과에서 1등 대비 아래 비율 미만으로 떨어진 청크는 "노이즈"로 간주해 컷.
# 실측 분포 기준(q040/q057/q058):
#   vector→graph 전환 지점에서 비율이 0.73→0.51로 급락 (gap 명확)
# 정답 유지 문항(q042)은 상위 5개가 0.786 이상으로 이 cutoff를 통과.
_ADAPTIVE_CUT_RATIO = 0.70
_ADAPTIVE_MIN_KEEP = 3

# Phase 4 (2026-04-12): Intent별 adaptive cutoff 완화.
# EARLY_GRADUATION / MAJOR_CHANGE는 관련 청크가 적어 0.70 cutoff에서 잘리는 경우가 있음.
# 0.60으로 완화 → 더 많은 청크가 context budget에 진입 가능.
# 기타 intent는 기존 0.70 유지 (precision 보호).
_INTENT_CUTOFF_RATIO: dict = {
    Intent.EARLY_GRADUATION: 0.60,
    Intent.MAJOR_CHANGE:     0.60,
}


def _adaptive_cutoff(
    results: List[SearchResult],
    ratio: float = _ADAPTIVE_CUT_RATIO,
    min_keep: int = _ADAPTIVE_MIN_KEEP,
) -> List[SearchResult]:
    """상위 점수 대비 일정 비율 미만 청크를 컷.

    원칙 2(비용·지연 최적화): 노이즈 청크가 토큰 예산을 먹는 것을 방지.
    원칙 1(유연한 스키마): 하드 "페이지당 N개" 제한 대신 데이터 분포 기반.

    - min_keep개는 무조건 보존 (reranker와 동일 원칙)
    - 1등 점수가 0 이하이면 cutoff 비활성 (안전)
    - transcript 청크(score≈10.0)처럼 인위적으로 부스트된 결과는 ratio 기준에서
      제외: 첫 번째 "일반 RRF 점수" 청크를 기준으로 삼는다.
    """
    if len(results) <= min_keep:
        return results

    # transcript 등 부스트된 선두 청크 건너뛰기 (score ≥ 1.0은 RRF 정상값보다 훨씬 큼)
    # 일반 RRF 점수는 1/(K+rank) * weight 수준이라 보통 < 0.2
    ref_idx = 0
    for i, r in enumerate(results):
        s = getattr(r, "score", 0.0) or 0.0
        if s < 1.0:
            ref_idx = i
            break
    else:
        # 모두 부스트된 경우(이상 케이스) 비활성
        return results

    ref_score = getattr(results[ref_idx], "score", 0.0) or 0.0
    if ref_score <= 0:
        return results

    # min_keep은 ref_idx 이후 기준으로 보장
    effective_min = max(min_keep, ref_idx + min_keep)
    for i in range(effective_min, len(results)):
        s = getattr(results[i], "score", 0.0) or 0.0
        if (s / ref_score) < ratio:
            return results[:i]
    return results


def _rrf_merge(
    graph_results: List[SearchResult],
    vector_results: List[SearchResult],
    graph_weight: float = 1.5,
    vector_weight: float = 1.0,
) -> List[SearchResult]:
    """
    Weighted Reciprocal Rank Fusion으로 그래프·벡터 결과를 병합합니다.
    score_rrf(d) = w_graph / (k + rank_graph(d)) + w_vector / (k + rank_vector(d))

    원칙 2: graph_weight/vector_weight는 인텐트에 따라 동적 조정됩니다.
    """
    rrf_scores: dict = {}   # id(result) → rrf_score
    result_map: dict = {}   # id(result) → SearchResult

    for rank, r in enumerate(graph_results, start=1):
        if r.metadata.get("source_type") != "transcript":
            r.metadata["source_type"] = "graph"
        rid = id(r)
        rrf_scores[rid] = rrf_scores.get(rid, 0.0) + graph_weight / (_RRF_K + rank)
        # transcript는 항상 최상위 — RRF 점수에 큰 가산
        if r.metadata.get("source_type") == "transcript":
            rrf_scores[rid] += 10.0
        result_map[rid] = r

    # Tier 1 doc_type (공식 학사 자료)은 RRF 점수 boost
    _TIER1_TYPES = frozenset({"domestic", "guide"})
    for rank, r in enumerate(vector_results, start=1):
        r.metadata["source_type"] = "vector"
        rid = id(r)
        base = vector_weight / (_RRF_K + rank)
        # Tier 1 공식 자료 boost: RRF 점수 +30%
        if r.metadata.get("doc_type") in _TIER1_TYPES:
            base *= 1.3
        # 고정공지(📌) boost: 그래프/FAQ(Tier 2)와 동등 → +15%
        elif r.metadata.get("doc_type") == "notice" and r.metadata.get("is_pinned"):
            base *= 1.15
        rrf_scores[rid] = rrf_scores.get(rid, 0.0) + base
        result_map[rid] = r

    merged = sorted(result_map.values(), key=lambda r: rrf_scores[id(r)], reverse=True)
    for r in merged:
        r.score = rrf_scores[id(r)]
    return merged


class ContextMerger:
    """
    [역할] Vector/Graph 검색 결과를 LLM 프롬프트용 컨텍스트로 통합
    [핵심] RRF(k=60)로 두 검색 채널을 순위 기반 병합 후 토큰 제한 내 선별
    """

    def merge(
        self,
        vector_results: List[SearchResult],
        graph_results: List[SearchResult],
        question: str = "",
        intent: Optional[Intent] = None,
        entities: Optional[dict] = None,
        transcript_context: str = "",
        question_type: Optional[QuestionType] = None,
    ) -> MergedContext:
        """검색 결과를 통합된 컨텍스트로 병합합니다.

        원칙 2: intent에 따라 RRF 가중치와 컨텍스트 예산을 동적 조정합니다.
        transcript_context가 있으면 합성 SearchResult로 선두 삽입합니다.
        """
        # 성적표 컨텍스트 주입 (PII 제거 상태)
        if transcript_context:
            from app.models import SearchResult as SR
            transcript_result = SR(
                text=transcript_context,
                score=10.0,  # RRF merge에서 항상 최상위 유지
                source="transcript",
                metadata={"source_type": "transcript"},
            )
            graph_results = [transcript_result] + list(graph_results)

        # 인텐트별 가중치 + 예산 결정
        gw, vw = _INTENT_WEIGHTS.get(intent, _DEFAULT_WEIGHTS)
        budget = _INTENT_BUDGET.get(intent, _DEFAULT_CONTEXT_TOKENS)

        # 원칙 2: QuestionType 변조 — 질문 유형에 따라 vector/graph 밸런스 조정
        if question_type:
            g_mod, v_mod = _QT_WEIGHT_MODIFIERS.get(question_type, _DEFAULT_QT_MOD)
            gw *= g_mod
            vw *= v_mod
            # OVERVIEW → 컨텍스트 예산 확장 (전반 안내에 더 많은 소스 필요)
            if question_type == QuestionType.OVERVIEW:
                budget = max(budget, 1400)
        # 성적표 컨텍스트가 있으면 예산 확장
        if transcript_context:
            budget = max(budget, 2000)

        # 그래프/FAQ direct_answer 존재 시 벡터 노이즈 억제
        # focused handler(≤3 결과)가 정확한 답을 제공 → 벡터 최소 보조만 허용
        direct_results = [r for r in graph_results if r.metadata.get("direct_answer")]
        if direct_results:
            if len(graph_results) <= 3:
                vw = 0.2   # PDF 출처 확보용 벡터 최소 유지
            else:
                vw = min(vw, 0.3)

        # 원칙 2: RRF 병합 전 원래 점수 캡처 (confidence 계산용)
        # RRF merge가 r.score를 순위 점수로 덮어쓰므로, 실제 관련성 점수를 보존
        _pre_rrf_vector_top = max((r.score for r in vector_results), default=0.0)
        _pre_rrf_graph_top = max((r.score for r in graph_results), default=0.0)

        # RRF로 그래프·벡터 결과 병합 (rank 기반, 인텐트별 가중치 적용)
        all_results = _rrf_merge(graph_results, vector_results, gw, vw)
        _pre_cutoff_count = len(all_results)

        # Adaptive Score-Gap Thresholding — 1등 대비 ratio 미만은 노이즈로 컷
        # (medium 실패 진단: q040/q057/q058은 vector→graph 전환점에서 급락)
        # Phase 4: intent별 완화 비율 적용 (EARLY_GRADUATION/MAJOR_CHANGE → 0.60)
        _cutoff_ratio = _INTENT_CUTOFF_RATIO.get(intent, _ADAPTIVE_CUT_RATIO)
        all_results = _adaptive_cutoff(all_results, ratio=_cutoff_ratio)
        _post_cutoff_count = len(all_results)

        # ── P0: 출처 매칭 부스트 (하이브리드 RAG 상호 검증) ─────────────
        # 벡터 청크와 그래프 노드가 동일 PDF 페이지 출처를 공유하면
        # 두 경로가 독립적으로 같은 근거를 가리킨다는 의미 → 신뢰도↑.
        # RRF 후 score에 ×1.2 부스트하여 교차 확인된 결과를 상위로 올림.
        _graph_pages: set[int] = set()
        _vector_pages: set[int] = set()
        for r in all_results:
            src = r.metadata.get("source_type", "")
            if src == "graph":
                sp = r.metadata.get("_source_pages")
                if isinstance(sp, (list, set)):
                    _graph_pages.update(int(p) for p in sp if isinstance(p, (int, float)))
                # page_number도 체크 (FAQ 등)
                pn = r.metadata.get("page_number")
                if isinstance(pn, (int, float)) and pn > 0:
                    _graph_pages.add(int(pn))
            elif src == "vector":
                pn = r.metadata.get("page_number")
                if isinstance(pn, (int, float)) and pn > 0:
                    _vector_pages.add(int(pn))
        # 교차 확인된 페이지 = 두 경로 모두에서 발견된 페이지
        _corroborated_pages = _graph_pages & _vector_pages
        if _corroborated_pages:
            _CROSS_BOOST = 1.2
            _boosted = 0
            for r in all_results:
                pn = r.metadata.get("page_number")
                sp = r.metadata.get("_source_pages")
                r_pages: set[int] = set()
                if isinstance(pn, (int, float)) and pn > 0:
                    r_pages.add(int(pn))
                if isinstance(sp, (list, set)):
                    r_pages.update(int(p) for p in sp if isinstance(p, (int, float)))
                if r_pages & _corroborated_pages:
                    r.score *= _CROSS_BOOST
                    _boosted += 1
            if _boosted:
                all_results.sort(key=lambda r: r.score, reverse=True)
                logger.debug(
                    "cross-source boost: %d results boosted (corroborated pages=%s)",
                    _boosted, _corroborated_pages,
                )

        # 원칙 2: 엔티티 기반 필터 — 단일 토픽 쿼리에서 무관 청크 차단
        all_results = self._filter_by_entity(all_results, entities)

        # 원칙 2: FAQ 승격은 하이브리드 시스템의 자체 신호(IDF·RRF·Cross-Encoder)에 위임
        #
        # direct_answer가 부여된 FAQ = 그래프 검색에서 IDF 가중치 + 특이성 페널티 +
        # stem 커버리지 게이트를 모두 통과 → 고신뢰 → 컨텍스트 최상단.
        # 그 외 FAQ는 RRF 점수 그대로 경쟁 (키워드 매칭으로 재판정하지 않음).
        # 리다이렉트 FAQ("어디서 확인/문의")는 본문 재료가 아님 → 항상 후순위.
        from app.graphdb.academic_graph import _is_redirect_answer as _is_redirect_answer_heur

        def _faq_answer_text(r: SearchResult) -> str:
            txt = r.text or ""
            idx = txt.find("A:")
            return txt[idx + 2:].strip() if idx >= 0 else txt

        def _is_redirect_faq(r: SearchResult) -> bool:
            if r.metadata.get("answer_type") == "redirect":
                return True
            return _is_redirect_answer_heur(_faq_answer_text(r), r.metadata)

        if all_results:
            promoted_faq, redirect_faq, rest = [], [], []
            for r in all_results:
                is_faq = (
                    r.metadata.get("doc_type") == "faq"
                    or r.metadata.get("node_type") == "FAQ"
                )
                if is_faq and _is_redirect_faq(r):
                    redirect_faq.append(r)
                elif is_faq and r.metadata.get("direct_answer"):
                    # 그래프 IDF·특이성·커버리지 게이트를 모두 통과한 FAQ만 승격
                    # B1/B2 threshold 강화로 OVERVIEW 별도 억제 불필요
                    promoted_faq.append(r)
                else:
                    # FAQ 포함 모든 결과: RRF 순서 유지 (하이브리드 점수 신뢰)
                    rest.append(r)
            all_results = promoted_faq + rest + redirect_faq

        # Phase 4 (2026-04-12): Intent keyword focus sort.
        # context budget 소비 루프(for result in all_results)는 순서대로 토큰을 소비.
        # 핵심 키워드를 포함한 청크를 앞으로 재배치 → 예산 내에 반드시 포함되도록 보장.
        # FAQ promotion 이후 적용 → promoted_faq 선두 보존.
        _INTENT_FOCUS_KWS: dict = {
            Intent.EARLY_GRADUATION: ("조기졸업",),
            Intent.MAJOR_CHANGE:     ("전과", "자유전공"),
            Intent.LEAVE_OF_ABSENCE: ("휴학",),
            Intent.SCHOLARSHIP:      ("장학", "TA"),
            Intent.ALTERNATIVE:      ("대체과목", "동일과목"),
        }
        _focus_kws = _INTENT_FOCUS_KWS.get(intent)
        if _focus_kws and all_results and len(all_results) > 3:
            _hits = [r for r in all_results if any(kw in (r.text or "") for kw in _focus_kws)]
            _miss = [r for r in all_results if r not in _hits]
            all_results = _hits + _miss
            logger.debug(
                "intent focus sort: intent=%s hits=%d/%d",
                intent, len(_hits), len(all_results),
            )

        # 토큰 제한 내에서 컨텍스트 구성
        context_parts = []
        total_chars = 0
        max_chars = int(budget / TOKENS_PER_CHAR)

        selected_vector = []
        selected_graph = []
        direct_answer = ""

        # 그래프·FAQ direct_answer를 동등하게 경쟁시킴 (RRF 순위 기반)
        # all_results는 이미 RRF 점수순으로 정렬되어 있으므로,
        # 가장 높은 RRF 점수를 받은 direct_answer를 채택한다.
        #
        # [Fix A: Semantic Gate] 2026-04-11 병목 진단.
        # 그래프 노드의 direct_answer는 "노드 토픽 하나당 한 문장"으로 베이크되어
        # 있어서, 같은 토픽의 다른 질문이 들어오면 엉뚱한 답을 내놓는다.
        #   예) 토픽 "OCU 초과학점" direct_answer = "~ 초과 신청이 가능합니다"
        #       - 질문이 "가능?" → OK
        #       - 질문이 "금액?" → 오답 (won 단위 없음)
        #       - 질문이 "최대?" → 오답 (credit 단위 없음)
        # AnswerUnit.aligns()는 질문의 기대 단위(credit/won/date/...)와 답변의
        # 실제 제공 단위가 일치하는지 검증한다. 불일치면 skip → 다음 후보 or
        # _try_extract_direct_answer 폴백 or LLM 경로.
        # [Rerank Bypass Gate] 2026-05-13: CrossEncoder raw logit 임계치 미만이면
        # direct_answer bypass 거부 → LLM 경유. 운영 로그 23쌍 실측 기반 임계치.
        # raw_score 없으면(reranker 미적용 경로) 게이트 우회 → 기존 동작 유지.
        from app.config import settings as _settings_rb
        _rb_thresh = _settings_rb.pipeline.rerank_bypass_threshold
        for result in all_results:
            if not result.metadata.get("direct_answer"):
                continue
            if _is_redirect_faq(result):
                continue
            _raw = result.metadata.get("raw_score")
            if _raw is not None and float(_raw) < _rb_thresh:
                logger.debug(
                    "direct_answer rejected by rerank gate: raw=%.3f < %.2f q=%r",
                    float(_raw), _rb_thresh, (question or "")[:60],
                )
                continue
            candidate = result.metadata["direct_answer"]
            if not _answer_unit_aligns(question, candidate):
                logger.debug(
                    "direct_answer rejected by AnswerUnit gate: q=%r da=%r",
                    (question or "")[:60], candidate[:60],
                )
                continue
            direct_answer = candidate
            break

        # OCU 섹션 트리밍 플래그 (질문에 OCU 미언급 시 활성화)
        _trim_ocu = bool(entities and not entities.get("ocu"))

        # 단일 청크가 예산을 독점하지 못하도록 상한 설정.
        # 상위 Rank 결과가 예산을 전부 먹어서 Rank 3~5의 정답 청크가
        # 들어가지 못하는 CAT_B/CAT_C 실패 패턴 방어.
        per_chunk_max = int(max_chars * 0.6)

        # 중복 청크 감지용 — 같은 PDF가 여러 소스 파일로 인제스트된 경우
        # (예: "2026학년도1학기학사안내.pdf" + "2026학년도 1학기 학사 안내_0123.pdf")
        # 동일 본문이 중복 선택되어 예산을 낭비하는 q042 회귀 사례 방어.
        _seen_text_prefixes: set[str] = set()

        for result in all_results:
            if not result.text:
                continue

            # FAQ direct_answer가 없을 때만 다른 소스의 direct_answer 수락
            # (동일 semantic gate + rerank gate 적용)
            if not direct_answer and result.metadata.get("direct_answer"):
                _raw2 = result.metadata.get("raw_score")
                if _raw2 is None or float(_raw2) >= _rb_thresh:
                    candidate = result.metadata["direct_answer"]
                    if _answer_unit_aligns(question, candidate):
                        direct_answer = candidate

            # 원칙 2: OCU 미언급 쿼리에서 혼합 청크의 OCU 섹션 동적 트리밍
            if _trim_ocu and result.metadata.get("source_type") != "graph":
                result_text = self._strip_ocu_section(result.text)
            else:
                result_text = result.text

            if not result_text or not result_text.strip():
                continue

            dedupe_sig = result_text.strip()[:120]
            if dedupe_sig in _seen_text_prefixes:
                continue
            _seen_text_prefixes.add(dedupe_sig)

            # 원칙 2+4 (2026-04-16 회귀 수정): 증거 텍스트 슬라이싱은
            # settings.pipeline.evidence_slicing_enabled 플래그로 가드.
            # 기본 ON이지만 A/B 테스트·비상 차단을 위해 환경변수 EVIDENCE_SLICING_ENABLED로 제어.
            from app.config import settings as _settings
            if _settings.pipeline.evidence_slicing_enabled:
                result_text = self._slice_evidence_text(
                    result_text,
                    question=question,
                    entities=entities,
                    question_type=question_type,
                )

            text_len = len(result_text)
            remaining = max_chars - total_chars

            # 예산 거의 소진 — 이 청크는 skip하되 loop는 계속 (더 작은 청크가
            # 들어올 여지를 남김). 기존 break 제거.
            if remaining < 80:
                continue

            # 단일 청크 상한 적용 — 다양성 보장을 위해 첫 청크가 독점 못 하게.
            chunk_budget = min(remaining, per_chunk_max)

            if text_len > chunk_budget:
                # 남은 공간(또는 단일 상한)에 맞게 자르기
                truncated = result_text[:chunk_budget] + "..."
                context_parts.append(self._format_result(result, truncated))
                total_chars += chunk_budget
                result.metadata["in_context"] = True
                if result.metadata.get("source_type") == "graph":
                    selected_graph.append(result)
                else:
                    selected_vector.append(result)
                continue

            context_parts.append(self._format_result(result, result_text))
            total_chars += text_len
            result.metadata["in_context"] = True

            if result.metadata.get("source_type") == "graph":
                selected_graph.append(result)
            else:
                selected_vector.append(result)

        formatted = "\n\n".join(context_parts)
        token_estimate = int(len(formatted) * TOKENS_PER_CHAR)

        # 공지사항 출처 URL 수집 (중복 제거)
        source_urls = self._collect_source_urls(selected_vector + selected_graph)

        # direct_answer가 없으면 컨텍스트에서 팩트 자동 추출 시도
        if not direct_answer and formatted and question:
            extracted = self._try_extract_direct_answer(question, formatted, entities)
            if extracted:
                direct_answer = extracted

        # [Fix A final gate] 어느 경로로 설정됐든 direct_answer는 최종적으로
        # AnswerUnit.aligns()를 통과해야 한다. 불일치면 폐기 → LLM 경로로 위임.
        # 이 게이트는 graph metadata / main loop / _try_extract_direct_answer 모두
        # 커버한다. 새로운 direct_answer 세팅 경로가 추가돼도 이 한 줄로 안전.
        if direct_answer and question and not _answer_unit_aligns(question, direct_answer):
            logger.info(
                "direct_answer final-gate rejected: q=%r da=%r",
                (question or "")[:60], direct_answer[:80],
            )
            direct_answer = ""

        # 원칙 2: context_confidence = 카운트 + 실제 점수 결합 신호 (0.0~1.0)
        #
        # 이전 버전은 단순 카운트 기반(n_selected)이었으나, 3건의 무관한 청크가
        # 0.8을 받고 LLM이 틀린 답을 생성하는 CAT_C 패턴이 재현됨.
        # 실제 관련성은 `_pre_rrf_vector_top` (리랭커 활성 시 BGE-Reranker logit,
        # 비활성 시 cosine 유사도)에 반영되어 있으므로 보정 신호로 사용.
        #
        # 로직:
        #   1) direct_answer 있으면 1.0 (변경 없음)
        #   2) 카운트 기반 baseline (3+=0.8 / 2=0.6 / 1=0.4)
        #   3) 벡터 top score로 상/하한 조정:
        #      - logit < 0 (명백한 무관) → 최대 0.3
        #      - logit > 2 (명백한 관련) → 최소 0.8
        #      - 그 외 중간값 → baseline 유지
        #   4) 그래프 점수는 핸들러 고정값이라 신호로 사용하지 않음
        if direct_answer:
            confidence = 1.0
        elif selected_vector or selected_graph:
            n_selected = len(selected_vector) + len(selected_graph)
            if n_selected >= 3:
                confidence = 0.8
            elif n_selected >= 2:
                confidence = 0.6
            else:
                confidence = 0.4

            # 벡터 점수 기반 보정 (원본 리스트가 비어있지 않을 때만)
            if vector_results:
                if _pre_rrf_vector_top < 0:
                    # BGE-Reranker logit 음수 = 확실히 무관 → 상한 제한
                    confidence = min(confidence, 0.3)
                elif _pre_rrf_vector_top > 2.0:
                    # BGE-Reranker logit > 2 = 확실히 관련 → 하한 보장
                    confidence = max(confidence, 0.8)

            # Adaptive cutoff가 결과를 크게 줄였다면 → 쿼리 중의성이 의심되는
            # 상황. P4 재시도 루프(confidence<0.5 트리거)가 동작하도록 클램프.
            # 실측 기준: q040/q057/q058은 RRF 8-14개 중 4-5개만 남음 (≈50% 이하).
            if _pre_cutoff_count >= 5 and _post_cutoff_count <= _pre_cutoff_count * 0.6:
                confidence = min(confidence, 0.4)
        else:
            confidence = 0.0

        return MergedContext(
            vector_results=selected_vector,
            graph_results=selected_graph,
            formatted_context=formatted,
            total_tokens_estimate=token_estimate,
            direct_answer=direct_answer,
            source_urls=source_urls,
            context_confidence=confidence,
        )

    @staticmethod
    def _try_extract_direct_answer(
        question: str,
        context: str,
        entities: Optional[dict] = None,
    ) -> str:
        """
        질문 유형에 따라 컨텍스트에서 핵심 팩트를 정규식으로 직접 추출.
        추출 성공 시 LLM을 우회하여 정확도 향상 + 지연 시간 절감.
        추출 실패 시 빈 문자열 → 기존 LLM 경로 유지.

        주의: 오탐 방지를 위해 질문+컨텍스트 동시 매칭만 수행.
        entities에 department가 있으면 해당 학과 행에서 전화/호실 직접 추출.

        2026-04-11 수정 (버그 #3): 모든 rule 결과를 `_answer_unit_aligns()`로
        최종 검증한다. rule의 느슨한 regex가 잘못된 값을 반환하면 aligns가 거부.
        """
        q = re.sub(r"\s+", "", question.lower())
        entities = entities or {}

        def _checked(candidate: str) -> str:
            """rule이 반환하려는 후보를 aligns()로 검증. 통과만 반환, 실패는 빈 문자열."""
            if candidate and _answer_unit_aligns(question, candidate):
                return candidate
            return ""

        # 2026-04-28: Category-A 룰(1 URL · 2 재수강 최고성적 · 5 졸업 최소학점 ·
        # 8 로그인 시간 · 8-b 수강신청 일정) 제거.
        # 사유: 위 5개는 academic_graph.py 핸들러에 동등 로직이 이미 있고, 거기엔
        # 트러블슈팅 신호어 가드(_has_troubleshoot_signal)도 적용돼 있다. 폴백을
        # 유지하면 그래프 가드를 통째로 우회하므로(2026-04-28 "수강신청 사이트 오류"
        # 회귀 사례) 단일 진실원(graph)만 유지. 카테고리 B/C 룰 (3, 4, 6, 7, 9, 10,
        # 11, 12)은 그래프 핸들러가 없는 영역이라 폴백 가치가 있어 보존.

        # ── 3) 재수강 가능 성적 기준 ──
        # 버그 #3 수정: 질문이 "제한/한도"를 묻는 경우(r05)엔 이 rule을 건너뛴다.
        # "재수강 가능 성적"을 추출하는데 질문이 "재수강 학점 제한"인 상황을 방어.
        _asks_limit_not_ability = any(kw in q for kw in ("제한", "한도", "최대")) and "가능" not in q
        if "재수강" in q and ("가능" in q or ("기준" in q and not _asks_limit_not_ability) or "몇" in q):
            m = re.search(r"재수강기준성적[:\s]*([A-Da-d][+]?이하)", context)
            if m:
                return _checked(f"{m.group(1)} 과목만 재수강 가능합니다.")
            m = re.search(r"([A-Da-d][+]?)\s*이하.*?(?:과목|가능|재수강)", context)
            if m:
                return _checked(f"{m.group(1)} 이하의 과목만 가능합니다.")

        # ── 4) 출석 요건 (분수/비율) ──
        if "출석" in q and any(kw in q for kw in ("요건", "조건", "기준")):
            m = re.search(r"(\d+/\d+)\s*이상", context)
            if m:
                return f"전체 출석일수의 {m.group(1)} 이상을 충족해야 합니다."
            m = re.search(r"출석요건[:\s]*(\d+/\d+)", context)
            if m:
                return f"전체 출석일수의 {m.group(1)} 이상을 충족해야 합니다."

        # ── 6) OCU 개강일/시간 ──
        if "ocu" in q and any(kw in q for kw in ("개강", "시작", "언제")):
            m_date = re.search(r"개강일[:\s]*([\d\-]+)", context)
            m_time = re.search(r"개강시간[:\s]*(오[전후]\s*\d+시)", context)
            if m_date:
                answer = f"OCU 개강일은 {m_date.group(1)}"
                if m_time:
                    answer += f" {m_time.group(1)}"
                return answer + "입니다."

        # ── 7) 마감 시간 ("몇 시", "마감 시간") ──
        if any(kw in q for kw in ("몇시", "마감시간", "마감몇시")):
            m = re.search(r"마감[^:]*?(\d{1,2}:\d{2})", context)
            if not m:
                m = re.search(r"(\d{1,2}:\d{2}).*?마감", context)
            if not m:
                m = re.search(r"시간[:\s]*(\d{1,2}:\d{2})", context)
            if m:
                return f"{m.group(1)}입니다."

        # ── 9) 이론/실습 학점 추출 ──
        if any(kw in q for kw in ("이론", "실습", "이수과목")):
            m = re.search(r"이론\s*(\d+)\s*(?:학점)?\s*실습\s*(\d+)", context)
            if m:
                return f"이론 {m.group(1)}학점, 실습 {m.group(2)}학점입니다."

        # ── 10) 금액 추출 ("수강료", "비용", "금액", "얼마") ──
        if any(kw in q for kw in ("수강료", "비용", "금액", "얼마")) and "초과" not in q:
            m = re.search(r"(\d{1,3}(?:,\d{3})*)\s*원", context)
            if m:
                return f"{m.group(1)}원입니다."

        # ── 11) 초과 수강료 ──
        if "초과" in q and any(kw in q for kw in ("수강료", "비용", "금액")):
            m = re.search(r"초과[^:]*?(\d{1,3}(?:,\d{3})*)\s*원", context)
            if m:
                return f"초과 수강료는 {m.group(1)}원입니다."

        # ── 12) 학과 엔티티 + 연락처/사무실 질문 ──
        # Fix C (2026-04-11): q059/q060처럼 학과명이 명확하고 질문이
        # 연락처/사무실인 경우, 컨텍스트의 해당 학과 행에서 phone/room을 직접 뽑는다.
        # LLM이 표의 다른 학과 행과 혼동해서 1자리 숫자를 틀리는 회귀 방어.
        dept = entities.get("department")
        if dept and any(kw in q for kw in ("전화", "연락처", "번호", "사무실", "어디")):
            from app.pipeline.answer_units import _extract_phone_in_entity_line
            phone = _extract_phone_in_entity_line(context, dept)
            room_match = None
            for line in context.split("\n"):
                if dept not in line:
                    continue
                rm = re.search(r"\b([A-Z]\d{3}(?:-\d+)?)\b", line)
                if rm:
                    room_match = rm.group(1)
                    break
            parts: list[str] = []
            if phone:
                parts.append(f"051-509-{phone}")
            if room_match:
                parts.append(f"사무실 {room_match}")
            if parts:
                return f"{dept}의 " + ", ".join(parts) + "입니다."

        return ""

    @staticmethod
    def _extract_core_tokens(question: str) -> list[str]:
        """질문에서 조사 제거 + stopword 필터한 어근 토큰. FAQ 관련성 판정용.

        공용 ko_tokenizer를 사용해 academic_graph.search_faq와 동일한 기준으로
        매칭 여부를 판정한다 → 두 레이어의 결과가 엇갈리지 않는다.
        """
        from app.pipeline.ko_tokenizer import core_tokens, FAQ_STOPWORDS
        return core_tokens(question, FAQ_STOPWORDS)

    @staticmethod
    def _filter_by_entity(
        results: List[SearchResult], entities: Optional[dict]
    ) -> List[SearchResult]:
        """엔티티 기반 필터: 단일 토픽 쿼리에서 무관 청크를 제거합니다.

        원칙 2: 검색 정밀도 향상을 위한 동적 필터링.
        최소 1개 결과는 항상 보장합니다.
        """
        if not entities or not results:
            return results

        # OCU 토픽 필터 (정방향): OCU 쿼리 → OCU 청크만 유지
        if entities.get("ocu"):
            _OCU_KW = ("ocu", "열린사이버", "컨소시엄", "cons.ocu")
            filtered = [
                r for r in results
                if r.metadata.get("source_type") == "graph"
                or any(kw in r.text.lower() for kw in _OCU_KW)
            ]
            if filtered:
                return filtered

        # OCU 역방향 필터: OCU 미언급 시 OCU 전용 청크를 제거
        # 혼합 청크(본교+OCU 같은 페이지)는 유지 — LLM 프롬프트에서 OCU 무시 지시
        if not entities.get("ocu"):
            _OCU_PRIMARY_KW = ("열린사이버", "cons.ocu", "컨소시엄")
            non_ocu = []
            ocu_pure = []
            for r in results:
                txt_lower = (r.text or "").lower()
                if r.metadata.get("source_type") == "graph":
                    non_ocu.append(r)
                # OCU가 주제인 청크만 제거 (제목/첫 100자에 OCU 핵심어)
                elif any(kw in txt_lower[:200] for kw in _OCU_PRIMARY_KW):
                    ocu_pure.append(r)
                else:
                    non_ocu.append(r)
            if non_ocu:
                return non_ocu

        return results

    @staticmethod
    def _strip_ocu_section(text: str) -> str:
        """혼합 청크에서 OCU 섹션을 동적으로 트리밍합니다.

        원칙 2: PDF 페이지에 본교+OCU 내용이 혼합된 경우,
        OCU 섹션 시작 지점 이후 텍스트를 제거하여 본교 내용만 남김.

        경계 패턴:
        - "기타사항 안내" + "OCU" (p.23 구조)
        - "상대평가 기준" (OCU 고유 — 본교는 2023-2부터 절대평가)
        - "OCU 개설 교과목" / "OCU 홈페이지"
        - "CS강의" / "On-line강의" (OCU 섹션 제목)
        """
        if not text:
            return text

        # OCU 섹션 시작점을 찾아서 그 이전까지만 남김
        _OCU_SECTION_MARKERS = (
            "기타사항 안내",
            "상대평가 기준",
            "나. 상대평가",
            "OCU 개설 교과목",
            "OCU 홈페이지",
            "CS강의",
            "On-line강의",
        )

        earliest_pos = len(text)
        for marker in _OCU_SECTION_MARKERS:
            pos = text.find(marker)
            if pos != -1 and pos < earliest_pos:
                earliest_pos = pos

        if earliest_pos < len(text):
            trimmed = text[:earliest_pos].rstrip()
            # 최소 100자 이상 남아야 의미 있는 컨텍스트
            if len(trimmed) >= 100:
                return trimmed

        return text

    # Phase C (2026-04-16): 조건·예외·한정 키워드가 포함된 줄은 무조건 보존.
    # 이전 구현은 "2022학번 21학점 / 2023학번 18학점 / 단 평점 4.0 이상 24학점까지"
    # 에서 예외 조항("단", "이상", "이하")을 유실시켜 다조건 질문 정답률 하락을 유발.
    _EVIDENCE_ALWAYS_KEEP_PATTERNS = re.compile(
        r"(단,|다만,|단\s|예외|제외|이상|이하|초과|미만|까지|이내|부터|포함|불포함|"
        r"범위|예시|조건|기준|경우|제한|적용)"
    )

    @classmethod
    def _slice_evidence_text(
        cls,
        text: str,
        question: str,
        entities: Optional[dict] = None,
        question_type: Optional[QuestionType] = None,
    ) -> str:
        """
        질문과 직접 맞닿는 원문 줄만 유지해 프롬프트 길이를 줄입니다.

        Phase C (2026-04-16) 회귀 수정:
        - min_text_len·min_sliced_len·context_lines 를 settings.pipeline 환경변수로 분리 (하드코딩 금지)
        - 조건·예외·한정 키워드("단,", "다만,", "이상", "이하", "제외", ...) 포함 줄은 무조건 보존
        - 주변 ±context_lines 줄로 컨텍스트 폭 확장 (기본 ±2)
        """
        from app.config import settings as _settings
        _cfg = _settings.pipeline
        min_text_len = _cfg.evidence_slicing_min_text_len
        min_sliced_len = _cfg.evidence_slicing_min_sliced_len
        ctx_n = max(1, _cfg.evidence_slicing_context_lines)

        if not text or not question or len(text) < min_text_len:
            return text

        lines = [line.rstrip() for line in text.splitlines()]
        if len(lines) < 5:
            return text

        entities = entities or {}
        question_focus = entities.get("question_focus")
        tokens = {
            token.lower()
            for token in cls._extract_core_tokens(question)
            if len(token) >= 2
        }

        for value in entities.values():
            if not isinstance(value, str):
                continue
            tokens.update(
                token.lower()
                for token in cls._extract_core_tokens(value)
                if len(token) >= 2
            )

        if not tokens:
            return text

        keep_indexes: set[int] = set()

        # Pass 1: 조건·예외 키워드 포함 줄은 무조건 보존 (+주변)
        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue
            if cls._EVIDENCE_ALWAYS_KEEP_PATTERNS.search(line):
                for near_idx in range(max(0, idx - ctx_n), min(len(lines), idx + ctx_n + 1)):
                    keep_indexes.add(near_idx)

        # Pass 2: 질문 토큰과 매칭되는 줄 (+주변 ±ctx_n)
        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue
            if cls._line_matches_question(
                line=line,
                tokens=tokens,
                question_focus=question_focus,
                question_type=question_type,
            ):
                for near_idx in range(max(0, idx - ctx_n), min(len(lines), idx + ctx_n + 1)):
                    keep_indexes.add(near_idx)

        if not keep_indexes:
            return text

        sliced = "\n".join(
            lines[idx] for idx in sorted(keep_indexes)
            if lines[idx].strip()
        ).strip()
        # 너무 짧거나 (정보 손실), 너무 많이 남으면 (슬라이싱 효과 없음) 원본 유지
        if len(sliced) < min_sliced_len or len(sliced) >= len(text) * 0.9:
            return text
        return sliced

    @classmethod
    def _line_matches_question(
        cls,
        *,
        line: str,
        tokens: set[str],
        question_focus: Optional[str] = None,
        question_type: Optional[QuestionType] = None,
    ) -> bool:
        normalized = line.lower()
        token_hits = sum(1 for token in tokens if token in normalized)
        focus_match = cls._line_matches_focus(normalized, question_focus)

        if token_hits >= 2:
            return True
        if token_hits >= 1 and focus_match:
            return True
        if question_type == QuestionType.PROCEDURAL and token_hits >= 1:
            return True
        return False

    @staticmethod
    def _line_matches_focus(line: str, question_focus: Optional[str]) -> bool:
        if not question_focus:
            return False

        focus_patterns = {
            "period": (
                r"\d{1,2}\.\s*\d{1,2}",
                r"\d{4}-\d{2}-\d{2}",
                r"\d{1,2}:\d{2}",
                r"\([월화수목금토일]\)",
            ),
            "limit": (r"\d{1,3}(?:,\d{3})*", r"학점", r"회", r"명", r"원"),
            "table_lookup": (r"\d{1,3}(?:,\d{3})*", r"\|", r"학점", r"시간", r"금액"),
            "rule_list": (r"대상", r"조건", r"기준", r"요건", r"제한", r"가능"),
            "method": (r"신청", r"로그인", r"접속", r"제출", r"확인", r"사이트", r"절차"),
            "location": (
                r"https?://",
                r"\b[A-Z]\d{3}(?:-\d+)?\b",
                r"호",
                r"층",
                r"센터",
                r"사무실",
            ),
            "eligibility": (r"대상", r"자격", r"가능", r"불가", r"이상", r"이하"),
        }

        patterns = focus_patterns.get(question_focus)
        if not patterns:
            return False
        return any(re.search(pattern, line) for pattern in patterns)

    @staticmethod
    def _collect_source_urls(results: list) -> list:
        """
        검색 결과에서 공지사항 출처 URL을 수집합니다.

        doc_type 이 "notice" 또는 "notice_attachment" 인 결과만 대상으로 하며
        동일 URL의 중복 항목은 제거합니다.

        Returns:
            [{"title": "공지 제목", "url": "https://..."}, ...]
        """
        _NOTICE_TYPES = {"notice", "notice_attachment"}
        seen: set = set()
        urls: list = []

        for result in results:
            meta = result.metadata or {}
            doc_type = meta.get("doc_type", "")
            url = meta.get("source_url", "")

            if doc_type not in _NOTICE_TYPES:
                continue
            if not url or not url.startswith("http"):
                continue
            if url in seen:
                continue

            seen.add(url)
            urls.append({
                "title": meta.get("title", "") or url,
                "url": url,
            })

        return urls

    @staticmethod
    def _format_result(result: SearchResult, text: str = None) -> str:
        """검색 결과를 포맷팅합니다.

        섹션 경로가 있으면 "p.47 | 섹션경로" 형식으로 병기 —
        LLM이 답변에 출처를 인용할 수 있도록.
        """
        text = text or result.text
        source_info = ""

        doc_type = result.metadata.get("doc_type", "")
        source_url = result.metadata.get("source_url", "")
        section_path = result.metadata.get("section_path", "")

        if doc_type in ("notice", "notice_attachment") and source_url:
            # 공지사항: URL을 출처로 표시하여 LLM이 참조 가능하게 함
            source_info = f" [{source_url}]"
        elif result.page_number:
            parts = [f"p.{result.page_number}"]
            if section_path:
                parts.append(section_path)
            source_info = f" [{' | '.join(parts)}]"
        elif result.source:
            source_info = f" [{result.source}]"

        return f"---{source_info}\n{text}"
