"""
BM25 sparse 검색 인덱스 — 한국어 최적화.

ChromaDB의 dense 검색(BGE-m3 코사인 유사도)을 보완하는 키워드 기반 검색.
Reranker(Cross-Encoder) 전 후보 풀을 확장해 정확한 키워드 매칭을 놓치지 않도록 한다.

핵심 설계:
  - corpus = ChromaDB 청크 그대로 (chunk 단위 통일 → fusion 의미 보장)
  - tokenizer = ko_tokenizer.tokenize_for_bm25 (조사 제거 + 불용어 필터)
  - 쿼리/문서 양쪽 동일 토큰화 → BM25 정확도 극대화

원칙 1(스키마 진화): ChromaDB 메타데이터(doc_type, cohort 등)를 그대로 활용.
원칙 2(비용·지연): 인메모리 인덱스, 검색 ~2ms (1,200 청크).
원칙 3(증분 업데이트): ChromaDB에서 전량 로드 → 재빌드. build() 재호출로 동기화.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Union

import numpy as np

from app.models import SearchResult

logger = logging.getLogger(__name__)


class BM25Index:
    """ChromaDB 청크 기반 BM25 인메모리 인덱스.

    corpus = ChromaDB 청크 (dense 검색과 동일 단위)
    tokenizer = ko_tokenizer.tokenize_for_bm25 (조사 제거 + 불용어 필터)
    """

    def __init__(self, chroma_store) -> None:
        self._store = chroma_store
        self._corpus: list[str] = []
        self._ids: list[str] = []
        self._metadatas: list[dict] = []
        self._bm25 = None
        self._built = False

    def build(self) -> None:
        """ChromaDB 전체 청크를 로드해 BM25Okapi 인덱스를 구축한다.

        ~1,200 청크 기준 <1초. Streamlit 시작 시 또는 ingest 후 1회 실행.
        토큰화: 조사 제거 + 불용어 필터 (한국어 BM25 성능의 핵심).
        """
        try:
            all_data = self._store.collection.get(
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.warning("BM25 인덱스 빌드 실패 (ChromaDB 접근 오류): %s", e)
            return

        self._ids = all_data.get("ids", [])
        self._corpus = all_data.get("documents", [])
        self._metadatas = all_data.get("metadatas", [])

        if not self._corpus:
            logger.warning("BM25 인덱스 빌드: ChromaDB에 문서가 없습니다.")
            return

        # 한국어 최적화 토큰화: 조사 제거 + 불용어 필터 + 2글자 이상
        from app.pipeline.ko_tokenizer import tokenize_for_bm25

        # A-2: section_path가 있으면 prefix해 헤더 토큰까지 BM25 매칭 대상에 포함.
        # raw text(self._corpus)는 그대로 — 토큰화 단계에서만 결합.
        def _enrich(doc: str, meta: dict) -> str:
            sec = (meta or {}).get("section_path") or ""
            return f"{sec} {doc or ''}" if sec else (doc or "")
        tokenized = [
            tokenize_for_bm25(_enrich(doc, meta))
            for doc, meta in zip(self._corpus, self._metadatas)
        ]

        # 빈 토큰 리스트 방어 (BM25Okapi는 빈 문서에 대해 0-division 가능)
        for i, tokens in enumerate(tokenized):
            if not tokens:
                tokenized[i] = ["_empty_"]

        from rank_bm25 import BM25Okapi

        self._bm25 = BM25Okapi(tokenized)
        self._built = True
        logger.info("BM25 인덱스 빌드 완료: %d 문서", len(self._corpus))

    def search(
        self,
        query: str,
        n_results: int = 20,
        doc_type: Optional[Union[str, List[str]]] = None,
    ) -> List[SearchResult]:
        """BM25 키워드 검색.

        쿼리도 동일한 tokenize_for_bm25로 토큰화해 조사 제거 + 불용어 필터 적용.
        doc_type 필터로 검색 대상을 제한할 수 있다.

        Args:
            query: 검색 질의
            n_results: 반환할 최대 결과 수
            doc_type: 필터할 문서 타입 (str 또는 list[str])

        Returns:
            BM25 스코어 내림차순 SearchResult 리스트
        """
        if not self._built or self._bm25 is None:
            return []

        from app.pipeline.ko_tokenizer import tokenize_for_bm25

        q_tokens = tokenize_for_bm25(query)
        if not q_tokens:
            return []

        scores = self._bm25.get_scores(q_tokens)

        # doc_type 필터: 해당 타입이 아닌 문서의 스코어를 -1로
        if doc_type:
            if isinstance(doc_type, str):
                doc_type = [doc_type]
            dt_set = set(doc_type)
            for i, meta in enumerate(self._metadatas):
                if meta.get("doc_type") not in dt_set:
                    scores[i] = -1.0

        # 상위 n_results 추출
        top_indices = np.argsort(scores)[::-1][:n_results]

        results: List[SearchResult] = []
        for idx in top_indices:
            idx = int(idx)
            if scores[idx] <= 0:
                break
            meta = self._metadatas[idx] if idx < len(self._metadatas) else {}
            results.append(
                SearchResult(
                    text=self._corpus[idx],
                    score=float(scores[idx]),
                    source=meta.get("source_file", ""),
                    page_number=int(meta.get("page_number", 0)),
                    metadata=dict(meta),
                )
            )
        return results

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def doc_count(self) -> int:
        return len(self._corpus) if self._built else 0
