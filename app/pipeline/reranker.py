"""
리랭커 - 초기 검색 결과를 Cross-Encoder로 재순위화합니다.
BAAI/bge-reranker-v2-m3 사용 (CPU 전용)
"""

import logging
from typing import List

from app.config import settings
from app.models import SearchResult

logger = logging.getLogger(__name__)


class Reranker:
    """
    [역할] Vector 검색 후보를 Cross-Encoder로 재순위화
    [모델] BAAI/bge-reranker-v2-m3
    [방식] (query, passage) 쌍을 입력받아 관련도 점수 산출
    """

    def __init__(self):
        self._model = None

    @property
    def model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                logger.info(
                    f"리랭커 모델 로드: {settings.reranker.model_name} "
                    f"(device={settings.reranker.device})"
                )
                self._model = CrossEncoder(
                    settings.reranker.model_name,
                    device=settings.reranker.device,
                )
            except Exception as e:
                logger.error(f"리랭커 로드 실패: {e}")
                raise
        return self._model

    def rerank(
        self,
        query: str,
        results: List[SearchResult],
        top_k: int = None,
    ) -> List[SearchResult]:
        """
        검색 결과를 query와의 관련도 기준으로 재순위화합니다.

        Args:
            query: 사용자 쿼리
            results: 초기 검색 결과 리스트
            top_k: 반환할 상위 결과 수 (None이면 settings.reranker.top_k)

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
        # Tier 2 (faq, 고정공지) = FAQ + 고정(📌) 공지 → 동등 경쟁
        # Tier 3 (기타) = 일반 공지, 장학, timetable 등 → boost 없음
        _TIER1_DOC_TYPES = frozenset({"domestic", "guide"})
        top_raw = max(raw_scores) if len(raw_scores) else 0.0
        tier1_bonus = abs(top_raw) * 0.20 if top_raw != 0 else 0.0
        tier2_bonus = abs(top_raw) * 0.10 if top_raw != 0 else 0.0
        boosted_scored = []
        for raw, (_, r) in zip(raw_scores, valid):
            s = float(raw)
            dt = r.metadata.get("doc_type", "")
            if dt in _TIER1_DOC_TYPES:
                s += tier1_bonus
            elif dt == "faq":
                s += tier2_bonus
            elif dt == "notice" and r.metadata.get("is_pinned"):
                s += tier2_bonus
            boosted_scored.append((s, r))

        scored = sorted(boosted_scored, key=lambda x: x[0], reverse=True)

        # 동적 컷오프: 최고 점수 대비 50% 미만인 결과 제거
        # 원칙 2: 무관한 청크의 컨텍스트 오염 방지 → 검색 정밀도 향상
        # 0.35 → 0.5 상향: 크롤링 확장 후 노이즈 청크가 threshold 아래로 대량 통과하던 문제 해결
        reranked = []
        top_score = scored[0][0] if scored else 0.0
        threshold = top_score * 0.5 if top_score > 0 else -float("inf")

        for score, result in scored[:top_k]:
            if score < threshold and len(reranked) >= 3:
                # 최소 3개는 유지, 이후 threshold 미달 시 중단
                break
            result.score = float(score)
            reranked.append(result)

        logger.debug(
            "리랭킹: %d개 후보 → %d개 선택 (top=%.3f, threshold=%.3f)",
            len(results),
            len(reranked),
            top_score,
            threshold,
        )
        return reranked
