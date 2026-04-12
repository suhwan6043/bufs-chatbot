"""
파이프라인 통합 테스트 — QueryAnalyzer → QueryRouter → ContextMerger → ResponseValidator

실제 LLM·ChromaDB·네트워크 없이 전체 파이프라인 흐름을 검증합니다.
모든 외부 의존성은 MagicMock으로 대체합니다.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from typing import List

from app.models import (
    Intent,
    QueryAnalysis,
    SearchResult,
    MergedContext,
)
from app.pipeline.query_analyzer import QueryAnalyzer
from app.pipeline.query_router import QueryRouter
from app.pipeline.context_merger import ContextMerger, _DEFAULT_CONTEXT_TOKENS as MAX_CONTEXT_TOKENS, TOKENS_PER_CHAR
from app.pipeline.response_validator import ResponseValidator


# ═══════════════════════════════════════════════════════════
#  공통 픽스처
# ═══════════════════════════════════════════════════════════

def _make_search_result(
    text: str,
    score: float = 0.8,
    page_number: int = 1,
    doc_type: str = "notice_attachment",
    source_url: str = "",
    source_type: str = "vector",
    direct_answer: str = "",
) -> SearchResult:
    """테스트용 SearchResult 팩토리."""
    metadata = {
        "doc_type": doc_type,
        "source_type": source_type,
        "page_number": page_number,
    }
    if source_url:
        metadata["source_url"] = source_url
    if direct_answer:
        metadata["direct_answer"] = direct_answer
    return SearchResult(
        text=text,
        score=score,
        source="test_source",
        page_number=page_number,
        metadata=metadata,
    )


@pytest.fixture
def mock_chroma():
    """ChromaStore.search → 미리 정의된 SearchResult 목록을 반환하는 mock."""
    store = MagicMock()
    store.search.return_value = [
        _make_search_result("졸업요건 관련 내용. 130학점 이수 필요.", score=0.9, page_number=5),
        _make_search_result("수강신청 관련 정책.", score=0.7, page_number=10),
    ]
    return store


@pytest.fixture
def mock_graph():
    """AcademicGraph.query_to_search_results → 빈 리스트를 반환하는 mock."""
    graph = MagicMock()
    graph.query_to_search_results.return_value = []
    return graph


# ═══════════════════════════════════════════════════════════
#  1. QueryAnalyzer — 인텐트 분류 + 라우팅 플래그
# ═══════════════════════════════════════════════════════════

class TestQueryAnalyzerRouting:
    """QueryAnalyzer가 올바른 라우팅 플래그를 설정하는지 검증합니다."""

    def setup_method(self):
        self.analyzer = QueryAnalyzer()

    def test_graduation_req_requires_graph_and_vector(self):
        """졸업요건 질문 → requires_graph=True, requires_vector=True"""
        result = self.analyzer.analyze("2023학번 졸업요건이 어떻게 되나요?")
        assert result.intent == Intent.GRADUATION_REQ
        assert result.requires_graph is True
        assert result.requires_vector is True

    def test_graduation_req_extracts_student_id(self):
        """학번 추출 확인."""
        result = self.analyzer.analyze("2023학번 졸업학점은 몇 학점인가요?")
        assert result.student_id == "2023"

    def test_registration_without_student_id_sets_missing_info(self):
        """학번 없는 수강신청 질문 → missing_info에 'student_id' 포함."""
        result = self.analyzer.analyze("수강신청 최대 학점이 몇 학점이에요?")
        assert result.intent == Intent.REGISTRATION
        assert "student_id" in result.missing_info

    def test_schedule_requires_graph_not_vector_by_default(self):
        """일반 일정 질문 → requires_graph=True, requires_vector=False"""
        result = self.analyzer.analyze("개강일이 언제예요?")
        assert result.intent == Intent.SCHEDULE
        assert result.requires_graph is True
        assert result.requires_vector is False

    def test_schedule_with_timetable_keyword_requires_vector(self):
        """교시 포함 일정 질문 → intent=SCHEDULE, requires_vector=True로 전환됨."""
        result = self.analyzer.analyze("교시 편성 일정 알려줘")
        assert result.intent == Intent.SCHEDULE
        assert result.requires_vector is True

    def test_alternative_uses_both_channels(self):
        """대체과목 질문 → requires_graph=True + requires_vector=True.

        query_analyzer.py: ALTERNATIVE 인텐트는 그래프 FAQ + 벡터 PDF 본문을 함께 사용한다.
        이전에는 requires_vector=False였으나 q054(대체/동일과목 정의) 회귀 때문에 활성화.
        학사안내 PDF p.9의 원문이 벡터 검색에서 필요.
        """
        result = self.analyzer.analyze("대체과목이 있나요?")
        assert result.intent == Intent.ALTERNATIVE
        assert result.requires_graph is True
        assert result.requires_vector is True

    def test_general_uses_both_channels(self):
        """일반 질문 → requires_graph=True (FAQ 직접 답변 경로), requires_vector=True.

        query_analyzer.py L337-344 주석 참조: GENERAL에서도 FAQ direct_answer를
        활용하려면 그래프 경로가 필요하므로, GENERAL은 벡터+그래프 모두 사용한다.
        """
        result = self.analyzer.analyze("학교 도서관은 어디에 있나요?")
        assert result.intent == Intent.GENERAL
        assert result.requires_graph is True
        assert result.requires_vector is True

    def test_two_digit_student_id_converted(self):
        """2자리 학번 → 4자리 연도 변환 (23학번 → 2023)."""
        result = self.analyzer.analyze("23학번 졸업요건 알려줘")
        assert result.student_id == "2023"

    def test_grade_selection_disables_graph(self):
        """성적선택제도 질문 → requires_graph=False (그래프 스키마에 없음)."""
        result = self.analyzer.analyze("성적선택제도가 뭔가요?")
        assert result.requires_graph is False
        assert result.requires_vector is True


# ═══════════════════════════════════════════════════════════
#  2. QueryRouter — 검색 경로 라우팅
# ═══════════════════════════════════════════════════════════

class TestQueryRouterRouting:
    """QueryRouter가 analysis 플래그에 따라 올바르게 검색을 위임하는지 검증합니다."""

    def _make_router(self, chroma=None, graph=None):
        return QueryRouter(chroma_store=chroma, academic_graph=graph)

    def test_requires_vector_calls_chroma_search(self, mock_chroma, mock_graph):
        """requires_vector=True → chroma_store.search 호출됨."""
        router = self._make_router(chroma=mock_chroma, graph=mock_graph)
        analysis = QueryAnalysis(
            intent=Intent.GRADUATION_REQ,
            student_id="2023",
            requires_graph=False,
            requires_vector=True,
        )
        results = router.route_and_search("졸업요건 질문", analysis)
        assert mock_chroma.search.call_count >= 1  # 2단계 검색으로 2회 호출 가능
        assert len(results["vector_results"]) > 0

    def test_requires_graph_calls_academic_graph(self, mock_chroma, mock_graph):
        """requires_graph=True, student_id 있음 → academic_graph.query_to_search_results 호출됨."""
        mock_graph.query_to_search_results.return_value = [
            _make_search_result("그래프 결과", score=1.0, source_type="graph"),
        ]
        router = self._make_router(chroma=mock_chroma, graph=mock_graph)
        analysis = QueryAnalysis(
            intent=Intent.GRADUATION_REQ,
            student_id="2023",
            requires_graph=True,
            requires_vector=False,
        )
        results = router.route_and_search("졸업요건 질문", analysis)
        mock_graph.query_to_search_results.assert_called_once()
        assert len(results["graph_results"]) > 0

    def test_requires_vector_false_skips_chroma(self, mock_chroma, mock_graph):
        """requires_vector=False → chroma_store.search 호출 안 됨 (그래프 결과 있을 때)."""
        mock_graph.query_to_search_results.return_value = [
            _make_search_result("일정 결과", score=1.0, source_type="graph"),
        ]
        router = self._make_router(chroma=mock_chroma, graph=mock_graph)
        analysis = QueryAnalysis(
            intent=Intent.SCHEDULE,
            requires_graph=True,
            requires_vector=False,
        )
        router.route_and_search("개강일 질문", analysis)
        mock_chroma.search.assert_not_called()

    def test_requires_graph_false_skips_graph(self, mock_chroma, mock_graph):
        """requires_graph=False → academic_graph.query_to_search_results 호출 안 됨."""
        router = self._make_router(chroma=mock_chroma, graph=mock_graph)
        analysis = QueryAnalysis(
            intent=Intent.GENERAL,
            requires_graph=False,
            requires_vector=True,
        )
        router.route_and_search("일반 질문", analysis)
        mock_graph.query_to_search_results.assert_not_called()

    def test_graduation_req_no_student_id_calls_graph(self, mock_chroma, mock_graph):
        """GRADUATION_REQ + student_id 없음 → 기본 학번('2023')으로 그래프 탐색 실행."""
        mock_graph.query_to_search_results.return_value = []
        router = self._make_router(chroma=mock_chroma, graph=mock_graph)
        analysis = QueryAnalysis(
            intent=Intent.GRADUATION_REQ,
            student_id=None,
            requires_graph=True,
            requires_vector=True,
        )
        router.route_and_search("졸업요건", analysis)
        mock_graph.query_to_search_results.assert_called_once()

    def test_schedule_intent_no_student_id_calls_graph(self, mock_chroma, mock_graph):
        """SCHEDULE intent는 student_id 없어도 그래프 탐색 가능."""
        mock_graph.query_to_search_results.return_value = []
        router = self._make_router(chroma=mock_chroma, graph=mock_graph)
        analysis = QueryAnalysis(
            intent=Intent.SCHEDULE,
            student_id=None,
            requires_graph=True,
            requires_vector=False,
        )
        router.route_and_search("개강일 언제", analysis)
        mock_graph.query_to_search_results.assert_called_once()

    def test_both_results_combined(self, mock_chroma, mock_graph):
        """vector + graph 모두 결과 있을 때 두 목록 모두 반환됨."""
        mock_graph.query_to_search_results.return_value = [
            _make_search_result("그래프 결과", score=1.0),
        ]
        router = self._make_router(chroma=mock_chroma, graph=mock_graph)
        analysis = QueryAnalysis(
            intent=Intent.GRADUATION_REQ,
            student_id="2023",
            requires_graph=True,
            requires_vector=True,
        )
        results = router.route_and_search("졸업요건", analysis)
        assert len(results["vector_results"]) > 0
        assert len(results["graph_results"]) > 0

    def test_no_stores_returns_empty(self):
        """ChromaStore와 AcademicGraph 둘 다 None → 빈 결과 반환."""
        router = self._make_router(chroma=None, graph=None)
        analysis = QueryAnalysis(
            intent=Intent.GRADUATION_REQ,
            student_id="2023",
            requires_graph=True,
            requires_vector=True,
        )
        results = router.route_and_search("질문", analysis)
        assert results["vector_results"] == []
        assert results["graph_results"] == []


# ═══════════════════════════════════════════════════════════
#  3. ContextMerger — 컨텍스트 병합
# ═══════════════════════════════════════════════════════════

class TestContextMerger:
    """ContextMerger의 병합 로직, 토큰 예산, source_urls를 검증합니다."""

    def setup_method(self):
        self.merger = ContextMerger()

    def test_basic_merge_returns_merged_context(self):
        """기본 merge → MergedContext 반환, formatted_context에 텍스트 포함."""
        v = [_make_search_result("벡터 결과 텍스트", score=0.8)]
        merged = self.merger.merge(v, [])
        assert isinstance(merged, MergedContext)
        assert "벡터 결과 텍스트" in merged.formatted_context

    def test_graph_results_prioritized_over_vector(self):
        """그래프 결과(score=1.0)가 벡터 결과보다 앞에 배치됩니다."""
        v = [_make_search_result("벡터 내용", score=0.9)]
        g = [_make_search_result("그래프 내용", score=1.0)]
        merged = self.merger.merge(v, g)
        graph_pos = merged.formatted_context.find("그래프 내용")
        vector_pos = merged.formatted_context.find("벡터 내용")
        assert graph_pos < vector_pos

    def test_direct_answer_extracted_from_graph_metadata(self):
        """그래프 결과의 direct_answer 메타데이터 → MergedContext.direct_answer에 저장."""
        g = [_make_search_result(
            "그래프 데이터",
            score=1.0,
            direct_answer="직접 답변: 130학점 필요",
        )]
        merged = self.merger.merge([], g)
        assert merged.direct_answer == "직접 답변: 130학점 필요"

    def test_empty_results_returns_empty_context(self):
        """벡터·그래프 모두 빈 결과 → formatted_context 빈 문자열."""
        merged = self.merger.merge([], [])
        assert merged.formatted_context == ""
        assert merged.total_tokens_estimate == 0

    def test_token_budget_respected(self):
        """총 토큰 추정값이 MAX_CONTEXT_TOKENS를 초과하지 않습니다."""
        # 각 800자 결과 5개 → 합계 4000자 >> 한도(800자)
        large_results = [
            _make_search_result("가" * 800, score=1.0 - i * 0.1)
            for i in range(5)
        ]
        merged = self.merger.merge(large_results, [])
        assert merged.total_tokens_estimate <= MAX_CONTEXT_TOKENS + 200  # 약간의 마진

    def test_source_urls_collected_from_notice_results(self):
        """doc_type='notice' 결과의 source_url → source_urls에 포함됨."""
        v = [_make_search_result(
            "공지 내용",
            score=0.8,
            doc_type="notice",
            source_url="https://www.bufs.ac.kr/bbs/board.php?bo_table=notice&wr_id=1",
        )]
        merged = self.merger.merge(v, [])
        assert len(merged.source_urls) == 1
        assert merged.source_urls[0]["url"].startswith("https://")

    def test_source_urls_deduplicated(self):
        """동일 URL 두 번 → source_urls에 한 번만 포함됨."""
        url = "https://www.bufs.ac.kr/notice/123"
        v = [
            _make_search_result("공지1", score=0.9, doc_type="notice", source_url=url),
            _make_search_result("공지2", score=0.8, doc_type="notice", source_url=url),
        ]
        merged = self.merger.merge(v, [])
        assert len(merged.source_urls) == 1

    def test_non_notice_doc_type_excluded_from_urls(self):
        """doc_type='notice_attachment' 이외 일반 PDF → source_urls에 미포함."""
        v = [_make_search_result(
            "일반 PDF 내용",
            score=0.8,
            doc_type="academic_guide",
            source_url="https://example.com/guide.pdf",
        )]
        merged = self.merger.merge(v, [])
        assert len(merged.source_urls) == 0

    def test_total_tokens_estimate_positive_for_nonempty(self):
        """내용이 있으면 total_tokens_estimate > 0."""
        v = [_make_search_result("내용 있는 결과", score=0.8)]
        merged = self.merger.merge(v, [])
        assert merged.total_tokens_estimate > 0

    def test_multiple_graph_results_all_selected(self):
        """그래프 결과 여러 개 → 모두 graph_results에 포함됨."""
        g = [
            _make_search_result("그래프1", score=1.0),
            _make_search_result("그래프2", score=1.0),
        ]
        merged = self.merger.merge([], g)
        assert len(merged.graph_results) == 2


# ═══════════════════════════════════════════════════════════
#  4. ResponseValidator — 할루시네이션 감지
# ═══════════════════════════════════════════════════════════

class TestResponseValidatorIntegration:
    """ResponseValidator의 숫자 교차검증과 컨텍스트 부재 응답 처리를 검증합니다."""

    def setup_method(self):
        self.validator = ResponseValidator()

    def test_correct_number_passes(self):
        """컨텍스트의 숫자와 답변의 숫자가 일치 → validation passed."""
        context = "졸업을 위해 130학점을 이수해야 합니다."
        answer = "졸업 요건은 130학점 이수입니다. [p.5]"
        passed, warnings = self.validator.validate(answer, context, [])
        # 할루시네이션된 숫자 없어야 함
        hallucination_warnings = [w for w in warnings if "확인되지 않는 숫자" in w]
        assert len(hallucination_warnings) == 0

    def test_wrong_number_flagged(self):
        """컨텍스트에 없는 학점 숫자 → 할루시네이션 경고 생성."""
        context = "졸업을 위해 130학점을 이수해야 합니다."
        answer = "졸업 요건은 140학점 이수입니다. [p.5]"
        passed, warnings = self.validator.validate(answer, context, [])
        assert passed is False
        assert any("140학점" in w for w in warnings)

    def test_no_context_response_passes(self):
        """'관련 정보를 찾을 수 없습니다' 형태의 정직한 응답 → 검증 통과."""
        answer = "관련 정보를 찾을 수 없습니다."
        passed, warnings = self.validator.validate(answer, "", [])
        assert passed is True
        assert warnings == []

    def test_no_context_response_variant_passes(self):
        """'해당 정보가 없습니다' 형태도 통과."""
        answer = "해당 정보가 없어 답변이 어렵습니다."
        passed, warnings = self.validator.validate(answer, "", [])
        assert passed is True

    def test_empty_answer_fails(self):
        """빈 답변 → 검증 실패."""
        passed, warnings = self.validator.validate("", "컨텍스트", [])
        assert passed is False
        assert len(warnings) > 0

    def test_whitespace_only_answer_fails(self):
        """공백만 있는 답변 → 검증 실패."""
        passed, warnings = self.validator.validate("   \n  ", "컨텍스트", [])
        assert passed is False

    def test_missing_source_reference_adds_warning(self):
        """답변에 출처 표기([p.N]) 없음 → 경고 추가 (but passed는 숫자 기준)."""
        context = "130학점 필요"
        answer = "130학점을 이수해야 합니다."  # 출처 표기 없음
        passed, warnings = self.validator.validate(answer, context, [])
        # 숫자는 일치하므로 passed=True, 출처 경고 존재
        assert passed is True
        assert any("출처" in w for w in warnings)

    def test_multiple_wrong_numbers_all_flagged(self):
        """여러 틀린 숫자 → 경고에 모두 포함."""
        context = "교양 30학점, 전공 60학점 필요."
        answer = "교양 35학점, 전공 65학점이 필요합니다."
        passed, warnings = self.validator.validate(answer, context, [])
        assert passed is False
        # 35, 65가 경고에 포함되어야 함
        warning_text = " ".join(warnings)
        assert "35학점" in warning_text or "65학점" in warning_text

    def test_year_numbers_not_flagged_as_hallucination(self):
        """학번 연도(2023학번 등)는 할루시네이션으로 처리하지 않음."""
        context = "2023학번 이후 학생에게 적용됩니다."
        answer = "2023학번 학생의 졸업요건입니다. [p.1]"
        passed, warnings = self.validator.validate(answer, context, [])
        # 2023 은 컨텍스트에 있으므로 경고 없어야 함
        hallucination_warnings = [w for w in warnings if "확인되지 않는 숫자" in w]
        assert len(hallucination_warnings) == 0


# ═══════════════════════════════════════════════════════════
#  5. 파이프라인 통합 시나리오
# ═══════════════════════════════════════════════════════════

class TestPipelineEndToEnd:
    """Analyzer → Router → Merger → Validator 전체 흐름을 검증합니다."""

    def _run_pipeline(
        self,
        question: str,
        vector_results: list = None,
        graph_results: list = None,
    ):
        """테스트용 파이프라인 실행 헬퍼."""
        vector_results = vector_results or []
        graph_results = graph_results or []

        # 1. 분석
        analyzer = QueryAnalyzer()
        analysis = analyzer.analyze(question)

        # 2. 라우팅 (mock store)
        mock_chroma = MagicMock()
        mock_chroma.search.return_value = vector_results
        mock_graph = MagicMock()
        mock_graph.query_to_search_results.return_value = graph_results

        router = QueryRouter(chroma_store=mock_chroma, academic_graph=mock_graph)
        search_results = router.route_and_search(question, analysis)

        # 3. 병합
        merger = ContextMerger()
        merged = merger.merge(
            search_results["vector_results"],
            search_results["graph_results"],
        )

        return analysis, merged

    def test_graduation_req_full_pipeline(self):
        """'2023학번 졸업요건' → GRADUATION_REQ, 컨텍스트에 벡터 결과 포함."""
        v = [_make_search_result("졸업을 위해 130학점 이수 필요.", score=0.9)]
        analysis, merged = self._run_pipeline(
            "2023학번 졸업요건이 어떻게 되나요?",
            vector_results=v,
        )
        assert analysis.intent == Intent.GRADUATION_REQ
        assert analysis.student_id == "2023"
        assert "130학점" in merged.formatted_context

    def test_empty_context_from_empty_search_results(self):
        """검색 결과 없음 → formatted_context 빈 문자열."""
        _, merged = self._run_pipeline("어떤 질문인지 모르겠어요")
        assert merged.formatted_context == ""

    def test_schedule_intent_graph_called_not_vector(self):
        """'개강일 언제' → SCHEDULE, graph 호출, vector 미호출."""
        mock_chroma = MagicMock()
        mock_graph = MagicMock()
        mock_graph.query_to_search_results.return_value = [
            _make_search_result("개강일: 2025년 3월 3일", score=1.0),
        ]

        analyzer = QueryAnalyzer()
        analysis = analyzer.analyze("개강일이 언제인가요?")

        router = QueryRouter(chroma_store=mock_chroma, academic_graph=mock_graph)
        router.route_and_search("개강일이 언제인가요?", analysis)

        assert analysis.intent == Intent.SCHEDULE
        assert analysis.requires_vector is False
        mock_chroma.search.assert_not_called()

    def test_direct_answer_in_context_skips_need_for_llm(self):
        """그래프 결과에 direct_answer → MergedContext.direct_answer에 저장."""
        g = [_make_search_result(
            "학사일정 데이터",
            score=1.0,
            direct_answer="2025년 3월 3일부터 개강",
        )]
        _, merged = self._run_pipeline(
            "개강일이 언제예요?",
            graph_results=g,
        )
        assert merged.direct_answer == "2025년 3월 3일부터 개강"

    def test_validator_passes_after_correct_merge(self):
        """파이프라인 결과로 생성된 컨텍스트 기반 올바른 답변 → 검증 통과."""
        v = [_make_search_result("전공학점 60학점이 필요합니다.", score=0.9)]
        _, merged = self._run_pipeline("졸업 전공학점", vector_results=v)

        validator = ResponseValidator()
        answer = "졸업을 위해 전공학점 60학점이 필요합니다. [p.1]"
        passed, warnings = validator.validate(
            answer, merged.formatted_context, merged.vector_results
        )
        hallucination_warnings = [w for w in warnings if "확인되지 않는 숫자" in w]
        assert len(hallucination_warnings) == 0

    def test_validator_catches_hallucinated_number_after_merge(self):
        """컨텍스트: 60학점, 답변: 70학점 → 할루시네이션 감지."""
        v = [_make_search_result("전공학점 60학점이 필요합니다.", score=0.9)]
        _, merged = self._run_pipeline("졸업 전공학점", vector_results=v)

        validator = ResponseValidator()
        answer = "졸업을 위해 전공학점 70학점이 필요합니다. [p.1]"
        passed, warnings = validator.validate(
            answer, merged.formatted_context, merged.vector_results
        )
        assert passed is False
        assert any("70학점" in w for w in warnings)

    def test_notice_source_url_propagated_through_pipeline(self):
        """공지사항 출처 URL이 MergedContext.source_urls에 전달됨."""
        url = "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=99"
        v = [_make_search_result(
            "수강신청 일정 공지",
            score=0.85,
            doc_type="notice",
            source_url=url,
        )]
        _, merged = self._run_pipeline("수강신청 일정", vector_results=v)
        urls = [item["url"] for item in merged.source_urls]
        assert url in urls
