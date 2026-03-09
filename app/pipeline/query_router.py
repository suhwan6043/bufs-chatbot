"""
쿼리 라우터 - Vector/Graph 검색 경로를 결정합니다.
CPU 전용, <1ms 처리
"""

import logging
from typing import List, Optional

from app.config import settings
from app.models import QueryAnalysis, SearchResult, Intent
from app.vectordb import ChromaStore
from app.graphdb import AcademicGraph

logger = logging.getLogger(__name__)


class QueryRouter:
    """
    [역할] 분석된 쿼리를 적절한 검색 엔진으로 라우팅
    [로직] QueryAnalysis의 requires_vector/requires_graph 플래그 + entities 기반
    [리랭킹] 벡터 검색 결과를 bge-reranker-v2-m3으로 재순위화
    """

    def __init__(
        self,
        chroma_store: ChromaStore = None,
        academic_graph: AcademicGraph = None,
        reranker=None,
    ):
        self.chroma_store = chroma_store
        self.academic_graph = academic_graph
        self._reranker = reranker

    @property
    def reranker(self):
        if self._reranker is None and settings.reranker.enabled:
            try:
                from app.pipeline.reranker import Reranker
                self._reranker = Reranker()
            except Exception as e:
                logger.warning(f"리랭커 초기화 실패, 비활성화: {e}")
                self._reranker = False  # 실패 시 재시도 방지
        return self._reranker if self._reranker else None

    def route_and_search(
        self, query: str, analysis: QueryAnalysis
    ) -> dict:
        results = {
            "vector_results": [],
            "graph_results": [],
        }

        if analysis.requires_vector and self.chroma_store:
            results["vector_results"] = self._search_vector(query, analysis)

        if analysis.requires_graph and self.academic_graph:
            results["graph_results"] = self._search_graph(query, analysis)

        logger.info(
            "라우팅: intent=%s, vector=%d, graph=%d",
            analysis.intent.value,
            len(results["vector_results"]),
            len(results["graph_results"]),
        )
        return results

    def _search_vector(
        self, query: str, analysis: QueryAnalysis
    ) -> List[SearchResult]:
        # 리랭커 사용 시 더 많은 후보 가져오기
        n_candidates = (
            settings.reranker.candidate_k
            if settings.reranker.enabled
            else settings.chroma.n_results
        )

        candidates = self.chroma_store.search(
            query=query,
            n_results=n_candidates,
            student_id=analysis.student_id,
        )

        reranker = self.reranker
        if reranker and candidates:
            return reranker.rerank(
                query=query,
                results=candidates,
                top_k=settings.reranker.top_k,
            )

        return candidates

    def _search_graph(
        self, query: str, analysis: QueryAnalysis
    ) -> List[SearchResult]:
        """
        NetworkX 그래프에서 의도 + 엔티티 기반 탐색.
        SCHEDULE, ALTERNATIVE는 student_id 없어도 동작.
        """
        no_id_intents = (Intent.SCHEDULE, Intent.ALTERNATIVE, Intent.REGISTRATION)

        if analysis.intent not in no_id_intents and not analysis.student_id:
            # 특정 엔티티가 있으면 기본 student_id로 그래프 탐색 허용
            has_focused_entity = bool(
                analysis.entities.get("graduation_cert")
                or analysis.entities.get("major_method")
            )
            if not has_focused_entity:
                logger.debug("student_id 없음 - 그래프 탐색 스킵")
                return []

        return self.academic_graph.query_to_search_results(
            student_id=analysis.student_id or "2023",
            intent=analysis.intent.value,
            entities=analysis.entities,
            student_type=analysis.student_type or "내국인",
            question=query,
        )
