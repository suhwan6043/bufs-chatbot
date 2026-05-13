"""학사 그래프 직접 응답 테스트"""

import networkx as nx

from app.graphdb.academic_graph import AcademicGraph


def make_graph():
    graph = AcademicGraph(graph_path="data/graphs/academic_graph.pkl")
    graph.G = nx.DiGraph()
    return graph


def test_registration_gpa_exception_returns_direct_answer():
    graph = make_graph()
    graph.add_registration_rule(
        "2023이후",
        {
            "평점4이상최대학점": 21,
            "장바구니최대학점": 30,
        },
    )

    results = graph.query_to_search_results(
        student_id="2023",
        intent="REGISTRATION",
        entities={"gpa_exception": True},
        question="2023학번 이후 학생이 직전학기 평점 4.0 이상이면 최대 몇 학점까지 신청할 수 있는가?",
    )

    assert results
    assert "21학점" in results[0].metadata["direct_answer"]


def test_schedule_match_returns_direct_answer():
    graph = make_graph()
    graph.add_schedule(
        "2024~2025학번 제1, 2전공 신청 및 변경(전과)",
        "2026-1",
        {"시작일": "2026-05-18", "종료일": "2026-05-29", "비고": ""},
    )

    results = graph.query_to_search_results(
        student_id="2024",
        intent="SCHEDULE",
        entities={"student_groups": ["2024_2025"]},
        question="2024~2025학번의 제1·2전공 신청 및 변경(전과) 기간은 언제인가?",
    )

    assert results
    assert "2026년 5월 18일부터 5월 29일까지" in results[0].metadata["direct_answer"]


def test_period_focus_topic_intent_includes_schedule_result():
    graph = make_graph()
    graph.add_schedule(
        "온라인 휴/복학 신청",
        "2026-2",
        {"시작일": "2026-07-06", "종료일": "2026-08-30", "비고": ""},
    )

    results = graph.query_to_search_results(
        student_id="2023",
        intent="LEAVE_OF_ABSENCE",
        entities={"question_focus": "period"},
        question="휴학 복학 신청기간 학사일정",
    )

    assert results
    assert "온라인 휴/복학 신청" in results[0].text
    assert "2026년 7월 6일부터 8월 30일까지" in results[0].metadata["direct_answer"]


def test_static_scholarship_result_uses_fallback_source_pages():
    graph = make_graph()
    graph.add_scholarship(
        "국가장학금",
        {
            "신청방법": "한국장학재단 홈페이지(www.kosaf.go.kr)에서 신청",
            "신청처": "https://www.kosaf.go.kr",
        },
    )

    results = graph.query_to_search_results(
        student_id="2024",
        intent="SCHOLARSHIP",
        entities={},
        question="국가장학금은 어디에서 신청하나요?",
    )

    assert results
    assert results[0].page_number == 7
    assert results[0].metadata["source_pages"] == [7, 8]


def test_static_early_graduation_result_uses_section_source_page():
    graph = make_graph()
    graph.add_early_graduation(
        "기타사항",
        {
            "내용": "조기졸업 대상자는 6학기 또는 7학기에 졸업할 수 있다.",
        },
    )

    results = graph.query_to_search_results(
        student_id="2024",
        intent="EARLY_GRADUATION",
        entities={},
        question="조기졸업이란 무엇인가?",
    )

    assert results
    assert results[0].page_number == 48


def test_major_method_result_prefers_precise_source_pages():
    graph = make_graph()
    graph.add_major_method(
        "방법1",
        "2022",
        {
            "설명": "주전공+복수전공",
            "주전공학점": 36,
            "제2전공학점": 30,
            "_source_pages": [3, 13, 18],
        },
    )

    results = graph.query_to_search_results(
        student_id="2022",
        intent="MAJOR_CHANGE",
        entities={"major_method": "방법1"},
        question="2022학번 방법1 주전공과 제2전공 학점",
    )

    assert results
    assert results[0].page_number == 39
    assert results[0].metadata["source_pages"][:2] == [39, 40]


def test_leave_student_registration_direct_answer_has_source_page():
    graph = make_graph()
    graph.add_registration_rule(
        "2023이후",
        {
            "최대신청학점": 18,
            "_source_pages": [8],
        },
    )

    results = graph.query_to_search_results(
        student_id="2023",
        intent="REGISTRATION",
        entities={},
        question="휴학 중인 학생은 수강신청 가능한가요?",
    )

    assert results
    assert results[0].page_number == 9


def test_static_faq_result_uses_alternative_source_pages():
    graph = make_graph()
    graph.add_faq_node(
        "FAQ-0027",
        "재수강을 하려고 하는데, 대체과목과 동일과목의 차이점이 뭔가요?",
        "동일과목은 중복하여 수강할 수 없고, 대체과목은 재수강도 가능하고 중복수강도 가능합니다.",
        "성적/시험",
    )

    results = graph.query_to_search_results(
        student_id="2023",
        intent="ALTERNATIVE",
        entities={},
        question="대체과목 동일과목 차이",
    )

    assert results
    assert results[0].page_number == 46


def test_graduation_comparison_returns_direct_answer():
    graph = make_graph()
    graph.add_graduation_req("2024_2025", "내국인", {"복수전공이수학점": 30})
    graph.add_graduation_req("2023", "내국인", {"복수전공이수학점": 27})
    graph.add_graduation_req("2022", "내국인", {"복수전공이수학점": 30})
    graph.add_graduation_req("2021", "내국인", {"복수전공이수학점": 33})

    results = graph.query_to_search_results(
        student_id="2024",
        intent="GRADUATION_REQ",
        entities={
            "student_groups": ["2024_2025", "2023", "2022", "2021"],
            "second_major_credits": True,
        },
        question="2024학번 이후, 2023학번, 2022학번, 2021학번의 복수전공 이수학점은 각각 얼마인가?",
    )

    assert results
    answer = results[0].metadata["direct_answer"]
    assert "2024학번 이후 30학점" in answer
    assert "2023학번 27학점" in answer
    assert "2021학번 33학점" in answer
