"""응답 검증기 테스트"""

import pytest
from app.models import SearchResult
from app.pipeline.response_validator import ResponseValidator


@pytest.fixture
def validator():
    return ResponseValidator()


def test_empty_answer(validator):
    passed, warnings = validator.validate("", "context", [])
    assert passed is False
    assert "비어" in warnings[0]


def test_no_context_response(validator):
    answer = "확인되지 않는 정보입니다."
    passed, warnings = validator.validate(answer, "context", [])
    assert passed is True
    assert len(warnings) == 0


def test_valid_answer_with_source(validator):
    context = "졸업학점은 130학점입니다."
    answer = "졸업학점은 130학점입니다. [출처: 23]"
    passed, warnings = validator.validate(answer, context, [])
    assert passed is True


def test_missing_source_warning(validator):
    context = "졸업학점은 130학점입니다."
    answer = "졸업학점은 130학점입니다."
    passed, warnings = validator.validate(answer, context, [])
    assert any("출처" in w for w in warnings)


def test_hallucinated_number(validator):
    context = "졸업학점은 130학점입니다."
    answer = "졸업학점은 999학점입니다. [출처: 23]"
    passed, warnings = validator.validate(answer, context, [])
    assert passed is False
    assert any("999" in w for w in warnings)


def test_valid_numbers_pass(validator):
    context = "교양 최소 30학점, 전공 최소 60학점"
    answer = "교양은 30학점, 전공은 60학점이 필요합니다. [출처: 10]"
    passed, warnings = validator.validate(answer, context, [])
    assert passed is True
