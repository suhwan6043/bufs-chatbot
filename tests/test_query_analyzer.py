"""쿼리 분석기 테스트"""

import pytest
from app.models import Intent
from app.pipeline.query_analyzer import QueryAnalyzer


@pytest.fixture
def analyzer():
    return QueryAnalyzer()


def test_extract_student_id(analyzer):
    result = analyzer.analyze("2023학번 졸업요건 알려줘")
    assert result.student_id == "2023"


def test_extract_student_id_none(analyzer):
    result = analyzer.analyze("졸업요건 알려줘")
    assert result.student_id is None
    assert "student_id" in result.missing_info


def test_intent_graduation(analyzer):
    result = analyzer.analyze("2023학번 졸업학점 몇 학점이야?")
    assert result.intent == Intent.GRADUATION_REQ


def test_intent_registration(analyzer):
    result = analyzer.analyze("수강신청 기간 알려줘")
    assert result.intent == Intent.REGISTRATION


def test_intent_schedule(analyzer):
    result = analyzer.analyze("기말고사 일정 언제야")
    assert result.intent == Intent.SCHEDULE


def test_intent_major_change(analyzer):
    result = analyzer.analyze("복수전공 신청 방법")
    assert result.intent == Intent.MAJOR_CHANGE


def test_intent_alternative(analyzer):
    result = analyzer.analyze("동일과목 대체 변경 알려줘")
    assert result.intent == Intent.ALTERNATIVE


def test_intent_general(analyzer):
    result = analyzer.analyze("학교 위치가 어디야")
    assert result.intent == Intent.GENERAL


def test_requires_graph_for_graduation(analyzer):
    result = analyzer.analyze("2023학번 졸업요건")
    assert result.requires_graph is True


def test_requires_vector_for_registration(analyzer):
    result = analyzer.analyze("수강신청 방법")
    assert result.requires_vector is True


def test_department_extraction(analyzer):
    result = analyzer.analyze("컴퓨터공학과 졸업요건")
    assert result.entities.get("department") == "컴퓨터공학"


def test_registration_override_for_gpa_exception(analyzer):
    result = analyzer.analyze(
        "2023학번 이후 학생이 직전학기 평점 4.0 이상이면 최대 몇 학점까지 신청할 수 있는가?"
    )
    assert result.intent == Intent.REGISTRATION
    assert result.student_id == "2023"
    assert result.entities.get("gpa_exception") is True


def test_registration_detects_basket_limit(analyzer):
    result = analyzer.analyze("장바구니에 담을 수 있는 최대 학점은 얼마인가?")
    assert result.intent == Intent.REGISTRATION
    assert result.entities.get("basket_limit") is True


def test_extract_student_groups_for_comparison(analyzer):
    result = analyzer.analyze(
        "2024학번 이후, 2023학번, 2022학번, 2021학번의 복수전공 이수학점은 각각 얼마인가?"
    )
    assert result.entities.get("student_groups") == [
        "2024_2025",
        "2023",
        "2022",
        "2021",
    ]
