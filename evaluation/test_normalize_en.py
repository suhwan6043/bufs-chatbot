"""
평가 2: 영어 쿼리 증강 (Glossary.normalize — 영어→한국어 매핑)

검증 항목:
  - 영어 학사 용어가 한국어로 정규화되는지
  - 원문(영어 비-학사 부분)이 보존되는지
  - 중복 치환이 발생하지 않는지
  - 한국어 쿼리는 변경 없이 패스스루되는지
"""

import pytest
from app.pipeline.glossary import Glossary


@pytest.fixture(scope="module")
def glossary():
    return Glossary()


# ────────────────────────────────────────────────────────────────────────────
# 영어 → 한국어 치환 케이스
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("query,expected_ko_term,description", [
    (
        "micro major requirements",
        "마이크로전공",
        "micro major → 마이크로전공",
    ),
    (
        "double major requirements",
        "복수전공",
        "double major → 복수전공",
    ),
    (
        "leave of absence procedure",
        "휴학",
        "leave of absence → 휴학",
    ),
    (
        "graduation credits for 2023",
        "졸업이수학점",
        "graduation credits → 졸업이수학점",
    ),
    (
        "course registration period",
        "수강신청",
        "course registration → 수강신청",
    ),
    (
        "max credit load",
        "최대 수강 학점",
        "max credit load → 최대 수강 학점 (YAML alias: 'max credit load')",
    ),
    (
        "pass/fail option for 2023",
        "성적평가 선택제도",
        "pass/fail option → 성적평가 선택제도 (YAML alias: 'pass/fail option')",
    ),
])
def test_en_term_normalized(glossary, query, expected_ko_term, description):
    result = glossary.normalize(query)
    assert expected_ko_term in result, (
        f"[{description}]\n"
        f"입력: {query!r}\n"
        f"기대 포함: {expected_ko_term!r}\n"
        f"실제 결과: {result!r}"
    )


# ────────────────────────────────────────────────────────────────────────────
# 원문 보존 검증
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("query,preserved_fragment,description", [
    (
        "micro major requirements",
        "requirements",
        "학사 용어 외 단어는 보존",
    ),
    (
        "double major requirements",
        "requirements",
        "학사 용어 외 단어는 보존",
    ),
    (
        "leave of absence procedure",
        "procedure",
        "학사 용어 외 단어는 보존",
    ),
    (
        "graduation credits for 2023",
        "for 2023",
        "연도 표현 보존",
    ),
    (
        "course registration period",
        "period",
        "기간 단어 보존",
    ),
])
def test_original_text_preserved(glossary, query, preserved_fragment, description):
    result = glossary.normalize(query)
    assert preserved_fragment in result, (
        f"[{description}]\n"
        f"입력: {query!r}\n"
        f"보존 기대: {preserved_fragment!r}\n"
        f"실제 결과: {result!r}"
    )


# ────────────────────────────────────────────────────────────────────────────
# 중복 치환 방지
# ────────────────────────────────────────────────────────────────────────────
def test_no_duplicate_replacement(glossary):
    """
    'double major dual major' → 복수전공 1회 치환.
    같은 뜻의 별칭이 두 번 등장해도 동일 용어로 치환되는지 확인.
    결과에 '복수전공'이 최대 2회 포함될 수 있으나,
    치환 과정에서 오류(예: 중복 접두사)가 없어야 한다.
    """
    result = glossary.normalize("double major dual major")
    assert "복수전공" in result
    # 연속 중복 없음: "복수전공복수전공" 형태 미허용
    assert "복수전공복수전공" not in result


# ────────────────────────────────────────────────────────────────────────────
# 비학사 쿼리 → 변경 없음
# ────────────────────────────────────────────────────────────────────────────
def test_no_match_passthrough(glossary):
    query = "what is the weather today"
    result = glossary.normalize(query)
    assert result == query, (
        f"학사 용어 없는 쿼리는 원문 그대로여야 함\n"
        f"입력: {query!r}\n"
        f"실제 결과: {result!r}"
    )


# ────────────────────────────────────────────────────────────────────────────
# 한국어 쿼리 → 패스스루 (영어 패턴 불간섭)
# ────────────────────────────────────────────────────────────────────────────
def test_korean_query_passthrough(glossary):
    query = "수강신청 기간이 언제야?"
    result = glossary.normalize(query)
    # 한국어 약어 정규화는 일어날 수 있으나 영어 패턴이 개입해선 안 됨
    assert "수강신청" in result
    # 영어 치환이 끼어들어 원문을 망가뜨리지 않아야 함
    assert "수강신청" in result and "기간" in result


# ────────────────────────────────────────────────────────────────────────────
# 대소문자 무관 매칭
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("query,expected_ko_term", [
    ("MICRO MAJOR requirements", "마이크로전공"),
    ("Double Major Requirements", "복수전공"),
    ("Leave Of Absence", "휴학"),
    ("COURSE REGISTRATION", "수강신청"),
])
def test_case_insensitive(glossary, query, expected_ko_term):
    result = glossary.normalize(query)
    assert expected_ko_term in result, (
        f"대소문자 무관 매칭 실패\n"
        f"입력: {query!r}\n"
        f"기대 포함: {expected_ko_term!r}\n"
        f"실제: {result!r}"
    )


# ────────────────────────────────────────────────────────────────────────────
# pass/fail 상세 — YAML 별칭 확인
# ────────────────────────────────────────────────────────────────────────────
def test_pass_fail_aliases(glossary):
    """
    YAML에 등록된 pass/fail 별칭 중 하나라도 치환되면 통과.
    aliases_en: [pass/fail option, grade conversion system, P/NP selection]
    → ko: 성적평가 선택제도 (또는 YAML에 정의된 ko 값)
    """
    aliases = ["pass/fail option", "P/NP selection", "grade conversion system"]
    for alias in aliases:
        result = glossary.normalize(alias)
        assert alias.lower() not in result.lower() or any(
            c > "\u00ff" for c in result
        ), (
            f"'{alias}' 치환 안 됨: {result!r}"
        )
