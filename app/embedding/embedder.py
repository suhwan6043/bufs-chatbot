"""
임베딩 모델 래퍼
BAAI/bge-m3를 CPU에서 실행하여 텍스트를 벡터로 변환합니다.
bge-m3는 query/passage prefix가 필요 없습니다.
"""

import logging
from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import settings

logger = logging.getLogger(__name__)


class Embedder:
    """
    [역할] 텍스트를 벡터 임베딩으로 변환
    [모델] BAAI/bge-m3 (CPU 전용, multilingual)
    [주의] bge-m3는 query/passage prefix 불필요
    """

    def __init__(self):
        self._model = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            logger.info(
                f"임베딩 모델 로드: {settings.embedding.model_name} "
                f"(device={settings.embedding.device})"
            )
            self._model = SentenceTransformer(
                settings.embedding.model_name,
                device=settings.embedding.device,
            )
        return self._model

    def embed_query(self, text: str) -> np.ndarray:
        """쿼리 텍스트를 임베딩합니다."""
        return self.model.encode(text, normalize_embeddings=True)

    def embed_passage(self, text: str) -> np.ndarray:
        """문서 텍스트를 임베딩합니다."""
        return self.model.encode(text, normalize_embeddings=True)

    def embed_passages_batch(
        self, texts: List[str], batch_size: int = 32
    ) -> List[np.ndarray]:
        """여러 문서 텍스트를 배치로 임베딩합니다."""
        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=True,
            normalize_embeddings=True,
        )
        return [emb for emb in embeddings]

    @property
    def dimension(self) -> int:
        """임베딩 벡터 차원 수를 반환합니다."""
        return self.model.get_sentence_embedding_dimension()
