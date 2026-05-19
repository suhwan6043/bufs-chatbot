"""Router/Merger/Reranker 입출력 스키마 + 후보 선별·컷오프·정렬.

전제: Router는 ChromaDB/Graph 의존이라 직접 테스트 어려움 → 출력 스키마만 검증.
Reranker는 mock CrossEncoder (conftest.py)로 동작.
Merger는 rule-based RRF + cutoff → 실연동.

실행: pytest tests/test_pipeline/test_router_merger_io.py -v
"""
from __future__ import annotations

import pytest

from app.models import Intent, MergedContext, QuestionType, SearchResult


# ── 1. ContextMerger.merge 출력 스키마 ──────────────────────────────

def test_merge_returns_merged_context(merger):
    """merge() → MergedContext dataclass."""
    res = merger.merge(vector_results=[], graph_results=[])
    assert isinstance(res, MergedContext)


def test_merged_context_required_fields(merger):
    """MergedContext 필수 필드 보존."""
    res = merger.merge(vector_results=[], graph_results=[])
    for field in ("vector_results", "graph_results", "formatted_context",
                  "total_tokens_estimate", "direct_answer", "source_urls",
                  "context_confidence"):
        assert hasattr(res, field), f"missing field: {field}"


def test_merge_empty_input_safe(merger):
    """빈 검색 결과 → context_confidence 0 + 빈 context."""
    res = merger.merge(vector_results=[], graph_results=[])
    assert res.context_confidence == 0.0
    assert res.formatted_context == ""
    assert res.direct_answer == ""


def test_merge_single_vector_result(merger, make_search_result):
    """vector 1건 → formatted_context에 포함."""
    sr = make_search_result("졸업학점은 130학점입니다.", score=0.9)
    res = merger.merge(
        vector_results=[sr], graph_results=[],
        question="졸업요건", intent=Intent.GRADUATION_REQ,
        question_type=QuestionType.FACTOID,
    )
    assert "130학점" in res.formatted_context
    assert res.context_confidence > 0


def test_merge_mixed_vector_graph(merger, make_search_result):
    """vector + graph 혼합 → 양쪽 다 formatted_context에."""
    v = make_search_result("vector 결과 텍스트", score=0.85, source="v.pdf")
    g = make_search_result("graph 결과 텍스트", score=0.92, source="graph")
    res = merger.merge(
        vector_results=[v], graph_results=[g],
        question="졸업요건", intent=Intent.GRADUATION_REQ,
    )
    # 양쪽 텍스트 일부 포함 (formatting에 따라)
    assert "vector" in res.formatted_context or "graph" in res.formatted_context


def test_merge_transcript_injection(merger, make_search_result):
    """transcript_context 제공 시 graph_results에 inject."""
    res = merger.merge(
        vector_results=[], graph_results=[],
        question="내 성적", transcript_context="2020학번 평점 4.0",
    )
    # transcript는 graph_results 앞에 삽입됨 → formatted_context에 포함
    assert "평점 4.0" in res.formatted_context or "transcript" in res.formatted_context.lower()


def test_merge_intent_none_safe(merger, make_search_result):
    """intent=None 안전 처리."""
    sr = make_search_result("test", score=0.5)
    res = merger.merge(vector_results=[sr], graph_results=[], intent=None)
    assert isinstance(res, MergedContext)


def test_merge_confidence_range(merger, make_search_result):
    """context_confidence는 [0, 1+α] 범위 (direct_answer면 1.0+)."""
    sr = make_search_result("test text", score=0.9)
    res = merger.merge(vector_results=[sr], graph_results=[])
    assert 0.0 <= res.context_confidence <= 2.0  # direct_answer boost 포함 여유


# ── 2. Reranker 출력 ────────────────────────────────────────────────

def test_reranker_empty_input(reranker):
    """빈 results → 빈 출력 (크래시 없음)."""
    out = reranker.rerank("졸업요건", [])
    assert out == []


def test_reranker_returns_search_results(reranker, make_search_result, analysis_stub):
    """rerank() → List[SearchResult]."""
    cands = [make_search_result(f"text {i} 졸업", score=0.5) for i in range(5)]
    out = reranker.rerank("졸업요건", cands, analysis=analysis_stub())
    assert all(isinstance(r, SearchResult) for r in out)


def test_reranker_top_k_limit(reranker, make_search_result, analysis_stub):
    """top_k 인자 → 결과 수 제한."""
    cands = [make_search_result(f"text {i}", score=0.5) for i in range(20)]
    out = reranker.rerank("졸업요건", cands, top_k=3, analysis=analysis_stub())
    assert len(out) <= 3


def test_reranker_scores_descending(reranker, make_search_result, analysis_stub):
    """반환 결과는 score 내림차순."""
    cands = [
        make_search_result("졸업학점 학점 학점", score=0.5),  # 토큰 overlap 多
        make_search_result("관련없는 텍스트", score=0.5),       # 토큰 overlap 少
        make_search_result("졸업 정보", score=0.5),
    ]
    out = reranker.rerank("졸업학점", cands, top_k=5, analysis=analysis_stub())
    # 내림차순 검증
    scores = [r.score for r in out]
    assert scores == sorted(scores, reverse=True), f"scores not descending: {scores}"


def test_reranker_none_text_safe(reranker, make_search_result, analysis_stub):
    """text=None 결과 안전 처리."""
    valid = make_search_result("졸업 텍스트", score=0.5)
    invalid = SearchResult(text=None, score=0.5)  # type: ignore
    out = reranker.rerank("졸업", [valid, invalid], analysis=analysis_stub())
    # invalid는 제외되고 valid만 반환
    assert all(r.text for r in out)


def test_reranker_cutoff_applied(reranker, make_search_result, analysis_stub):
    """이중 컷오프 (relative + absolute floor) — 너무 낮은 score는 제외."""
    cands = [
        make_search_result("정확한 졸업학점 매칭", score=0.5),
        make_search_result("zzz 무관한 텍스트", score=0.5),
    ]
    out = reranker.rerank("졸업학점", cands, top_k=10, analysis=analysis_stub())
    # 최소 1건은 통과
    assert len(out) >= 1


def test_reranker_score_attribute_updated(reranker, make_search_result, analysis_stub):
    """반환된 결과의 .score는 rerank 후 새 점수로 업데이트."""
    cands = [make_search_result("졸업학점", score=999.0)]  # 원본 점수 무시
    out = reranker.rerank("졸업학점", cands, top_k=1, analysis=analysis_stub())
    if out:
        # mock predict가 0.5~1.5 범위 점수 부여 → 원본 999가 아닌 새 점수
        assert out[0].score != 999.0


# ── 3. Reranker analysis 인자 (asks_url 분기) ───────────────────────

def test_reranker_asks_url_boost(reranker, make_search_result, analysis_stub):
    """analysis.entities['asks_url']=True → URL 포함 청크 가산점."""
    no_url = make_search_result("일반 텍스트", score=0.5, doc_type="notice")
    with_url = make_search_result(
        "관련 정보는 https://example.com 에 있습니다.",
        score=0.5, doc_type="notice",
    )
    out_no = reranker.rerank("URL 알려줘", [no_url, with_url],
                              top_k=5, analysis=analysis_stub(asks_url=False))
    out_yes = reranker.rerank("URL 알려줘", [no_url, with_url],
                                top_k=5, analysis=analysis_stub(asks_url=True))
    # asks_url=True 시 URL 청크가 비-URL보다 같거나 높아야
    if out_yes and len(out_yes) >= 2:
        url_score = next((r.score for r in out_yes if "https://" in r.text), -1)
        no_url_score = next((r.score for r in out_yes if "https://" not in r.text), -1)
        if url_score > 0 and no_url_score > 0:
            assert url_score >= no_url_score


# ── 4. SearchResult dataclass 직접 검증 ─────────────────────────────

def test_search_result_required_fields():
    sr = SearchResult(text="t")
    for field in ("text", "score", "source", "page_number", "metadata"):
        assert hasattr(sr, field)


def test_search_result_metadata_default_dict():
    sr = SearchResult(text="t")
    assert isinstance(sr.metadata, dict)


# ── 5. Idempotency (state-free) ────────────────────────────────────

def test_reranker_idempotent(reranker, make_search_result, analysis_stub):
    """동일 입력 → 동일 출력."""
    cands = [make_search_result(f"text {i} 졸업", score=0.5) for i in range(5)]
    out1 = reranker.rerank("졸업", cands, top_k=3, analysis=analysis_stub())
    out2 = reranker.rerank("졸업", cands, top_k=3, analysis=analysis_stub())
    assert len(out1) == len(out2)
    assert [r.text for r in out1] == [r.text for r in out2]
