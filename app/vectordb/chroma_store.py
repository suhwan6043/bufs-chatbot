"""
ChromaDB 벡터 저장소
로컬 파일 기반 벡터 DB로 문서 청크를 저장/검색합니다.
"""

import logging
from typing import List, Optional

import chromadb

from app.config import settings
from app.models import Chunk, SearchResult
from app.embedding import Embedder

logger = logging.getLogger(__name__)


class ChromaStore:
    """
    [역할] 벡터 임베딩 저장 및 유사도 검색
    [백엔드] ChromaDB (SQLite 기반, 로컬 파일)
    [기능] 메타데이터 필터링 지원 (학번, 문서 유형 등)
    """

    def __init__(self, embedder: Embedder = None):
        self.embedder = embedder or Embedder()
        self._client = None
        self._collection = None

    @property
    def client(self) -> chromadb.ClientAPI:
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=settings.chroma.persist_dir
            )
        return self._client

    @property
    def collection(self) -> chromadb.Collection:
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=settings.chroma.collection_name,
                metadata={"hnsw:space": settings.chroma.distance_metric},
            )
        return self._collection

    def add_chunks(self, chunks: List[Chunk]) -> None:
        """청크 리스트를 벡터 DB에 추가합니다."""
        if not chunks:
            return

        texts = [c.text for c in chunks]
        ids = [c.chunk_id for c in chunks]
        metadatas = [
            {
                "page_number": c.page_number,
                "source_file": c.source_file,
                "student_id": c.student_id or "",
                "doc_type": c.doc_type,
                "cohort_from": c.cohort_from,
                "cohort_to": c.cohort_to,
                "semester": c.semester,
                **{k: str(v) for k, v in c.metadata.items()},
            }
            for c in chunks
        ]

        embeddings = self.embedder.embed_passages_batch(texts)
        embedding_lists = [emb.tolist() for emb in embeddings]

        # ChromaDB는 한 번에 최대 ~5000개까지 추가 가능
        batch_size = 500
        for i in range(0, len(ids), batch_size):
            end = i + batch_size
            self.collection.upsert(
                ids=ids[i:end],
                embeddings=embedding_lists[i:end],
                documents=texts[i:end],
                metadatas=metadatas[i:end],
            )

        logger.info(f"ChromaDB에 {len(chunks)}개 청크 추가 완료")

    def search(
        self,
        query: str,
        n_results: int = None,
        student_id: Optional[str] = None,
        doc_type: Optional[str] = None,
        semester: Optional[str] = None,
        department: Optional[str] = None,
    ) -> List[SearchResult]:
        """쿼리에 대해 유사 문서를 검색합니다."""
        n_results = n_results or settings.chroma.n_results
        query_embedding = self.embedder.embed_query(query).tolist()

        where_filter = self._build_filter(student_id, doc_type, semester, department)

        kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
        }
        if where_filter:
            kwargs["where"] = where_filter

        results = self.collection.query(**kwargs)

        search_results = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 0.0
                score = 1.0 - distance  # 코사인 거리 -> 유사도

                search_results.append(SearchResult(
                    text=doc,
                    score=score,
                    source=metadata.get("source_file", ""),
                    page_number=int(metadata.get("page_number", 0)),
                    metadata=metadata,
                ))

        return search_results

    @staticmethod
    def _build_filter(
        student_id: Optional[str],
        doc_type: Optional[str],
        semester: Optional[str] = None,
        department: Optional[str] = None,
    ) -> Optional[dict]:
        """
        메타데이터 필터를 구성합니다.

        student_id (학번 연도 문자열, 예: "2024"):
            exact match 대신 cohort 범위 필터를 사용합니다.
            cohort_from <= year <= cohort_to 인 청크만 반환.
        semester (예: "2026-1"):
            exact match. 빈 문자열("")은 전 학기 공통 청크를 뜻하므로
            semester 필터가 지정된 경우 해당 학기 OR 공통("") 청크를 반환합니다.
        department (예: "소프트웨어"):
            수업시간표 청크의 department 메타데이터와 exact match.
            COURSE_INFO intent에서만 적용되며, 다른 학과 데이터 혼입을 방지합니다.
        """
        conditions = []
        if student_id:
            try:
                year = int(student_id)
                conditions.append({"cohort_from": {"$lte": year}})
                conditions.append({"cohort_to":   {"$gte": year}})
            except (ValueError, TypeError):
                logger.warning("student_id '%s'를 정수로 변환할 수 없어 코호트 필터를 건너뜁니다.", student_id)
        if doc_type:
            conditions.append({"doc_type": doc_type})
        if semester:
            # 해당 학기 청크 OR 전 학기 공통("") 청크 모두 포함
            conditions.append({"$or": [{"semester": semester}, {"semester": ""}]})
        if department:
            # department 메타데이터가 있는 청크(수업시간표)만 필터링
            conditions.append({"department": {"$eq": department}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def count(self) -> int:
        """저장된 문서 수를 반환합니다."""
        return self.collection.count()
