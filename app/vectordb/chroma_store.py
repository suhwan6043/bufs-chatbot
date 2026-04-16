"""
ChromaDB 벡터 저장소
로컬 파일 기반 벡터 DB로 문서 청크를 저장/검색합니다.
"""

import logging
from typing import List, Optional, Union

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
                "student_types": getattr(c, "student_types", ""),  # 빈 문자열 = 전체 허용
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
        doc_type: Optional[Union[str, List[str]]] = None,
        semester: Optional[str] = None,
        department: Optional[str] = None,
        student_type: Optional[str] = None,
        query_embedding: list = None,
    ) -> List[SearchResult]:
        """쿼리에 대해 유사 문서를 검색합니다.

        원칙 2: query_embedding을 외부에서 전달하면 embed_query() 호출을 건너뛰어
        동일 쿼리의 반복 임베딩을 방지합니다 (Phase 1/2/2.5 공유).
        """
        n_results = n_results or settings.chroma.n_results
        if query_embedding is None:
            query_embedding = self.embedder.embed_query(query).tolist()
        elif hasattr(query_embedding, 'tolist'):
            query_embedding = query_embedding.tolist()

        # 주의: ChromaDB는 메타데이터 `where`에서 $contains 를 지원하지 않는다.
        # → student_type 은 _build_filter() 가 아닌 Python 후처리로 분기.
        where_filter = self._build_filter(student_id, doc_type, semester, department)

        # student_type 필터가 활성화되어 있으면 후처리 드롭으로 n_results 가 줄어들 수 있으므로
        # 넉넉히 가져와서 잘라낸다.
        stype_filter_active = bool(
            student_type and settings.admin_faq.student_type_filter_enabled
        )
        fetch_n = n_results * 2 if stype_filter_active else n_results

        kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": fetch_n,
        }
        if where_filter:
            kwargs["where"] = where_filter

        try:
            results = self.collection.query(**kwargs)
        except Exception as e:
            if where_filter and "Error finding id" in str(e):
                logger.warning("ChromaDB 필터 쿼리 실패 (InternalError), 필터 없이 재시도: %s", e)
                fallback_kwargs = {
                    "query_embeddings": [query_embedding],
                    "n_results": fetch_n,
                }
                results = self.collection.query(**fallback_kwargs)
            else:
                raise

        search_results = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = (results["metadatas"][0][i] or {}) if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 0.0
                score = 1.0 - distance  # 코사인 거리 -> 유사도

                # student_type 후처리 필터 — 빈 student_types 는 전체 허용
                if stype_filter_active:
                    allowed = (metadata.get("student_types") or "")
                    if allowed and student_type not in allowed.split("|"):
                        continue

                search_results.append(SearchResult(
                    text=doc,
                    score=score,
                    source=metadata.get("source_file", ""),
                    page_number=int(metadata.get("page_number", 0)),
                    metadata=metadata,
                ))

        # 후처리 드롭 고려: 원래 n_results 만큼으로 잘라냄
        return search_results[:n_results]

    @staticmethod
    def _build_filter(
        student_id: Optional[str],
        doc_type: Optional[Union[str, List[str]]] = None,
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
            if isinstance(doc_type, list):
                conditions.append({"doc_type": {"$in": doc_type}})
            else:
                conditions.append({"doc_type": doc_type})
        if semester:
            # 해당 학기 청크 OR 전 학기 공통("") 청크 모두 포함
            conditions.append({"$or": [{"semester": semester}, {"semester": ""}]})
        if department:
            # department 메타데이터가 있는 청크(수업시간표)만 필터링
            conditions.append({"department": {"$eq": department}})
        # 주의: student_type 필터는 ChromaDB metadata where 에서 $contains 미지원으로
        # chroma_store.search() 의 Python 후처리로 이동했다.

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def delete_by_source(self, source_identifier: str) -> int:
        """
        source_file 또는 source_url 메타데이터가 일치하는 청크를 모두 삭제합니다.

        PDF 경로(source_file)와 웹 URL(metadata.source_url) 두 가지를 모두 지원합니다.
        Returns:
            삭제된 청크 수
        """
        if not source_identifier:
            return 0

        matched_ids: list[str] = []

        # 1차: source_file 필드로 검색 (PDF 청크)
        try:
            result = self.collection.get(
                where={"source_file": {"$eq": source_identifier}},
                include=[],
            )
            if result and result.get("ids"):
                matched_ids.extend(result["ids"])
        except Exception as e:
            logger.warning("source_file 필터 검색 실패: %s", e)

        # 2차: source_url 메타데이터로 검색 (웹 크롤링 청크)
        if not matched_ids:
            try:
                result = self.collection.get(
                    where={"source_url": {"$eq": source_identifier}},
                    include=[],
                )
                if result and result.get("ids"):
                    matched_ids.extend(result["ids"])
            except Exception as e:
                logger.warning("source_url 필터 검색 실패: %s", e)

        if not matched_ids:
            logger.info("삭제할 청크 없음: %s", source_identifier)
            return 0

        # 배치 삭제 (500개씩)
        batch_size = 500
        deleted = 0
        for i in range(0, len(matched_ids), batch_size):
            batch = matched_ids[i : i + batch_size]
            self.collection.delete(ids=batch)
            deleted += len(batch)

        logger.info("ChromaDB에서 %d개 청크 삭제 완료: %s", deleted, source_identifier)
        return deleted

    def delete_all_by_doc_type(self, doc_type: str) -> int:
        """
        특정 doc_type의 모든 청크를 ChromaDB에서 삭제합니다.

        Returns:
            삭제된 청크 수
        """
        try:
            result = self.collection.get(
                where={"doc_type": {"$eq": doc_type}},
                include=[],
            )
            ids = result.get("ids", []) if result else []
        except Exception as e:
            logger.warning("doc_type=%s 조회 실패: %s", doc_type, e)
            return 0

        if not ids:
            return 0

        batch_size = 500
        deleted = 0
        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            self.collection.delete(ids=batch)
            deleted += len(batch)

        logger.info("doc_type=%s 청크 %d개 삭제 완료", doc_type, deleted)
        return deleted

    def count(self) -> int:
        """저장된 문서 수를 반환합니다."""
        return self.collection.count()
