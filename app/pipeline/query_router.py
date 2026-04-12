"""
쿼리 라우터 - Vector/Graph 검색 경로를 결정합니다.
CPU 전용, <1ms 처리
"""

import concurrent.futures
import logging
import os
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
        bm25_index=None,
    ):
        self.chroma_store = chroma_store
        self.academic_graph = academic_graph
        self._reranker = reranker
        self.bm25_index = bm25_index  # 원칙 2: BM25 sparse 후보 확장용

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

        # 원칙 2(비용·지연 최적화): 벡터 ∥ 그래프 병렬 실행
        # 벡터 내부 Phase 1→2→2.5→3 티어 순서는 유지하되,
        # 독립적인 그래프 검색은 동시에 실행해 대기 시간 절감.
        #
        # 안전 스위치: ChromaDB 일부 빌드에서 병렬 query가 segfault를 일으키는
        # 경우가 보고되어, 환경변수 QUERY_ROUTER_SEQUENTIAL=1로 순차 실행 강제 가능.
        need_vector = analysis.requires_vector and self.chroma_store
        need_graph = analysis.requires_graph and self.academic_graph
        _sequential = os.getenv("QUERY_ROUTER_SEQUENTIAL", "").lower() in ("1", "true", "yes")

        if need_vector and need_graph and not _sequential:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                v_fut = ex.submit(self._search_vector, query, analysis)
                g_fut = ex.submit(self._search_graph, query, analysis)
                results["vector_results"] = v_fut.result()
                results["graph_results"] = g_fut.result()
        elif need_vector and need_graph and _sequential:
            # 순차: 그래프 먼저(빠름) → 벡터(임베딩 포함)
            results["graph_results"] = self._search_graph(query, analysis)
            results["vector_results"] = self._search_vector(query, analysis)
        elif need_vector:
            results["vector_results"] = self._search_vector(query, analysis)
        elif need_graph:
            results["graph_results"] = self._search_graph(query, analysis)

        # 원칙 2: 양쪽 빈 결과 → 벡터 폴백 (비용 최적화)
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
    # 주의: 리랭커 활성 시 실제 검색은 `max(intent_k, candidate_k=30)`로 수행되므로
    # 아래 값은 리랭커 비활성 경로의 fallback이자, 향후 candidate_k 조정 시 보험.
    # 다년도·다표 질문(q044/q050 등)의 누락 방지 차원에서 하한을 상향 조정.
    _INTENT_K = {
        Intent.SCHEDULE:       15,   # 5 → 15: 학사일정 다중 청크 대응
        Intent.ALTERNATIVE:    10,   # 5 → 10
        Intent.GRADUATION_REQ: 15,   # 10 → 15
        Intent.REGISTRATION:   15,   # 12 → 15
        Intent.EARLY_GRADUATION: 10, # 8 → 10
        Intent.SCHOLARSHIP:    15,   # 12 → 15
        Intent.LEAVE_OF_ABSENCE: 15, # 12 → 15
        Intent.COURSE_INFO:    20,   # 유지: 시간표 청크 다수 필요
        Intent.MAJOR_CHANGE:   15,   # 10 → 15: 학번별 표 분산 대응
        Intent.GENERAL:        15,   # 유지
    }

    # ── 원칙 2: 인텐트별 우선 doc_type (Tier 1: domestic+guide 최우선) ──
    _INTENT_DOC_TYPES = {
        Intent.GRADUATION_REQ:    ["domestic", "guide", "faq"],
        Intent.REGISTRATION:      ["domestic", "guide", "faq"],
        Intent.SCHEDULE:          ["domestic", "guide", "faq"],
        Intent.MAJOR_CHANGE:      ["domestic", "guide", "faq"],
        Intent.EARLY_GRADUATION:  ["domestic", "guide", "faq"],
        Intent.LEAVE_OF_ABSENCE:  ["domestic", "guide", "faq"],
        Intent.COURSE_INFO:       ["domestic", "guide", "timetable", "faq"],
        # Phase 2 Step C (2026-04-12): notice_attachment 영구 포함.
        # 장학금 공지 첨부파일 (TA장학 지침, KOSAF 안내 등)이 SCHOLARSHIP 질문의
        # 주요 소스인데 기존 preferred_types에서 제외돼 retrieval에 미도달.
        # sc03 (TA장학 선발 기준), sc01 (국가장학 신청처) 등 복구.
        Intent.SCHOLARSHIP:       ["domestic", "guide", "scholarship", "notice_attachment", "faq"],
        Intent.ALTERNATIVE:       ["domestic", "guide", "faq"],
        Intent.GENERAL:           ["domestic", "guide", "faq", "notice"],
    }

    _MIN_PHASE1_RESULTS = 3  # Phase 1 최소 결과 수 (미달 시 Phase 2 확장)

    # 원칙 2(비용·지연 최적화): BM25 비동기 사전 실행용 스레드풀
    _bm25_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def _search_vector(
        self, query: str, analysis: QueryAnalysis
    ) -> List[SearchResult]:
        # Phase 3+ 튜닝 (2026-04-12): asks_url + COURSE_INFO 쿼리 확장.
        # c01 "수업시간표 어디서 확인" → sugang.bufs.ac.kr 청크가 dense embedding 상위에
        # 오지 않는 문제. "수강신청 사이트"를 쿼리에 추가해 BM25/dense 양쪽에서
        # sugang 관련 청크가 후보 풀에 포함되도록 함.
        # 하드코딩 아닌 일반 규칙: URL 기대 질문 + 특정 intent에서 관련 키워드 확장.
        if (analysis.entities.get("asks_url")
                and analysis.intent == Intent.COURSE_INFO
                and "시간표" in query):
            query = f"{query} 수강신청 사이트"
            logger.debug("query expanded for URL-seeking COURSE_INFO: %s", query)

        # 원칙 2: 인텐트별 검색 후보 수 동적 조정
        intent_k = self._INTENT_K.get(analysis.intent, settings.chroma.n_results)

        # OCU intent_k 제한 제거 (2026-04-11 병목 진단):
        # 이전: `intent_k = min(intent_k, 6)` — "단일 토픽 쿼리 노이즈 억제" 목적
        # 문제: OCU 세부 정책 청크(학사안내 p.20-23)가 6위 밖으로 밀려나서
        #       q033/q035/q040 3건이 동일 패턴으로 실패.
        # 해결: intent_k 제한 제거 → REGISTRATION 기본 k=15 + candidate_k=30 경로로
        #       후보 풀 확보. OCU 필터링은 context_merger._filter_by_entity에서 수행.

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

        # Phase 2 Step B (2026-04-12): asks_url 질문은 preferred_types 확장.
        # "어디서 신청/확인" 같은 URL 기대 질문은 intent 기본 범위 외에도
        # notice_attachment(공지 첨부: KOSAF 장학 공지 등), scholarship, notice를
        # retrieval 범위에 포함시켜 sc01 같은 문항에서 URL 청크가 탈락하지 않게 함.
        if preferred_types and analysis.entities.get("asks_url"):
            _URL_EXTRA_TYPES = ("notice_attachment", "notice", "scholarship", "timetable")
            preferred_types = list(preferred_types) + [
                t for t in _URL_EXTRA_TYPES if t not in preferred_types
            ]

        # 원칙 2: 임베딩 1회만 수행 → Phase 1/2/2.5 모두에 재사용
        _q_emb = self.chroma_store.embedder.embed_query(query)

        # 원칙 2(비용·지연 최적화): BM25를 Phase 1과 동시 시작
        # BM25는 Phase 1 결과에 의존하지 않으므로 사전 실행 가능
        # EN 쿼리: BM25 인덱스가 한국어 토큰 기반이므로 ko_query 사용
        bm25_query = (
            analysis.ko_query
            if analysis.lang == "en" and analysis.ko_query
            else query
        )
        bm25_future = None
        if self.bm25_index and self.bm25_index.is_built:
            bm25_future = self._bm25_pool.submit(
                self.bm25_index.search,
                bm25_query, 20, preferred_types,
            )

        # Phase 1: 우선 doc_type으로 검색 (Tier 1 우선)
        candidates = self.chroma_store.search(
            query=query,
            n_results=n_candidates,
            student_id=analysis.student_id,
            doc_type=preferred_types,
            department=department,
            query_embedding=_q_emb,
        )

        # Phase 2: 결과 부족 시 전체 doc_type으로 확장 (Tier 순서 유지)
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
                query_embedding=_q_emb,
            )
            # 중복 제거 후 병합
            seen_texts = {c.text[:100] for c in candidates}
            for c in all_candidates:
                if c.text[:100] not in seen_texts:
                    candidates.append(c)
                    seen_texts.add(c.text[:100])

        # Phase 2.5: FAQ 최소 보장
        # preferred_types에 "faq"가 있는데 FAQ 청크가 2개 미만이면 FAQ 전용 추가 검색
        # 이유: 크롤링된 범용 페이지(수강신청안내 등)가 상위 랭킹을 차지해
        # FAQ 청크가 reranker에 도달하지 못하는 회귀 현상 방지
        if preferred_types and "faq" in preferred_types:
            faq_count = sum(1 for c in candidates if c.metadata.get("doc_type") == "faq")
            if faq_count < 2:
                faq_only = self.chroma_store.search(
                    query=query,
                    n_results=5,
                    student_id=analysis.student_id,
                    doc_type=["faq"],
                    query_embedding=_q_emb,
                )
                seen_texts = {c.text[:100] for c in candidates}
                for c in faq_only:
                    if c.text and c.text[:100] not in seen_texts:
                        candidates.append(c)
                        seen_texts.add(c.text[:100])
                        if sum(1 for x in candidates if x.metadata.get("doc_type") == "faq") >= 2:
                            break

        # Phase 3: BM25 sparse 후보 합류 (이미 병렬 실행됨)
        # Dense 검색이 놓치는 exact keyword match를 BM25로 보완해
        # Reranker(Cross-Encoder)가 더 넓은 후보 풀에서 정확도를 높이도록 한다.
        if bm25_future:
            try:
                bm25_results = bm25_future.result(timeout=10)
                seen_texts = {c.text[:100] for c in candidates}
                bm25_added = 0
                for c in bm25_results:
                    if c.text and c.text[:100] not in seen_texts:
                        candidates.append(c)
                        seen_texts.add(c.text[:100])
                        bm25_added += 1
                if bm25_added:
                    logger.debug("Phase 3 BM25: %d개 후보 추가 (총 %d개)", bm25_added, len(candidates))
            except Exception as e:
                logger.warning("BM25 병렬 검색 실패 (무시): %s", e)

        # 원칙 2: 리랭커 스킵 — 후보 ≤3이면 Cross-Encoder 건너뜀 (재순위화 의미 없음)
        reranker = self.reranker
        if reranker and len(candidates) > 3:
            return reranker.rerank(
                query=query,
                results=candidates,
                top_k=settings.reranker.top_k,
                analysis=analysis,  # Phase 2 Step B: asks_url URL-aware boost
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
            Intent.GRADUATION_REQ, Intent.MAJOR_CHANGE,
            Intent.GENERAL,  # FAQ 검색용 — 학번 없어도 FAQ 탐색 필요
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

        # EN 쿼리: 그래프 내부 키워드 매칭이 한국어 기반이므로
        # matched_terms에서 변환된 ko_query를 사용해야 올바른 노드가 탐색됨
        graph_question = (
            analysis.ko_query
            if analysis.lang == "en" and analysis.ko_query
            else query
        )
        return self.academic_graph.query_to_search_results(
            student_id=analysis.student_id or "2023",
            intent=analysis.intent.value,
            entities=analysis.entities,
            student_type=analysis.student_type or "내국인",
            question=graph_question,
            question_type=analysis.question_type.value if analysis.question_type else "",
        )
