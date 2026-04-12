"""
ko_tokenizer 단위 테스트 — 조사 제거 + 복합 명사 확장 + stopword 필터.

이 유틸의 정확성이 FAQ 오매칭 방지와 교직 키워드 라우팅 복구의 기반이다.
"""

from app.pipeline.ko_tokenizer import (
    FAQ_STOPWORDS,
    core_tokens,
    expand_compound,
    expand_tokens,
    stems,
    strip_suffix,
    tokenize,
)


def test_tokenize_basic():
    assert tokenize("제 2전공으로 교직신청 가능한가요?") == [
        "전공으로", "교직신청", "가능한가요"
    ]


def test_strip_suffix_long_suffixes_first():
    assert strip_suffix("전공으로") == "전공"
    assert strip_suffix("가능한가요") == "가능"
    assert strip_suffix("언제인가요") == "언제"
    # 짧은 토큰은 strip하지 않음 (len(token) > len(suf)+1 규칙)
    assert strip_suffix("으로") == "으로"


def test_stems_end_to_end():
    assert stems("제 2전공으로 교직신청 가능한가요?") == [
        "전공", "교직신청", "가능"
    ]


def test_core_tokens_keeps_content_tokens():
    """의미 토큰은 stopword가 아님 — IDF 가중치로 자동 조절 (ko_tokenizer.py L174 주석).

    이전 설계는 "전공/신청/가능" 등을 하드코딩 stopword로 제거했으나,
    하드코딩 금지 원칙에 따라 FAQ_STOPWORDS는 어미 잔여 + 메타 단어만 남긴다.
    """
    tokens = core_tokens("제 2전공으로 교직신청 가능한가요?", FAQ_STOPWORDS)
    # 주요 콘텐츠 토큰은 유지되어야 한다
    assert "교직신청" in tokens
    assert "전공" in tokens
    # 어미 잔여 "가능한가요"는 strip_suffix로 "가능"이 되어 유지 (stopword 아님)
    assert "가능" in tokens


def test_core_tokens_keeps_general_terms():
    """'신청/방법' 같은 일반어도 stopword가 아니므로 유지된다 (IDF가 낮춰줄 뿐)."""
    tokens = core_tokens("신청 방법은 어떻게 되나요?", FAQ_STOPWORDS)
    assert "신청" in tokens
    assert "방법" in tokens


def test_expand_compound_bigrams_for_long_terms():
    # 4글자 이상 복합 명사는 2글자 bigram으로 확장
    assert set(expand_compound("교직신청")) == {"교직신청", "교직", "직신", "신청"}
    # 3글자 이하는 그대로
    assert expand_compound("장학금") == ["장학금"]
    assert expand_compound("전공") == ["전공"]


def test_expand_tokens_preserves_content_tokens():
    # 4글자 복합어는 2글자 bigram으로 확장되며, "신청"은 더 이상 stopword가 아니므로 유지됨
    result = expand_tokens(["교직신청"], FAQ_STOPWORDS)
    assert "교직신청" in result
    assert "교직" in result
    assert "신청" in result  # stopword 제거 대상 아님 (IDF로 조절)


def test_expand_tokens_complex_query():
    # "장학금 신청 기간" → 모든 콘텐츠 토큰이 유지됨 (IDF로 가중치 조절)
    stems_list = stems("장학금 신청 기간은 언제인가요?")
    result = expand_tokens(stems_list, FAQ_STOPWORDS)
    assert "장학금" in result
    assert "기간" in result
    assert "신청" in result  # stopword 제거 대상 아님


def test_teacher_query_matches_teacher_faq_discriminative():
    """핵심 회귀 테스트: 교직 질문이 교직 FAQ와 **구별적 토큰**(교직, 교직신청)을 공유해야 하며,
    무관한 계절학기 FAQ와는 그러한 구별적 토큰을 공유하지 않아야 한다.

    기존 회귀: "제2전공으로 교직신청 가능한가요?" 질문이 "복수전공→부전공" FAQ로 잘못 매칭.

    주의: stopword가 축소되어(IDF로 자동 조절) "가능" 같은 일반어가 여러 FAQ와
    교집합을 만들 수 있다. 테스트는 단순 교집합 유무가 아니라 "구별적 토큰 공유"를 검증.
    """
    q_key = expand_tokens(stems("제 2전공으로 교직신청 가능한가요?"), FAQ_STOPWORDS)

    # 교직 FAQ와는 "교직" 또는 "교직신청" 등 구별적 토큰을 공유해야 한다
    teacher_faq = "제2전공(복수전공/부전공)으로 교직 이수가 가능한가요?"
    teacher_key = expand_tokens(stems(teacher_faq), FAQ_STOPWORDS)
    discriminative_teacher = {"교직", "교직신청"}
    assert q_key & teacher_key & discriminative_teacher, (
        "교직 질문이 교직 FAQ와 구별적 토큰(교직/교직신청)을 공유하지 않음"
    )

    # 무관한 계절학기 FAQ와는 "교직", "전공" 등 주제 토큰을 공유해서는 안 된다.
    # (일반어 "가능"은 IDF 가중치가 낮으므로 단순 교집합은 허용)
    unrelated = "휴학 중인데 계절학기 수강이 가능한가요?"
    unrelated_key = expand_tokens(stems(unrelated), FAQ_STOPWORDS)
    topic_tokens = {"교직", "교직신청", "전공", "복수전공"}
    shared_topic = q_key & unrelated_key & topic_tokens
    assert not shared_topic, (
        f"교직 질문이 무관한 계절학기 FAQ와 주제 토큰을 공유함: {shared_topic}"
    )
