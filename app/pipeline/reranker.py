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

        # FAQ boost: FAQ 청크는 단문·핵심 답을 담고 있어 cross-encoder가 과소평가하기 쉬움
        # → 동적 가중치(top_score 비례)로 보정하여 범용 가이드 청크에 밀리지 않도록 함
        top_raw = max(raw_scores) if len(raw_scores) else 0.0
        faq_bonus = abs(top_raw) * 0.15 if top_raw != 0 else 0.0
        boosted_scored = []
        for raw, (_, r) in zip(raw_scores, valid):
            s = float(raw)
            if r.metadata.get("doc_type") == "faq":
                s += faq_bonus
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
