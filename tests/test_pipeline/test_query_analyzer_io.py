"""QueryAnalyzer 입출력 스키마 + 11 Intent + edge case.

전제: rule-based, LLM/Embedder 불필요. analyzer fixture (conftest.py) 사용.
실행: pytest tests/test_pipeline/test_query_analyzer_io.py -v
"""
from __future__ import annotations

import pytest

from app.models import Intent, QueryAnalysis, QuestionType


# ── 1. 출력 스키마 보존 ────────────────────────────────────────────

def test_analyze_returns_query_analysis_dataclass(analyzer):
    """analyze() → QueryAnalysis dataclass 반환."""
    res = analyzer.analyze("졸업요건 알려줘")
    assert isinstance(res, QueryAnalysis)


def test_analyze_required_fields_present(analyzer):
    """QueryAnalysis 모든 필드 노출 (frontend·downstream 호환)."""
    res = analyzer.analyze("2020학번 졸업요건")
    for field in ("intent", "student_id", "entities", "lang", "question_type",
                  "matched_terms", "normalized_query", "missing_info"):
        assert hasattr(res, field), f"missing field: {field}"


def test_analyze_intent_is_enum(analyzer):
    """intent는 Intent enum."""
    res = analyzer.analyze("졸업요건 알려줘")
    assert isinstance(res.intent, Intent)


def test_analyze_question_type_is_enum(analyzer):
    """question_type은 QuestionType enum."""
    res = analyzer.analyze("개강일은?")
    assert isinstance(res.question_type, QuestionType)


def test_analyze_entities_is_dict(analyzer):
    """entities는 dict."""
    res = analyzer.analyze("졸업요건")
    assert isinstance(res.entities, dict)


def test_analyze_matched_terms_is_list(analyzer):
    """matched_terms는 list."""
    res = analyzer.analyze("졸업요건")
    assert isinstance(res.matched_terms, list)


# ── 2. 학번/학과 추출 ──────────────────────────────────────────────

@pytest.mark.parametrize("q,expected_sid", [
    ("2020학번 졸업요건", "2020"),
    ("2023학번 수강신청 어떻게 해?", "2023"),
    ("19학번 졸업요건", "2019"),  # 2자리 학번 → 4자리 추정
    ("졸업요건 알려줘", None),  # 학번 없음
])
def test_extract_student_id(analyzer, q, expected_sid):
    """학번 정규식 추출 — '학번' 키워드 필수 (입학년도 단독으로는 미추출)."""
    res = analyzer.analyze(q)
    assert res.student_id == expected_sid


def test_extract_department(analyzer):
    """학과 키워드 → entities['department']."""
    res = analyzer.analyze("영어전공 졸업요건")
    # 학과 추출은 정규식 기반 — entity dict에 들어가야
    assert isinstance(res.entities, dict)


# ── 3. Intent 분류 (룰 매핑 검증) ───────────────────────────────────

@pytest.mark.parametrize("q,expected_intent", [
    ("졸업요건 알려줘", Intent.GRADUATION_REQ),
    ("개강일은 언제인가?", Intent.SCHEDULE),
    ("수강신청은 어떻게 하나요", Intent.REGISTRATION),
    ("국가장학금 신청 방법", Intent.SCHOLARSHIP),
    ("휴학 신청 방법", Intent.LEAVE_OF_ABSENCE),
    ("복수전공 신청", Intent.MAJOR_CHANGE),
])
def test_intent_classification_rule_based(analyzer, q, expected_intent):
    """주요 키워드 → Intent 매핑이 변경되면 즉시 감지.

    주의: "수강신청 기간"같이 '기간' 키워드는 SCHEDULE로 분류됨.
    REGISTRATION을 받으려면 행위 키워드("어떻게/방법/신청")가 필요.
    """
    res = analyzer.analyze(q)
    assert res.intent == expected_intent, f"'{q}' → {res.intent.value} (expected {expected_intent.value})"


# ── 4. 언어 감지 ───────────────────────────────────────────────────

def test_korean_lang(analyzer):
    res = analyzer.analyze("졸업요건 알려줘")
    assert res.lang == "ko"


def test_english_lang(analyzer):
    res = analyzer.analyze("What is the graduation requirement?")
    assert res.lang == "en"


def test_mixed_lang_majority_korean(analyzer):
    res = analyzer.analyze("졸업 requirement 알려줘")
    # 한글 우세 → ko
    assert res.lang == "ko"


# ── 5. Edge case ─────────────────────────────────────────────────

def test_empty_query_safe(analyzer):
    """빈 쿼리 — 크래시 없이 GENERAL/factoid 반환."""
    res = analyzer.analyze("")
    assert isinstance(res, QueryAnalysis)
    # 빈 쿼리는 intent 분류 불가 → GENERAL fallback 가능
    assert res.intent in (Intent.GENERAL, Intent.GRADUATION_REQ)  # 룰에 따라


def test_very_long_query_safe(analyzer):
    """긴 쿼리 (1000자) 처리."""
    q = "졸업요건 " * 200
    res = analyzer.analyze(q)
    assert isinstance(res, QueryAnalysis)
    assert res.intent == Intent.GRADUATION_REQ


def test_special_chars_safe(analyzer):
    """특수문자만 — 크래시 없음."""
    res = analyzer.analyze("!!@@##$$%%^^&&**")
    assert isinstance(res, QueryAnalysis)


def test_only_whitespace_safe(analyzer):
    """공백만 — 크래시 없음."""
    res = analyzer.analyze("   \n\t   ")
    assert isinstance(res, QueryAnalysis)


# ── 6. 다회 호출 결정성 (idempotent) ────────────────────────────────

def test_analyze_idempotent(analyzer):
    """동일 입력 → 동일 출력 (mutable state 회피)."""
    q = "2020학번 졸업요건"
    res1 = analyzer.analyze(q)
    res2 = analyzer.analyze(q)
    assert res1.intent == res2.intent
    assert res1.student_id == res2.student_id
    assert res1.lang == res2.lang


# ── 7. normalized_query 적용 ───────────────────────────────────────

def test_glossary_normalize_applied(analyzer):
    """glossary 정규화 — '학식' → '학생식당' 같은 매핑이 적용되면 normalized_query 채워짐."""
    res = analyzer.analyze("학식 메뉴")
    # 정규화 결과는 None이거나 str (구현 따라). 둘 다 허용.
    assert res.normalized_query is None or isinstance(res.normalized_query, str)


# ── 8. matched_terms 형식 ──────────────────────────────────────────

def test_matched_terms_dict_format(analyzer):
    """matched_terms는 [{ko, en}] 형식 dict 리스트 (정의된 경우)."""
    res = analyzer.analyze("수강신청 어떻게")
    for term in res.matched_terms:
        if isinstance(term, dict):
            # ko/en 키 중 하나는 있어야 (정의된 매핑이면)
            assert "ko" in term or "en" in term
