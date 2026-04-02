"""
그래프 데이터 입력 스크립트 (PDF 파싱 기반)

pdf_to_graph.py에서 PDF를 파싱하여 학사 그래프를 구축합니다.
하드코딩된 데이터 없이 100% PDF 기반으로 작동합니다.

사용법:
    python scripts/build_graph.py
    python scripts/build_graph.py --pdf data/pdfs/2026학년도1학기학사안내.pdf
    python scripts/build_graph.py --help
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pdf_to_graph import build_graph_from_pdf


def _add_early_graduation_data(graph) -> None:
    """
    조기졸업 안내 문서 데이터를 그래프에 추가합니다.
    데이터 출처: data/early_graduation.json (별도 공문 기반)
    """
    import json
    data_path = Path(__file__).parent.parent / "data" / "early_graduation.json"
    try:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[WARN] {data_path} 파일 없음 — 조기졸업 노드 건너뜀")
        return

    # 1. 신청자격
    if "신청자격" in data:
        graph.add_early_graduation("신청자격", data["신청자격"])

    # 2. 학번별 졸업기준
    for criteria in data.get("졸업기준", []):
        target = criteria.get("적용대상", "")
        if "2022" in target:
            graph.add_early_graduation("기준_2022이전", criteria)
        elif "2023" in target:
            graph.add_early_graduation("기준_2023이후", criteria)

    # 3. 기타사항
    if "기타사항" in data:
        graph.add_early_graduation("기타사항", data["기타사항"])

    # 4. 신청기간 학사일정
    sched = data.get("신청기간", {})
    if sched.get("시작일"):
        graph.add_schedule("조기졸업신청", sched.get("학기", ""), {
            k: v for k, v in sched.items() if k != "학기"
        })

    # 5. 엣지 연결
    graph.add_relation(
        "early_grad_신청자격", "early_grad_기준_2022이전", "신청자격적용"
    )
    graph.add_relation(
        "early_grad_신청자격", "early_grad_기준_2023이후", "신청자격적용"
    )
    if sched.get("시작일"):
        graph.add_relation(
            f"schedule_조기졸업신청_{sched.get('학기', '')}",
            "early_grad_신청자격", "기간정한다"
        )


def _add_scholarship_data(graph) -> None:
    """
    장학금 안내 데이터를 그래프에 추가합니다.
    데이터 출처: data/scholarships.json
    """
    import json
    data_path = Path(__file__).parent.parent / "data" / "scholarships.json"
    try:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[WARN] {data_path} 파일 없음 — 장학금 노드 건너뜀")
        return

    for item in data.get("scholarships", []):
        name = item.get("장학금명", "")
        if name:
            graph.add_scholarship(name, item)


def main():
    """PDF 파싱 기반 학사 그래프 구축"""
    parser = argparse.ArgumentParser(description="PDF 기반 학사 그래프 자동 구축")
    parser.add_argument(
        "--pdf",
        default="data/pdfs/2026학년도1학기학사안내.pdf",
        help="PDF 파일 경로 (기본값: data/pdfs/2026학년도1학기학사안내.pdf)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파싱만 수행, 그래프 저장 안 함",
    )
    parser.add_argument(
        "--show-pages",
        action="store_true",
        help="섹션별 페이지 번호 출력 후 종료",
    )

    args = parser.parse_args()
    pdf_path = str(Path(args.pdf).resolve())

    print("=" * 60)
    print("BUFS 학사 그래프 구축 (PDF 파싱 기반)")
    print("=" * 60)
    print(f"PDF: {pdf_path}")
    print()

    # pdf_to_graph.py에서 직접 파싱 및 구축
    result = build_graph_from_pdf(
        pdf_path,
        dry_run=args.dry_run,
        show_pages=args.show_pages,
    )

    if result is None:
        return

    # ── 조기졸업 데이터 보완 (별도 공문 기반 하드코딩) ──────────
    if not args.dry_run:
        _add_early_graduation_data(result)
        print("조기졸업 노드/엣지 추가 완료")
        _add_scholarship_data(result)
        result.save()
        print("장학금 노드 추가 완료")

    print()
    print("=" * 60)
    print(f"완료: {result.G.number_of_nodes()}개 노드, {result.G.number_of_edges()}개 엣지")
    if not args.dry_run:
        print(f"저장 위치: {result.path}")
    print("=" * 60)

    # 타입별 통계
    from collections import Counter

    type_counts = Counter(
        data.get("type") for _, data in result.G.nodes(data=True)
    )
    print("[노드 타입별 통계]")
    for ntype, count in sorted(type_counts.items(), key=lambda x: x[0] or ""):
        print(f"  {ntype}: {count}개")


if __name__ == "__main__":
    main()
