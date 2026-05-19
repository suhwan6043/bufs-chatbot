"""파이프라인 단위테스트 공통 fixture.

설계 원칙:
1. **빠른 실행** — LLM/Embedder/Reranker 등 무거운 의존성은 mock
2. **격리** — 각 테스트는 독립 (싱글톤 mutable state 회피)
3. **명확한 fail** — mock 객체에 spec= 명시해서 잘못된 호출 즉시 감지
4. **재사용** — analyzer fixture는 module 단위 (regex compile 1회)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# 프로젝트 루트 path 등록
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


# ── 1. QueryAnalyzer (rule-based, LLM 불필요) ─────────────────────

@pytest.fixture(scope="module")
def analyzer():
    """QueryAnalyzer 인스턴스 — module 단위 재사용 (정규식 compile 비용 1회).

    embedder가 None이면 question_type 분류가 fallback ("factoid")이지만
    intent/entities/student_id 등 핵심 출력은 정상.
    """
    from app.pipeline.query_analyzer import QueryAnalyzer
    return QueryAnalyzer(embedder=None)


# ── 2. SearchResult 빌더 ─────────────────────────────────────────

@pytest.fixture
def make_search_result():
    """SearchResult 빠른 생성 헬퍼."""
    from app.models import SearchResult
    def _make(text: str, score: float = 0.8, source: str = "test.pdf",
              page: int = 1, doc_type: str = "domestic", **meta) -> SearchResult:
        return SearchResult(
            text=text, score=score, source=source, page_number=page,
            metadata={"doc_type": doc_type, **meta},
        )
    return _make


# ── 3. Mock CrossEncoder (Reranker LLM 없이 동작) ─────────────────

@pytest.fixture
def mock_cross_encoder():
    """BGE-Reranker CrossEncoder mock.

    predict([[q, t1], [q, t2], ...]) → list[float] (인덱스 역순 = 점수 내림)
    실제 모델 대신 query 토큰이 text에 많이 나타날수록 높은 점수 부여.
    """
    def predict(pairs):
        scores = []
        for q, t in pairs:
            q_tokens = set(q.replace(" ", ""))
            t_tokens = set((t or "").replace(" ", ""))
            overlap = len(q_tokens & t_tokens)
            scores.append(overlap * 0.1 + 0.5)  # 0.5 ~ 1.5 range
        return scores
    m = MagicMock()
    m.predict = predict
    return m


@pytest.fixture
def reranker(mock_cross_encoder):
    """Reranker 인스턴스 — mock CrossEncoder 주입."""
    from app.pipeline.reranker import Reranker
    return Reranker(model=mock_cross_encoder)


# ── 4. ContextMerger (rule-based, LLM 불필요) ─────────────────────

@pytest.fixture
def merger():
    """ContextMerger — rule-based RRF + cutoff, 의존성 없음."""
    from app.pipeline.context_merger import ContextMerger
    return ContextMerger()


# ── 5. ResponseValidator (rule-based) ─────────────────────────────

@pytest.fixture
def validator():
    """ResponseValidator — 정규식 + 키워드 검증, 의존성 없음."""
    from app.pipeline.response_validator import ResponseValidator
    return ResponseValidator()


# ── 6. AnswerGenerator cache 전용 (LLM stream은 별도 mock) ─────────

@pytest.fixture
def generator_for_cache():
    """AnswerGenerator — cache only 검증용 (LLM 호출 안 함).

    settings 의존성 때문에 import만 늦게.
    """
    from app.pipeline.answer_generator import AnswerGenerator
    g = AnswerGenerator()
    # 기존 cache 비우기 (test isolation)
    g._response_cache.clear()
    return g


# ── 7. AnalysisStub (Reranker가 analysis 인자로 받음) ─────────────

@pytest.fixture
def analysis_stub():
    """QueryAnalysis stub — entities.asks_url 기본 False."""
    from app.models import QueryAnalysis, Intent
    def _make(intent: Intent = Intent.GRADUATION_REQ, asks_url: bool = False, **kwargs):
        ent = {"asks_url": asks_url, **kwargs.get("entities", {})}
        return QueryAnalysis(
            intent=intent,
            entities=ent,
            normalized_query=kwargs.get("normalized_query"),
            **{k: v for k, v in kwargs.items() if k not in ("entities", "normalized_query")},
        )
    return _make
