"""컨텍스트 통합기 테스트"""

import pytest
from app.models import SearchResult
from app.pipeline.context_merger import ContextMerger


@pytest.fixture
def merger():
    return ContextMerger()


def test_merge_empty(merger):
    result = merger.merge([], [])
    assert result.formatted_context == ""
    assert result.total_tokens_estimate == 0


def test_merge_vector_only(merger):
    vector = [
        SearchResult(text="졸업학점은 130학점입니다.", score=0.9, page_number=23),
    ]
    result = merger.merge(vector, [])
    assert "130학점" in result.formatted_context
    assert len(result.vector_results) == 1
    assert len(result.graph_results) == 0


def test_merge_graph_priority(merger):
    vector = [
        SearchResult(text="벡터 결과", score=0.8, page_number=10),
    ]
    graph = [
        SearchResult(text="그래프 결과", score=1.0, source="graph"),
    ]
    result = merger.merge(vector, graph)
    # 그래프 결과(score=1.0)가 먼저 나와야 함
    assert result.formatted_context.index("그래프 결과") < result.formatted_context.index("벡터 결과")


def test_merge_token_limit(merger):
    # 매우 긴 텍스트로 토큰 제한 테스트
    long_text = "가" * 5000
    vector = [
        SearchResult(text=long_text, score=0.9, page_number=1),
        SearchResult(text="두번째 결과", score=0.8, page_number=2),
    ]
    result = merger.merge(vector, [])
    # 전체 텍스트보다 짧아야 함
    assert len(result.formatted_context) < len(long_text)


def test_merge_page_number_in_output(merger):
    vector = [
        SearchResult(text="테스트 내용", score=0.9, page_number=42),
    ]
    result = merger.merge(vector, [])
    assert "p.42" in result.formatted_context


def test_merge_preserves_direct_answer(merger):
    graph = [
        SearchResult(
            text="그래프 결과",
            score=1.0,
            source="graph",
            metadata={"direct_answer": "직접 답변입니다. [출처: 페이지 번호]"},
        ),
    ]
    result = merger.merge([], graph)
    assert result.direct_answer == "직접 답변입니다. [출처: 페이지 번호]"
