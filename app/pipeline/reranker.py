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

        pairs = [[query, r.text] for r in results]
        scores = self.model.predict(pairs)

        scored = sorted(
            zip(scores, results),
            key=lambda x: x[0],
            reverse=True,
        )

        # 동적 컷오프: 최고 점수 대비 50% 미만인 결과 제거
        # 원칙 2: 무관한 청크의 컨텍스트 오염 방지 → 검색 정밀도 향상
        reranked = []
        top_score = scored[0][0] if scored else 0.0
        threshold = top_score * 0.5 if top_score > 0 else -float("inf")

        for score, result in scored[:top_k]:
            if score < threshold and len(reranked) >= 2:
                # 최소 2개는 유지, 이후 threshold 미달 시 중단
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
