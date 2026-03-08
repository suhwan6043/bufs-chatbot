"""PDF 그래프 파서 테스트"""

from scripts.pdf_to_graph import parse_schedule_table


def test_parse_schedule_table_keeps_spring_year():
    table = "\n".join(
        [
            "| 월 | 일 | 내용 |",
            "| --- | --- | --- |",
            "| 3 | 2(월) | 개강 |",
            "| 5 | 18(월) ~ 29(금) | 제1·2전공 신청 및 변경(전과) |",
        ]
    )

    events = parse_schedule_table(
        table_md=table,
        base_year=2026,
        semester_start_month=3,
    )

    assert events[0]["시작일"] == "2026-03-02"
    assert events[1]["시작일"] == "2026-05-18"
    assert events[1]["종료일"] == "2026-05-29"
