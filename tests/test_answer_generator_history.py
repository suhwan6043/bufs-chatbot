"""answer_generator history 윈도우 트리밍 + 캐시 키 분리 단위 테스트."""

from app.pipeline.answer_generator import AnswerGenerator


def _hist(pairs: list[tuple[str, str]]) -> list[dict]:
    out: list[dict] = []
    for u, a in pairs:
        out.append({"role": "user", "content": u})
        out.append({"role": "assistant", "content": a})
    return out


# ── _trim_history_for_llm ──

def test_trim_history_empty():
    assert AnswerGenerator._trim_history_for_llm(None, 2, 500) == []
    assert AnswerGenerator._trim_history_for_llm([], 2, 500) == []


def test_trim_history_keeps_recent_turns():
    hist = _hist([("q1", "a1"), ("q2", "a2"), ("q3", "a3")])
    trimmed = AnswerGenerator._trim_history_for_llm(hist, max_turns=2, char_budget=1000)
    # user/assistant 쌍으로 2턴 = 4 메시지
    assert len(trimmed) == 4
    # 가장 오래된 쌍(q1/a1) 제외
    contents = [m["content"] for m in trimmed]
    assert "q1" not in contents
    assert "q3" in contents


def test_trim_history_respects_char_budget():
    long_a = "A" * 2000
    hist = _hist([("짧은질문", long_a)])
    trimmed = AnswerGenerator._trim_history_for_llm(hist, max_turns=2, char_budget=200)
    # budget 내로 축소됨
    total = sum(len(m["content"]) for m in trimmed)
    assert total <= 300  # 약간의 여유 허용


def test_trim_history_drops_metadata_fields():
    hist = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a", "rated": True, "rating": 5},
    ]
    trimmed = AnswerGenerator._trim_history_for_llm(hist, max_turns=1, char_budget=100)
    for m in trimmed:
        assert set(m.keys()) == {"role", "content"}


def test_trim_history_skips_unpaired_user():
    # user만 있고 assistant가 아직 없는 경우 (in-flight)
    hist = [{"role": "user", "content": "pending"}]
    trimmed = AnswerGenerator._trim_history_for_llm(hist, max_turns=2, char_budget=100)
    assert trimmed == []


# ── _make_cache_key: history 구분 ──

def test_cache_key_differs_with_history():
    gen = AnswerGenerator()
    base_kwargs = dict(
        question="각각 차이?",
        context="context text",
        student_id=None,
        question_focus=None,
        lang="ko",
        matched_terms=None,
        student_context=None,
        context_confidence=0.8,
        question_type=None,
        intent="SCHOLARSHIP",
        entities={},
    )
    key_no_hist = gen._make_cache_key(**base_kwargs, history=None)
    key_with_hist = gen._make_cache_key(
        **base_kwargs,
        history=_hist([("장학금?", "**국가장학금**...")]),
    )
    assert key_no_hist != key_with_hist


def test_cache_key_same_when_history_same():
    gen = AnswerGenerator()
    h = _hist([("장학금?", "**국가장학금**...")])
    base = dict(
        question="각각 차이?",
        context="x",
        lang="ko",
        intent="SCHOLARSHIP",
    )
    k1 = gen._make_cache_key(**base, history=h)
    k2 = gen._make_cache_key(**base, history=list(h))
    assert k1 == k2
