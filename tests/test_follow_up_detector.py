"""follow_up_detector 휴리스틱 테스트."""

from app.pipeline.follow_up_detector import detect


def _hist(pairs: list[tuple[str, str]]) -> list[dict]:
    out: list[dict] = []
    for u, a in pairs:
        out.append({"role": "user", "content": u})
        out.append({"role": "assistant", "content": a})
    return out


# ── 비-follow-up 케이스 (history 없음/신규 주제) ──

def test_no_history_not_follow_up():
    sig = detect("졸업요건 알려줘", [])
    assert sig.is_follow_up is False
    assert sig.reason == "no_history"


def test_empty_query_not_follow_up():
    sig = detect("", _hist([("a", "b")]))
    assert sig.is_follow_up is False


def test_new_topic_with_subject_not_follow_up():
    # 완전히 새로운 주제 + 주어 있음
    sig = detect(
        "조기졸업 요건은 어떻게 되나요?",
        _hist([("국가장학금 유형?", "I유형, II유형...")]),
    )
    assert sig.is_follow_up is False


# ── 단일 지시 대명사 (Stage 2 가능) ──

def test_singular_pronoun_ko():
    sig = detect("그거 자세히 알려줘", _hist([("장학금?", "I유형...")]))
    assert sig.is_follow_up is True
    assert sig.skip_rule_stage is False  # 규칙 치환 가능
    assert "singular_pronoun" in sig.reason


def test_singular_pronoun_en():
    sig = detect("Tell me more about it", _hist([("scholarship?", "Type I...")]))
    assert sig.is_follow_up is True
    assert sig.skip_rule_stage is False


# ── 분배/순서 대명사 (Stage 2 스킵 → LLM 폴백) ──

def test_distributive_each_ko():
    sig = detect("각각 어떤 차이가 있어?", _hist([("장학금 유형?", "I유형...")]))
    assert sig.is_follow_up is True
    assert sig.skip_rule_stage is True  # 규칙 치환 불가 → LLM 필수
    assert "distributive" in sig.reason or "comparison" in sig.reason


def test_distributive_only():
    sig = detect("각각 설명해줘", _hist([("장학금 유형?", "I유형...")]))
    assert sig.is_follow_up is True
    assert sig.skip_rule_stage is True


def test_ordinal_first():
    sig = detect("첫번째는 뭐야?", _hist([("A, B, C 차이?", "A는...B는...")]))
    assert sig.is_follow_up is True
    assert sig.skip_rule_stage is True


def test_comparison_short():
    sig = detect("차이가 뭐야?", _hist([("A와 B?", "...")]))
    assert sig.is_follow_up is True
    assert sig.skip_rule_stage is True


# ── 생략·짧은 질문 ──

def test_elliptic_conjunction():
    sig = detect("그럼 신청은?", _hist([("자격?", "...")]))
    assert sig.is_follow_up is True
    assert sig.skip_rule_stage is True


def test_no_subject_short_ko():
    # 주어 없는 매우 짧은 한국어 질문
    sig = detect("언제 신청해?", _hist([("장학금?", "...")]))
    assert sig.is_follow_up is True


# ── 설정: follow_up_max_words 경계 ──

def test_long_query_with_subject_not_follow_up():
    # 긴 질문 + 주어 있음 → 자립적, follow-up 아님
    sig = detect(
        "2023학번 학생이 조기졸업을 하려면 어떤 요건을 충족해야 합니까?",
        _hist([("장학금?", "...")]),
    )
    assert sig.is_follow_up is False
