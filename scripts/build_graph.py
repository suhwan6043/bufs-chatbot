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
    출처: 2025학년도 2학기 조기졸업 신청 안내 (교무처장)

    PDF 기반 자동 파싱 대상이 아닌 별도 공문이므로 직접 입력합니다.
    """
    # ── 1. 신청자격 노드 ──────────────────────────────────────
    graph.add_early_graduation("신청자격", {
        "신청학기": "6학기 또는 7학기 등록 재학생",
        "편입생_신청불가": True,
        "평점기준_2005이전": "4.0 이상",
        "평점기준_2006":     "4.2 이상",
        "평점기준_2007이후": "4.3 이상",
        "글로벌미래융합학부": "별도기준 적용",
    })

    # ── 2. 학번별 졸업기준 노드 ───────────────────────────────
    _이수조건 = (
        "각 영역별(교양, 전공 등) 이수학점 취득 / "
        "졸업 전공시험(졸업논문) 합격 / "
        "기타 졸업인증 등 학번별 졸업요건 충족"
    )
    graph.add_early_graduation("기준_2022이전", {
        "적용대상": "2022학번 이전",
        "기준학점": 130,
        "이수조건": _이수조건,
    })
    graph.add_early_graduation("기준_2023이후", {
        "적용대상": "2023학번 이후",
        "기준학점": 120,
        "비고": (
            "2025학년도 2학기 취득 예정학점까지 포함 "
            "(동계학기 이수 예정 학점 제외)"
        ),
        "이수조건": _이수조건,
    })

    # ── 3. 기타사항 노드 ──────────────────────────────────────
    graph.add_early_graduation("기타사항", {
        "탈락자처리": "전어학기 등록금 납부, 수강신청 및 학점이수 필수",
        "합격자졸업유예": "신청 불가 (졸업합격자로 유예대상 아님)",
        "7학기등록주의": (
            "7학기 등록 학생은 대상 학기(7학기차) 지정된 신청기간 내에 신청 필수. "
            "기간 내 미신청 시 조기졸업 불가, 해당 학기는 이수 완료 학기로 처리됨"
        ),
    })

    # ── 4. 신청기간 학사일정 노드 ─────────────────────────────
    graph.add_schedule("조기졸업신청", "2025-2", {
        "시작일": "2025-11-19",
        "종료일": "2025-11-25",
        "신청방법": (
            "학생포털시스템(https://m.bufs.ac.kr) → 로그인 → 졸업 → 조기졸업 신청/조회"
        ),
    })

    # ── 5. 엣지 연결 ──────────────────────────────────────────
    # 신청자격 → 각 학번 기준 (신청자격적용)
    graph.add_relation(
        "early_grad_신청자격", "early_grad_기준_2022이전", "신청자격적용"
    )
    graph.add_relation(
        "early_grad_신청자격", "early_grad_기준_2023이후", "신청자격적용"
    )
    # 학사일정(신청기간) → 신청자격 (기간정한다)
    graph.add_relation(
        "schedule_조기졸업신청_2025-2", "early_grad_신청자격", "기간정한다"
    )


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
        result.save()
        print("조기졸업 노드/엣지 추가 완료")

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
