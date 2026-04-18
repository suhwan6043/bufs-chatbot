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


def test_en_cohort_extraction_admitted_in_year(analyzer):
    result = analyzer.analyze("students admitted in 2022 graduating through method 1")
    assert result.student_id == "2022"


def test_en_cohort_admitted_or_later_not_collapsed(analyzer):
    result = analyzer.analyze("students admitted in 2024 or later changing their major")
    assert result.student_id is None


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


# ── 갭 1: EN 엔티티 추출 보강 검증 ────────────────────────────────────────────

def test_en_entity_ocu(analyzer):
    """EN: 'ocu' 키워드 → entities['ocu']=True"""
    result = analyzer.analyze("how do I register for an OCU course?")
    assert result.lang == "en"
    assert result.entities.get("ocu") is True


def test_en_entity_gpa_exception(analyzer):
    """EN: 'gpa 4.0' 키워드 → entities['gpa_exception']=True"""
    result = analyzer.analyze("if my gpa 4.0 last semester, how many credits can I take?")
    assert result.entities.get("gpa_exception") is True


def test_en_entity_basket_limit(analyzer):
    """EN: 'wish list' → entities['basket_limit']=True (기간 질문 아닐 때)"""
    result = analyzer.analyze("what is the maximum credits I can put in my wish list?")
    assert result.entities.get("basket_limit") is True


def test_en_entity_basket_no_limit_when_period(analyzer):
    """EN: 'basket' + 'when' → basket_limit 미설정 (기간 질문)"""
    result = analyzer.analyze("when does the basket registration period start?")
    assert result.entities.get("basket_limit") is None


def test_en_entity_payment_period(analyzer):
    """EN: 'tuition payment' → entities['payment_period']=True"""
    result = analyzer.analyze("when is the tuition payment deadline?")
    assert result.entities.get("payment_period") is True


def test_en_entity_second_major_credits(analyzer):
    """EN: 'double major credits' → entities['second_major_credits']=True"""
    result = analyzer.analyze("how many double major credits do I need?")
    assert result.entities.get("second_major_credits") is True


def test_en_entity_graduation_cert_topik(analyzer):
    """EN: 'topik' → entities['graduation_cert']='TOPIK'"""
    result = analyzer.analyze("do I need to pass TOPIK to graduate?")
    assert result.entities.get("graduation_cert") == "TOPIK"


def test_en_entity_graduation_cert_toeic(analyzer):
    """EN: 'toeic' → entities['graduation_cert']='TOEIC'"""
    result = analyzer.analyze("is toeic required for graduation certification?")
    assert result.entities.get("graduation_cert") == "TOEIC"


def test_en_entity_major_method(analyzer):
    """EN: 'method 1' → entities['major_method']='방법1' (맥락 불필요)"""
    result = analyzer.analyze("what is method 1 for completing a double major?")
    assert result.entities.get("major_method") == "방법1"


def test_en_entity_major_method_option_with_context(analyzer):
    """EN: 'option 2' + 전공 맥락 → entities['major_method']='방법2'"""
    result = analyzer.analyze("how do I complete a double major using option 2?")
    assert result.entities.get("major_method") == "방법2"


def test_en_entity_major_method_option_no_context(analyzer):
    """EN: 'option 1' 단독 (전공 맥락 없음) → major_method 미설정 (오탐 방지)"""
    result = analyzer.analyze("this option 1 is not related to academics at all")
    assert result.entities.get("major_method") is None


def test_en_limit_credits_standalone_no_false_positive(analyzer):
    """EN: 'I got 3 credits' 같은 일반 문장 → 'double major credits'는 limit, 단순 credits는 아님"""
    # _EN_LIMIT_KW에서 "credits" 단독 제거 후, 복합 표현만 남음
    result = analyzer.analyze("I got 3 credits this semester")
    # "credits" 단독은 더 이상 limit 트리거 안 함
    assert result.entities.get("question_focus") != "limit"


def test_en_limit_double_major_credits_triggers_limit(analyzer):
    """EN: 'double major credits' → question_focus='limit' (복합 표현)"""
    result = analyzer.analyze("how many double major credits do I need?")
    assert result.entities.get("question_focus") == "limit"


def test_en_question_focus_method(analyzer):
    """EN: 'how to' → question_focus='method'"""
    result = analyzer.analyze("how to apply for a leave of absence?")
    assert result.entities.get("question_focus") == "method"


def test_en_question_focus_location(analyzer):
    """EN: 'where' (절차 키워드 없음) → question_focus='location'"""
    result = analyzer.analyze("where is the academic affairs office located?")
    assert result.entities.get("question_focus") == "location"


def test_en_question_focus_eligibility(analyzer):
    """EN: 'eligible' → question_focus='eligibility'"""
    result = analyzer.analyze("am I eligible for early graduation?")
    assert result.entities.get("question_focus") == "eligibility"


def test_en_ko_query_populated(analyzer):
    """EN 쿼리 분석 시 ko_query가 None이 아닌 한국어 용어로 설정됨"""
    result = analyzer.analyze("what are the graduation requirements?")
    assert result.lang == "en"
    assert result.ko_query is not None
    assert len(result.ko_query) > 0


def test_en_ko_query_none_when_no_match(analyzer):
    """FlashText 미매칭 시 ko_query=None"""
    result = analyzer.analyze("hello how are you today")
    assert result.lang == "en"
    assert result.ko_query is None


# ── 갭 4: _EN_LIMIT_KW 확장 검증 ───────────────────────────────────────────────

def test_en_limit_credits_keyword(analyzer):
    """'credits' 단독 → question_focus='limit' (갭 4 수정)"""
    result = analyzer.analyze("double major credits?")
    assert result.entities.get("question_focus") == "limit"


def test_en_limit_minimum_keyword(analyzer):
    """'minimum' → question_focus='limit'"""
    result = analyzer.analyze("what is the minimum number of credits for graduation?")
    assert result.entities.get("question_focus") == "limit"


def test_en_limit_how_many_keyword(analyzer):
    """'how many' → question_focus='limit'"""
    result = analyzer.analyze("how many courses can I take per semester?")
    assert result.entities.get("question_focus") == "limit"


# ── 갭 5: en_glossary 신규 alias 검증 ─────────────────────────────────────────

def test_en_glossary_credit_recognition(analyzer):
    """'credit transfer' → matched_terms에 '학점인정' 포함"""
    result = analyzer.analyze("how does credit transfer work at this university?")
    assert result.lang == "en"
    ko_terms = [t["ko"] for t in result.matched_terms]
    assert "학점인정" in ko_terms


def test_en_glossary_exemption(analyzer):
    """'exemption' → matched_terms에 '졸업요건 면제' 포함"""
    result = analyzer.analyze("can I get an exemption from the graduation requirement?")
    ko_terms = [t["ko"] for t in result.matched_terms]
    assert "졸업요건 면제" in ko_terms


def test_en_glossary_graduation_deferral(analyzer):
    """'graduation deferral' → matched_terms에 '졸업유보' 포함"""
    result = analyzer.analyze("how do I apply for graduation deferral?")
    ko_terms = [t["ko"] for t in result.matched_terms]
    assert "졸업유보" in ko_terms


def test_en_glossary_authorized_absence(analyzer):
    """'authorized absence' → matched_terms에 '공인결석계' 포함"""
    result = analyzer.analyze("how do I get an authorized absence?")
    ko_terms = [t["ko"] for t in result.matched_terms]
    assert "공인결석계" in ko_terms


def test_en_glossary_gpa_term(analyzer):
    """'grade point average' → matched_terms에 '평점' 포함"""
    result = analyzer.analyze("what grade point average do I need to maintain?")
    ko_terms = [t["ko"] for t in result.matched_terms]
    assert "평점" in ko_terms


def test_en_glossary_enrollment_certificate(analyzer):
    """'certificate of enrollment' → matched_terms에 '재학증명서' 포함"""
    result = analyzer.analyze("how do I get a certificate of enrollment?")
    ko_terms = [t["ko"] for t in result.matched_terms]
    assert "재학증명서" in ko_terms


# ── High 격차 해소 검증 ────────────────────────────────────────────────────────

def test_en_asks_url_entity(analyzer):
    """High #1: 'where can i apply' → entities['asks_url']=True"""
    result = analyzer.analyze("where can i apply for a leave of absence?")
    assert result.entities.get("asks_url") is True


def test_en_asks_url_which_website(analyzer):
    """High #1: 'which website' → entities['asks_url']=True"""
    result = analyzer.analyze("which website do I use to register for courses?")
    assert result.entities.get("asks_url") is True


def test_en_semester_half_first(analyzer):
    """High #2: 'first semester' → entities['semester_half']='전기'"""
    result = analyzer.analyze("when is the first semester graduation ceremony?")
    assert result.entities.get("semester_half") == "전기"


def test_en_semester_half_second(analyzer):
    """High #2: 'second semester' → entities['semester_half']='후기'"""
    result = analyzer.analyze("when is the second semester commencement?")
    assert result.entities.get("semester_half") == "후기"


def test_en_grade_sel_requires_graph_false(analyzer):
    """High #3: 'pass/fail' 질문 → requires_graph=False"""
    result = analyzer.analyze("how do I apply for pass/fail grading?")
    assert result.requires_graph is False
    assert result.requires_vector is True


def test_en_grade_sel_pnp(analyzer):
    """High #3: 'p/np' → requires_graph=False"""
    result = analyzer.analyze("can I change my course to p/np grading?")
    assert result.requires_graph is False


def test_en_grade_sel_period_keeps_graph_for_schedule(analyzer):
    result = analyzer.analyze(
        "When is the pass/fail conversion application period for the 2026 spring semester?"
    )
    assert result.intent == Intent.REGISTRATION
    assert result.requires_graph is True
    assert result.entities.get("question_focus") == "period"


def test_en_alternative_retake_question_keeps_alternative_intent(analyzer):
    result = analyzer.analyze(
        "Why is it important to verify alternative and equivalent courses before applying for a course retake?"
    )
    assert result.intent == Intent.ALTERNATIVE
    assert result.requires_graph is True


def test_en_where_class_schedule_is_location_not_period(analyzer):
    result = analyzer.analyze("Where can the class schedule be checked?")
    assert result.entities.get("question_focus") == "location"
    assert "수업시간표" in result.ko_query
    assert "신청기간" not in result.ko_query


# ── Medium 격차 해소 검증 ─────────────────────────────────────────────────────

def test_en_registration_deadline_entity(analyzer):
    """Medium #4: 'cancel + deadline' → entities['registration_deadline']=True"""
    result = analyzer.analyze("what is the deadline to cancel a course?")
    assert result.entities.get("registration_deadline") is True


def test_en_registration_deadline_drop(analyzer):
    """Medium #4: 'drop + last day' → entities['registration_deadline']=True"""
    result = analyzer.analyze("what is the last day to drop a class?")
    assert result.entities.get("registration_deadline") is True


def test_en_liberal_arts_chapel(analyzer):
    """Medium #5: 'chapel' → entities['liberal_arts_area']='인성체험교양'"""
    result = analyzer.analyze("is chapel required for graduation?")
    assert result.entities.get("liberal_arts_area") == "인성체험교양"


def test_en_liberal_arts_global_communication(analyzer):
    """Medium #5: 'global communication' → entities['liberal_arts_area']='글로벌소통역량'"""
    result = analyzer.analyze("how many global communication credits do I need?")
    assert result.entities.get("liberal_arts_area") == "글로벌소통역량"


def test_en_student_type_default_domestic(analyzer):
    """Medium #6: 학생유형 미매칭 시 기본값='내국인'"""
    result = analyzer.analyze("what are the graduation requirements?")
    assert result.student_type == "내국인"


def test_en_missing_info_student_id(analyzer):
    """Medium #7: 졸업요건 질문 + student_id 없음 → missing_info=['student_id']"""
    result = analyzer.analyze("what are the graduation requirements?")
    assert "student_id" in result.missing_info


def test_en_missing_info_empty_when_id_present(analyzer):
    """Medium #7: student_id 있으면 missing_info 비어 있음"""
    result = analyzer.analyze("what are the graduation requirements for class of 2022 students?")
    assert "student_id" not in result.missing_info


# ── Low 격차 해소 검증 ────────────────────────────────────────────────────────

def test_en_cohort_short_pattern(analyzer):
    """Low #9: '22 cohort' → student_id='2022'"""
    result = analyzer.analyze("what rules apply to the 22 cohort?")
    assert result.student_id == "2022"


def test_en_course_number_extraction(analyzer):
    """Low #10: 영문 과목 코드 추출"""
    result = analyzer.analyze("can I retake ENG1234 if I failed?")
    assert result.entities.get("course_number") == "ENG1234"


def test_en_transcript_requires_graph(analyzer):
    """Low #12: TRANSCRIPT intent → requires_graph=True"""
    result = analyzer.analyze("show me my transcript and GPA")
    assert result.requires_graph is True


def test_en_question_focus_table_lookup(analyzer):
    """Low #11: 'table' + 'cohort' → question_focus='table_lookup'"""
    result = analyzer.analyze("show me the credits table by cohort")
    assert result.entities.get("question_focus") == "table_lookup"


def test_en_question_focus_rule_list(analyzer):
    """Low #11: 'requirements' → question_focus='rule_list'"""
    result = analyzer.analyze("what are the requirements for early graduation?")
    assert result.entities.get("question_focus") == "rule_list"


def test_en_period_terms_are_added_to_ko_query(analyzer):
    result = analyzer.analyze(
        "When is the secondary major application period for the 2026 spring semester?"
    )
    assert result.entities.get("question_focus") == "period"
    assert "신청기간" in result.ko_query


def test_en_changed_starting_from_academic_year_is_not_period(analyzer):
    result = analyzer.analyze(
        "What changed in the primary major credit requirement starting from the 2026 academic year?"
    )
    assert result.entities.get("question_focus") != "period"
    assert result.intent == Intent.MAJOR_CHANGE


def test_en_leave_register_courses_prefers_registration(analyzer):
    result = analyzer.analyze("Can a student on leave of absence register for courses?")
    assert result.intent == Intent.REGISTRATION
    assert "수강신청" in result.ko_query


def test_en_plural_scholarship_aliases(analyzer):
    result = analyzer.analyze("Where do you apply for national scholarships?")
    assert result.intent == Intent.SCHOLARSHIP
    assert "국가장학금" in result.ko_query


def test_en_alternative_same_name_aliases(analyzer):
    result = analyzer.analyze(
        "If a student takes a course with the same name twice, is the credit recognized?"
    )
    assert result.intent == Intent.ALTERNATIVE
    assert "동일과목" in result.ko_query
