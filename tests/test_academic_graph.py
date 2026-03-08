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
