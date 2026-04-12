"""용어 사전 테스트"""

import pytest
import yaml
from pathlib import Path
from app.pipeline.glossary import Glossary

# academic_terms.yaml → en_glossary.yaml로 리네임됨 (2026-04 EN/KO 패리티 커밋)
_TERMS_YAML = Path(__file__).parent.parent / "config" / "en_glossary.yaml"


@pytest.fixture
def glossary():
    return Glossary()


@pytest.fixture(scope="module")
def terms_data():
    with _TERMS_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_normalize_abbreviations(glossary):
    assert "졸업요건" in glossary.normalize("졸요 알려줘")
    assert "수강신청" in glossary.normalize("수신 기간 언제야")
    assert "복수전공" in glossary.normalize("복전 신청하고 싶어")
    assert "부전공" in glossary.normalize("부전 이수학점")


def test_normalize_multiple(glossary):
    result = glossary.normalize("졸요 만족하려면 전필 몇학점?")
    assert "졸업요건" in result
    assert "전공필수" in result
    assert "몇 학점" in result


def test_normalize_empty(glossary):
    assert glossary.normalize("") == ""
    assert glossary.normalize(None) is None


def test_normalize_no_match(glossary):
    text = "일반적인 질문입니다"
    assert glossary.normalize(text) == text


def test_no_duplicate_en_aliases(terms_data):
    """
    aliases_en 전체에 중복 항목이 없어야 합니다.

    동일 alias가 두 용어에 걸리면 glossary.normalize()가
    YAML 파싱 순서에 따라 어느 쪽으로든 치환될 수 있어
    같은 쿼리가 다른 문서를 검색하는 의미 충돌이 발생합니다.

    예: 'add-drop period'가 '수강신청 기간'과 '수강신청 확인기간'에
        동시에 등록되면 "When is the add-drop period?"에 대해
        본 신청일(2/9)이나 정정 기간(3/4) 중 하나만 반환됩니다.
    """
    all_aliases: list[str] = []
    for term in terms_data.get("terms", []):
        all_aliases.extend(a.lower() for a in term.get("aliases_en", []))

    seen: set[str] = set()
    duplicates: set[str] = set()
    for alias in all_aliases:
        if alias in seen:
            duplicates.add(alias)
        seen.add(alias)

    assert not duplicates, (
        f"aliases_en 중복 발견 (의미 충돌 위험):\n"
        + "\n".join(f"  - {a!r}" for a in sorted(duplicates))
    )
