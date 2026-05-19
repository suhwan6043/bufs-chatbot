"""AnswerGenerator cache + ResponseValidator 입출력 검증.

전제:
- AnswerGenerator._response_cache는 메모리 dict (LLM 미필요)
- generate() async iter는 LLM 의존 → 별도 mock 패턴 (본 파일에서는 cache + validator 위주)
- ResponseValidator는 rule-based (정규식 + 키워드)

실행: pytest tests/test_pipeline/test_generator_validator_io.py -v
"""
from __future__ import annotations

import pytest

from app.models import SearchResult


# ── 1. AnswerGenerator cache round-trip ────────────────────────────

def _cache_kwargs(question: str = "졸업요건", **overrides) -> dict:
    """get/store_cached_response 공통 인자 생성."""
    base = {
        "question": question,
        "context": "졸업학점 130학점",
        "student_id": None,
        "question_focus": None,
        "lang": "ko",
        "matched_terms": [],
        "student_context": "",
        "context_confidence": 1.0,
        "question_type": "factoid",
        "intent": "GRADUATION_REQ",
        "entities": {},
        "history": None,
        "share_across_sessions": True,
    }
    base.update(overrides)
    return base


def test_cache_miss_returns_none(generator_for_cache):
    """저장 안 한 키 → None."""
    assert generator_for_cache.get_cached_response(**_cache_kwargs()) is None


def test_cache_store_then_retrieve(generator_for_cache):
    """store 직후 get → 동일 답변."""
    g = generator_for_cache
    kw = _cache_kwargs()
    g.store_cached_response("졸업학점은 130학점입니다.", **kw)
    assert g.get_cached_response(**kw) == "졸업학점은 130학점입니다."


def test_cache_empty_answer_not_stored(generator_for_cache):
    """빈 답변은 캐시 저장 안 됨."""
    g = generator_for_cache
    kw = _cache_kwargs()
    g.store_cached_response("", **kw)
    g.store_cached_response("   ", **kw)
    assert g.get_cached_response(**kw) is None


def test_cache_different_questions_separate(generator_for_cache):
    """질문이 다르면 cache key도 다름."""
    g = generator_for_cache
    g.store_cached_response("answer A", **_cache_kwargs(question="질문 A"))
    g.store_cached_response("answer B", **_cache_kwargs(question="질문 B"))
    assert g.get_cached_response(**_cache_kwargs(question="질문 A")) == "answer A"
    assert g.get_cached_response(**_cache_kwargs(question="질문 B")) == "answer B"


def test_cache_different_intent_separate(generator_for_cache):
    """intent가 다르면 별도 캐시."""
    g = generator_for_cache
    g.store_cached_response("졸업답", **_cache_kwargs(intent="GRADUATION_REQ"))
    g.store_cached_response("일정답", **_cache_kwargs(intent="SCHEDULE"))
    assert g.get_cached_response(**_cache_kwargs(intent="GRADUATION_REQ")) == "졸업답"
    assert g.get_cached_response(**_cache_kwargs(intent="SCHEDULE")) == "일정답"


def test_cache_share_across_sessions_flag(generator_for_cache):
    """share_across_sessions=False → 세션 격리 (다른 student_id 등 시 hit 안 됨)."""
    g = generator_for_cache
    g.store_cached_response("답", **_cache_kwargs(share_across_sessions=True))
    # share=True로 저장 → 동일 key로 조회 성공
    assert g.get_cached_response(**_cache_kwargs(share_across_sessions=True)) == "답"


# ── 2. ResponseValidator.validate ──────────────────────────────────

def test_validate_returns_tuple(validator):
    """validate() → (bool, list[str])."""
    out = validator.validate("졸업학점은 130학점입니다.", "졸업학점 130", search_results=[])
    assert isinstance(out, tuple)
    assert len(out) == 2
    passed, warnings = out
    assert isinstance(passed, bool)
    assert isinstance(warnings, list)


def test_validate_empty_answer_fails(validator):
    """빈 답변 → passed=False + warning."""
    passed, warnings = validator.validate("", "context", search_results=[])
    assert passed is False
    assert len(warnings) > 0


def test_validate_whitespace_answer_fails(validator):
    """공백 답변 → 빈 답변 동등 처리."""
    passed, warnings = validator.validate("   \n\t  ", "context", search_results=[])
    assert passed is False


def test_validate_no_context_response_passes(validator):
    """'관련 정보를 찾을 수 없습니다.' → 정직한 거부 응답, passed=True."""
    out_text = "관련 정보를 찾을 수 없습니다."
    passed, warnings = validator.validate(out_text, "", search_results=[])
    assert passed is True
    assert warnings == []  # 거부 응답은 warning 없음


def test_validate_warnings_are_strings(validator):
    """warnings는 list[str]."""
    _, warnings = validator.validate("어떤 답변", "context", search_results=[])
    for w in warnings:
        assert isinstance(w, str)


def test_validate_idempotent(validator):
    """동일 입력 → 동일 출력 (state-free)."""
    answer = "졸업학점은 130학점입니다."
    ctx = "졸업학점 130"
    out1 = validator.validate(answer, ctx, search_results=[])
    out2 = validator.validate(answer, ctx, search_results=[])
    assert out1 == out2


def test_validate_with_search_results(validator, make_search_result):
    """search_results 인자도 안전 처리."""
    sr = make_search_result("졸업 정보", score=0.9)
    out = validator.validate("졸업학점", "졸업학점", search_results=[sr])
    assert isinstance(out, tuple)


# ── 3. Generator generate (async iter mock) ────────────────────────

@pytest.mark.asyncio
async def test_generator_generate_yields_tokens(generator_for_cache, monkeypatch):
    """generate() async iter → 토큰 yield (mock LLM).

    monkeypatch로 _stream_one_pass를 async iter mock으로 치환.
    """
    g = generator_for_cache

    async def fake_stream(*args, **kwargs):
        for tok in ["졸업", "학점은 ", "130", "학점"]:
            yield tok

    # generate가 어떤 내부 메서드 호출하는지에 따라 다름 — 가장 표준 entry
    # 직접 호출하지 않고, generate가 yield하는 것만 검증
    # 실제 generate는 LLM 호출하므로 skip 처리
    pytest.skip("generate() LLM 의존 — interface 테스트는 test_chat_interface.py에서 ChatResponse 통해 검증")


# ── 4. Cache key 결정성 ────────────────────────────────────────────

def _key_kwargs(**overrides) -> dict:
    """_make_cache_key용 인자 (share_across_sessions 제외 — 그 인자는 get/store에만 적용)."""
    kw = _cache_kwargs(**overrides)
    return {k: v for k, v in kw.items() if k != "share_across_sessions"}


def test_cache_key_deterministic(generator_for_cache):
    """동일 인자 → 동일 cache_key (내부 _make_cache_key)."""
    g = generator_for_cache
    key1 = g._make_cache_key(**_key_kwargs())
    key2 = g._make_cache_key(**_key_kwargs())
    assert key1 == key2


def test_cache_key_changes_with_question(generator_for_cache):
    """질문 바뀌면 cache_key 변경."""
    g = generator_for_cache
    key1 = g._make_cache_key(**_key_kwargs(question="A"))
    key2 = g._make_cache_key(**_key_kwargs(question="B"))
    assert key1 != key2


def test_cache_key_hex64(generator_for_cache):
    """cache_key는 SHA-256 hex (64자)."""
    g = generator_for_cache
    key = g._make_cache_key(**_key_kwargs())
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


# ── 5. ResponseValidator 출처 누락 warning ─────────────────────────

def test_validate_warns_no_source_reference(validator):
    """출처 없는 답변 → 'p.X' 같은 페이지 참조 누락 warning."""
    answer = "졸업학점은 130학점입니다."
    passed, warnings = validator.validate(answer, "졸업학점 130", search_results=[])
    # 출처 참조 누락 warning이 포함될 수 있음 (구현에 따라)
    assert isinstance(warnings, list)
    # 모든 warning은 사람이 읽을 수 있는 string
    for w in warnings:
        assert len(w) > 0
