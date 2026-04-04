"""
쿼리 라우터 - Vector/Graph 검색 경로를 결정합니다.
CPU 전용, <1ms 처리
"""

import logging
from typing import List

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

        # 원칙 2: 그래프 빈 결과 → 벡터 폴백 (비용 최적화)
        if not results["graph_results"] and not results["vector_results"] and self.chroma_store:
            logger.info("그래프 결과 없음 → 벡터 폴백 활성화")
            results["vector_results"] = self._search_vector(query, analysis)

        logger.info(
            "라우팅: intent=%s, vector=%d, graph=%d",
            analysis.intent.value,
            len(results["vector_results"]),
            len(results["graph_results"]),
        )
        return results

    # ── 원칙 2: 인텐트별 벡터 검색 후보 수 (동적 k 선택) ──
    _INTENT_K = {
        Intent.SCHEDULE:       5,    # 그래프가 처리, 벡터 최소
        Intent.ALTERNATIVE:    5,
        Intent.GRADUATION_REQ: 10,
        Intent.REGISTRATION:   12,
        Intent.EARLY_GRADUATION: 8,
        Intent.SCHOLARSHIP:    12,
        Intent.LEAVE_OF_ABSENCE: 12,
        Intent.COURSE_INFO:    20,   # 시간표 청크 다수 필요
        Intent.MAJOR_CHANGE:   10,
        Intent.GENERAL:        15,
    }

    # ── 원칙 2: 인텐트별 우선 doc_type (공지 노이즈 차단) ──
    _INTENT_DOC_TYPES = {
        Intent.GRADUATION_REQ:    ["domestic"],
        Intent.REGISTRATION:      ["domestic"],
        Intent.SCHEDULE:          ["domestic"],
        Intent.MAJOR_CHANGE:      ["domestic"],
        Intent.EARLY_GRADUATION:  ["domestic"],
        Intent.LEAVE_OF_ABSENCE:  ["domestic"],
        Intent.COURSE_INFO:       ["domestic", "timetable"],
        Intent.SCHOLARSHIP:       ["domestic", "scholarship"],
        Intent.ALTERNATIVE:       ["domestic"],
        Intent.GENERAL:           None,  # 전체 검색
    }

    _MIN_PHASE1_RESULTS = 3  # Phase 1 최소 결과 수 (미달 시 Phase 2 확장)

    def _search_vector(
        self, query: str, analysis: QueryAnalysis
    ) -> List[SearchResult]:
        # 원칙 2: 인텐트별 검색 후보 수 동적 조정
        intent_k = self._INTENT_K.get(analysis.intent, settings.chroma.n_results)

        # 단일 토픽 쿼리(OCU 등)는 후보 수를 줄여 노이즈 억제
        if analysis.entities.get("ocu"):
            intent_k = min(intent_k, 6)

        n_candidates = (
            max(intent_k, settings.reranker.candidate_k)
            if settings.reranker.enabled
            else intent_k
        )

        # COURSE_INFO + department: 수업시간표 전용 필터 적용
        department = None
        if analysis.intent == Intent.COURSE_INFO:
            department = analysis.entities.get("department")
            if department:
                n_candidates = max(n_candidates, 20)

        # ── 2단계 검색: 우선 doc_type → 부족 시 전체 확장 ──
        preferred_types = self._INTENT_DOC_TYPES.get(analysis.intent)

        # Phase 1: 우선 doc_type으로 검색
        candidates = self.chroma_store.search(
            query=query,
            n_results=n_candidates,
            student_id=analysis.student_id,
            doc_type=preferred_types,
            department=department,
        )

        # Phase 2: 결과 부족 시 전체 doc_type으로 확장
        if preferred_types and len(candidates) < self._MIN_PHASE1_RESULTS:
            logger.info(
                "Phase 1 결과 %d개 < %d → Phase 2 전체 검색",
                len(candidates), self._MIN_PHASE1_RESULTS,
            )
            all_candidates = self.chroma_store.search(
                query=query,
                n_results=n_candidates,
                student_id=analysis.student_id,
                department=department,
            )
            # 중복 제거 후 병합
            seen_texts = {c.text[:100] for c in candidates}
            for c in all_candidates:
                if c.text[:100] not in seen_texts:
                    candidates.append(c)
                    seen_texts.add(c.text[:100])

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
        # EARLY_GRADUATION: 학번 없어도 일반 자격·일정 안내 가능
        # SCHOLARSHIP: 장학금 정보는 학번 무관하게 조회 가능
        no_id_intents = (
            Intent.SCHEDULE, Intent.ALTERNATIVE,
            Intent.REGISTRATION, Intent.EARLY_GRADUATION,
            Intent.SCHOLARSHIP, Intent.LEAVE_OF_ABSENCE,
        )

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
