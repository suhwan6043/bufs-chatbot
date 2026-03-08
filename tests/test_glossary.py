"""용어 사전 테스트"""

import pytest
from app.pipeline.glossary import Glossary


@pytest.fixture
def glossary():
    return Glossary()


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
