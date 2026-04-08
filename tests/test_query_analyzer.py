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
    # 방법/규정 질문 → REGISTRATION
    result = analyzer.analyze("수강신청 방법 알려줘")
    assert result.intent == Intent.REGISTRATION


def test_intent_registration_period_is_schedule(analyzer):
    # "수강신청 기간" → SCHEDULE (날짜/기간 질문이므로 학사일정에서 처리)
    result = analyzer.analyze("수강신청 기간 알려줘")
    assert result.intent == Intent.SCHEDULE


def test_intent_extra_registration_normalized(analyzer):
    # 추가 수강신청 → 수강신청 정정 → 기간 질문이므로 SCHEDULE
    result = analyzer.analyze("추가 수강신청 기간 알려줘")
    assert result.intent == Intent.SCHEDULE


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


def test_intent_grading_selection_is_registration(analyzer):
    """성적선택제(A~F/P/NP) 질문은 REGISTRATION으로 분류, 그래프 OFF·벡터 ON"""
    result = analyzer.analyze(
        "A~F로 나오는 성적 등급제와 Pass/Non-Pass로 나오는 성적제도를"
        " 선택할 수 있는 제도가 있다던데 언제 신청가능한지 요건은 뭔지 알려줘"
    )
    assert result.intent == Intent.REGISTRATION
    assert result.requires_vector is True
    assert result.requires_graph is False   # 그래프 스키마에 없는 정보


def test_intent_grading_selection_pnp_keyword(analyzer):
    """P/NP 키워드만으로도 그래프 OFF·벡터 ON"""
    result = analyzer.analyze("P/NP 성적선택 신청 기간 알려줘")
    assert result.intent == Intent.REGISTRATION
    assert result.requires_vector is True
    assert result.requires_graph is False


def test_grade_selection_short_query(analyzer):
    """'패논패 신청일 언제야' → 그래프 OFF, 벡터 ON"""
    result = analyzer.analyze("패논패 신청일 언제야")
    assert result.requires_vector is True
    assert result.requires_graph is False


def test_schedule_with_policy_keyword_enables_vector(analyzer):
    """SCHEDULE 분류여도 성적·제도 키워드가 있으면 벡터 검색 활성화"""
    result = analyzer.analyze("성적포기 언제까지야")
    assert result.requires_vector is True
    assert result.requires_graph is False


# ── EN 파이프라인 갭 검증 ─────────────────────────────────────────────────

def test_en_lang_detected(analyzer):
    """EN 쿼리는 lang='en'으로 분류"""
    result = analyzer.analyze("how many credits do I need to graduate?")
    assert result.lang == "en"


def test_en_intent_graduation_req(analyzer):
    """'graduation requirements' → GRADUATION_REQ"""
    result = analyzer.analyze("what are the graduation requirements?")
    assert result.intent == Intent.GRADUATION_REQ


def test_en_question_focus_period(analyzer):
    """Gap 2: 'when' 포함 쿼리 → question_focus='period'"""
    result = analyzer.analyze("when is the course registration period?")
    assert result.entities.get("question_focus") == "period"


def test_en_question_focus_limit(analyzer):
    """Gap 2: 'maximum' 포함 쿼리 → question_focus='limit'"""
    result = analyzer.analyze("what is the maximum number of credits I can register?")
    assert result.entities.get("question_focus") == "limit"


def test_en_question_focus_limit_how_many_credits(analyzer):
    """Gap 2: 'how many credits' 포함 쿼리 → question_focus='limit'"""
    result = analyzer.analyze("how many credits do I need to graduate?")
    assert result.entities.get("question_focus") == "limit"


def test_en_student_type_international(analyzer):
    """Gap 3: 'international student' → student_type='외국인'"""
    result = analyzer.analyze("what are the registration rules for international students?")
    assert result.student_type == "외국인"


def test_en_student_type_transfer(analyzer):
    """Gap 3: 'transfer student' → student_type='편입생'"""
    result = analyzer.analyze("what scholarship is available for transfer students?")
    assert result.student_type == "편입생"


def test_en_cohort_extraction_class_of(analyzer):
    """Gap 3: 'class of 2020' → student_id='2020'"""
    result = analyzer.analyze("what are the graduation requirements for class of 2020 students?")
    assert result.student_id == "2020"


def test_en_cohort_extraction_year_student(analyzer):
    """Gap 3: '2021 student' → student_id='2021'"""
    result = analyzer.analyze("how many credits does a 2021 student need to graduate?")
    assert result.student_id == "2021"


def test_en_cohort_no_false_positive(analyzer):
    """Gap 3 오탐 방지: 연도 단독은 student_id로 추출 안 됨"""
    result = analyzer.analyze("the 2020 academic calendar shows holidays")
    assert result.student_id is None


def test_en_requires_vector_always_true(analyzer):
    """EN 쿼리는 항상 vector 검색 활성화 (BGE-M3 크로스링구얼)"""
    result = analyzer.analyze("tell me about scholarship applications")
    assert result.requires_vector is True


def test_en_requires_graph_for_graduation(analyzer):
    """EN 졸업요건 쿼리는 그래프 검색도 활성화"""
    result = analyzer.analyze("what are the graduation requirements?")
    assert result.requires_graph is True


def test_en_schedule_intent_period_question(analyzer):
    """'when' + 학사일정 관련 용어 → SCHEDULE intent"""
    result = analyzer.analyze("when does the course registration period start?")
    assert result.intent == Intent.SCHEDULE
