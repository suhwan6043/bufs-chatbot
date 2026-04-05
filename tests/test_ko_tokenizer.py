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


def test_core_tokens_removes_stopwords():
    # "전공", "가능"은 stopword → 제거, "교직신청"만 남는다
    assert core_tokens("제 2전공으로 교직신청 가능한가요?", FAQ_STOPWORDS) == [
        "교직신청"
    ]


def test_core_tokens_handles_all_stopword_query():
    # 질문이 전부 범용어뿐이면 빈 리스트 반환 → 호출부에서 fallback 결정
    assert core_tokens("신청 방법은 어떻게 되나요?", FAQ_STOPWORDS) == []


def test_expand_compound_bigrams_for_long_terms():
    # 4글자 이상 복합 명사는 2글자 bigram으로 확장
    assert set(expand_compound("교직신청")) == {"교직신청", "교직", "직신", "신청"}
    # 3글자 이하는 그대로
    assert expand_compound("장학금") == ["장학금"]
    assert expand_compound("전공") == ["전공"]


def test_expand_tokens_applies_stopword_filter_after_expansion():
    # "교직신청" 확장 후 "신청"은 stopword로 걸러짐, "교직"/"직신"/"교직신청" 남음
    result = expand_tokens(["교직신청"], FAQ_STOPWORDS)
    assert "교직신청" in result
    assert "교직" in result
    assert "신청" not in result  # stopword 제거


def test_expand_tokens_complex_query():
    # "장학금 신청 기간" → 확장 후 "장학금", "기간" 남음 (신청은 stopword)
    stems_list = stems("장학금 신청 기간은 언제인가요?")
    result = expand_tokens(stems_list, FAQ_STOPWORDS)
    assert "장학금" in result
    assert "기간" in result
    assert "신청" not in result


def test_teacher_query_matches_teacher_faq_terms():
    """핵심 회귀 테스트: 교직 질문의 매칭 key가 교직 FAQ 텍스트와 교집합이 비지 않아야 한다.

    기존 회귀: "제2전공으로 교직신청 가능한가요?" 질문이 "복수전공→부전공" FAQ로 잘못 매칭.
    """
    q_key = expand_tokens(stems("제 2전공으로 교직신청 가능한가요?"), FAQ_STOPWORDS)

    # 진짜 교직 FAQ의 어휘와는 교집합이 있어야 한다
    teacher_faq = "제2전공(복수전공/부전공)으로 교직 이수가 가능한가요?"
    teacher_key = expand_tokens(stems(teacher_faq), FAQ_STOPWORDS)
    assert q_key & teacher_key, "교직 질문이 교직 FAQ와 매칭되지 않음"

    # 무관한 "복수전공 포기" FAQ와는 "복수전공"만 공유 — 이건 의미상 허용.
    # 중요한 건 "계절학기 휴학" FAQ 같은 완전 무관 건과 매칭되지 않는 것.
    unrelated = "휴학 중인데 계절학기 수강이 가능한가요?"
    unrelated_key = expand_tokens(stems(unrelated), FAQ_STOPWORDS)
    assert not (q_key & unrelated_key), (
        f"교직 질문이 무관한 계절학기 FAQ와 매칭됨: {q_key & unrelated_key}"
    )
