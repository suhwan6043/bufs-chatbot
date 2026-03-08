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
