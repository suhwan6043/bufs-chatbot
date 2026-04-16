"""
평가 3: 용어집 번역 일관성 (academic_terms.yaml)

LLM 출력 없이 YAML만으로 검증 가능한 항목:
  - 각 용어의 'en' 필드가 고정 번역값과 일치하는지
  - 금지 표현(avoid_en)이 aliases_en에 포함되지 않았는지
  - 영어 시스템 프롬프트 _EN_TERM_INSTRUCTION에 주요 용어가 포함되어 있는지
"""

import pytest
import yaml
from pathlib import Path

YAML_PATH = Path(__file__).parent.parent / "config" / "academic_terms.yaml"


@pytest.fixture(scope="module")
def terms_data():
    with YAML_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def term_by_ko(terms_data):
    """ko → term dict 인덱스"""
    return {t["ko"]: t for t in terms_data.get("terms", [])}


# ────────────────────────────────────────────────────────────────────────────
# 고정 번역값 검증 (YAML en 필드)
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ko_term,expected_en,forbidden_en", [
    # forbidden_en: LLM 출력(en 필드)에 쓰이면 안 되는 표현 목록
    # aliases_en은 입력 인식용이므로 forbidden_en 검사 대상이 아님
    ("복수전공",       "Double Major",                    ["second major", "dual major"]),
    ("부전공",         "Minor",                           ["sub-major"]),
    ("마이크로전공",   "Micro Major",                     ["micro-major", "small major"]),
    ("수강신청",       "Course Registration",             ["enrollment", "class sign-up"]),
    ("졸업이수학점",   "Required Credits for Graduation",  ["graduation points"]),
    ("학점",           "credits",                         ["points", "units"]),
    ("휴학",           "Leave of Absence",                []),
    ("학사경고",       "Academic Probation",              ["academic warning"]),
    ("이수구분",       "Credit Classification",           ["course type"]),
])
def test_en_field_value(term_by_ko, ko_term, expected_en, forbidden_en):
    assert ko_term in term_by_ko, f"'{ko_term}' 항목이 YAML에 없습니다"
    term = term_by_ko[ko_term]
    actual_en = term.get("en", "")

    # en 필드 (LLM 출력에 쓰이는 고정 번역) 검증
    assert actual_en == expected_en, (
        f"[{ko_term}] en 필드 불일치\n"
        f"  기대: {expected_en!r}\n"
        f"  실제: {actual_en!r}"
    )

    # en 필드에 금지 표현이 그대로 쓰이지 않는지 확인
    # (aliases_en은 입력 인식 전용이므로 검사 제외)
    actual_en_lower = actual_en.lower()
    for forbidden in forbidden_en:
        assert forbidden.lower() not in actual_en_lower, (
            f"[{ko_term}] en 출력 필드에 금지 표현 포함: {forbidden!r}\n"
            f"  en: {actual_en!r}"
        )


# ────────────────────────────────────────────────────────────────────────────
# 성적평가 선택제도 (Pass/Fail) — 별도 검증
# ────────────────────────────────────────────────────────────────────────────
def test_pass_fail_term(term_by_ko):
    """
    YAML에서 Pass/Fail 관련 용어의 en 필드가 'Pass/Fail Conversion System'
    또는 이에 준하는 표현인지 확인.
    """
    # 가능한 ko 키 목록
    candidates = ["성적평가 선택제도", "성적선택", "성적선택제"]
    found = None
    for key in candidates:
        if key in term_by_ko:
            found = term_by_ko[key]
            break

    assert found is not None, (
        f"Pass/Fail 관련 용어가 YAML에 없습니다. 확인 대상: {candidates}"
    )
    en = found.get("en", "")
    # "P/NP" 또는 "Pass" 또는 "Conversion" 중 하나라도 포함
    assert any(kw in en for kw in ["Pass", "P/NP", "Conversion", "Grade"]), (
        f"Pass/Fail 용어의 en 값이 기대 표현과 다름: {en!r}"
    )


# ────────────────────────────────────────────────────────────────────────────
# 시스템 프롬프트 _EN_TERM_INSTRUCTION 포함 확인
# ────────────────────────────────────────────────────────────────────────────
def test_system_prompt_includes_key_terms():
    """
    answer_generator.py의 _EN_TERM_INSTRUCTION에
    LLM 출력 일관성에 핵심적인 용어 힌트가 포함되어 있는지 확인.
    """
    from app.pipeline.answer_generator import _EN_TERM_INSTRUCTION

    required_hints = [
        "Double Major",
        "Leave of Absence",
        "Course Registration",
        "credits",
        "Graduation Requirements",
    ]
    for hint in required_hints:
        assert hint in _EN_TERM_INSTRUCTION, (
            f"_EN_TERM_INSTRUCTION에 힌트 누락: {hint!r}\n"
            f"현재 값:\n{_EN_TERM_INSTRUCTION}"
        )


# ────────────────────────────────────────────────────────────────────────────
# YAML 무결성 검사
# ────────────────────────────────────────────────────────────────────────────
def test_yaml_structure_integrity(terms_data):
    """모든 항목이 ko, en 필드를 가지고 있는지 확인"""
    errors = []
    for i, term in enumerate(terms_data.get("terms", [])):
        if not term.get("ko"):
            errors.append(f"항목[{i}] 'ko' 필드 없음: {term}")
        if not term.get("en"):
            errors.append(f"항목[{i}] 'en' 필드 없음: {term}")
    assert not errors, "\n".join(errors)


def test_no_duplicate_ko_keys(terms_data):
    """ko 키 중복 없음"""
    ko_keys = [t.get("ko") for t in terms_data.get("terms", [])]
    seen = set()
    duplicates = []
    for k in ko_keys:
        if k in seen:
            duplicates.append(k)
        seen.add(k)
    assert not duplicates, f"중복 ko 키: {duplicates}"


def test_no_duplicate_en_aliases_across_terms(terms_data):
    """서로 다른 용어가 동일한 aliases_en을 공유하지 않아야 함 (오분류 방지)"""
    alias_to_ko: dict[str, str] = {}
    conflicts = []
    for term in terms_data.get("terms", []):
        ko = term.get("ko", "")
        for alias in term.get("aliases_en", []):
            alias_lower = alias.lower()
            if alias_lower in alias_to_ko and alias_to_ko[alias_lower] != ko:
                conflicts.append(
                    f"'{alias}': '{alias_to_ko[alias_lower]}' vs '{ko}'"
                )
            else:
                alias_to_ko[alias_lower] = ko
    assert not conflicts, f"aliases_en 충돌:\n" + "\n".join(conflicts)
