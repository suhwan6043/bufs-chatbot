#!/usr/bin/env python3
# REGISTRATION schedule fan-out 운영 측정 분석.
#
# 운영에서 세 플래그(REG_SCHEDULE_GATE / REG_SCHEDULE_FANOUT /
# SCHEDULE_GATE_MODE=label) on으로 며칠 측정한 뒤 디폴트 전환 결정 전,
# data/logs/chat_*.jsonl을 읽어 세 항목을 출력한다.
#
# (M1) 게이트 발동 빈도 — fan-out 통합 본문 (a) vs 단일 학년 폴백 (b)
# (M2) (M1-a) 발동 질문 전수 덤프 — 패턴 필터링 없이 사람 검수
# (M3) (M1-a) 본문 깨끗함 검수 — raw + repr (줄바꿈/정렬/라벨 위치 시각 확인)
#
# 사용: python scripts/inspect_reg_schedule_traffic.py [--since YYYY-MM-DD]
# 읽기 전용 — 로그 파일 수정·이동 없음.

import argparse
import glob
import json
import re
from pathlib import Path

LOG_GLOB = "data/logs/chat_*.jsonl"

# fan-out 통합 본문 — "수강신청 기간:" 헤더 + 다음 줄 "·" 라인 시작.
# academic_graph._query_registration의 통합 본문 합성 형식과 일치.
FANOUT_RE = re.compile(r"^수강신청 기간:\s*\n·", re.MULTILINE)
# 단일 학년 폴백 — "수강신청_<학년라벨> 기간은 ..." (1학년/2학년/3,4학년/전학년).
SINGLE_RE = re.compile(r"^수강신청_\S+ 기간은")


def load_rows(since: str | None) -> list[dict]:
    rows: list[dict] = []
    for fp in sorted(glob.glob(LOG_GLOB)):
        for line in open(fp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since and (d.get("timestamp", "") < since):
                continue
            rows.append(d)
    return rows


def is_fanout(answer: str) -> bool:
    return bool(FANOUT_RE.search(answer or ""))


def is_single_grade(answer: str) -> bool:
    return bool(SINGLE_RE.match(answer or ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--since",
        help="YYYY-MM-DD 이후 timestamp만 분석 (기본: 전체)",
        default=None,
    )
    args = ap.parse_args()

    log_files = sorted(glob.glob(LOG_GLOB))
    if not log_files:
        print(f"로그 파일 없음: {LOG_GLOB}")
        return

    rows = load_rows(args.since)
    print(f"분석 대상 로그 {len(log_files)}개 파일, {len(rows)}건  "
          f"(since={args.since or '전체'})")
    print(f"파일: {', '.join(Path(fp).name for fp in log_files)}")
    print()

    reg_rows = [r for r in rows if r.get("intent") == "REGISTRATION"]
    fanout_rows = [r for r in reg_rows if is_fanout(r.get("answer", ""))]
    single_rows = [r for r in reg_rows if is_single_grade(r.get("answer", ""))]

    # ── (M1) ─────────────────────────────────────────────────────────────
    print("━" * 72)
    print("(M1) 게이트 발동 빈도")
    print("━" * 72)
    print(f"  REGISTRATION 전체:        {len(reg_rows):>4}건")
    if reg_rows:
        ratio_a = 100.0 * len(fanout_rows) / len(reg_rows)
        ratio_b = 100.0 * len(single_rows) / len(reg_rows)
    else:
        ratio_a = ratio_b = 0.0
    print(f"  (a) fan-out 통합 본문:    {len(fanout_rows):>4}건 ({ratio_a:5.1f}%)")
    print(f"  (b) 단일 학년 폴백:       {len(single_rows):>4}건 ({ratio_b:5.1f}%)")
    print(f"  (그 외 REGISTRATION):     {len(reg_rows) - len(fanout_rows) - len(single_rows):>4}건")
    print()

    # ── (M2) ─────────────────────────────────────────────────────────────
    print("━" * 72)
    print("(M2) fan-out 발동 질문 전수 덤프 — 패턴 필터링 없음, 사람 검수")
    print("━" * 72)
    if not fanout_rows:
        print("  fan-out 발동 0건 — 덤프 없음")
        print("  (운영에서 세 플래그 on 후 며칠 쌓이면 다시 호출)")
    else:
        groups: dict[str, list[str]] = {}
        for r in fanout_rows:
            q = (r.get("question") or "").strip()
            a_first = (r.get("answer") or "").split("\n", 1)[0]
            groups.setdefault(q, []).append(a_first)
        for q, ans_list in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print(f"  [{len(ans_list):>3}회] {q!r}")
            uniq_ans = sorted(set(ans_list))
            for a in uniq_ans[:3]:
                print(f"          → {a}")
            if len(uniq_ans) > 3:
                print(f"          → ... ({len(uniq_ans) - 3}개 답변 변형 더)")
    print()

    # ── (M3) ─────────────────────────────────────────────────────────────
    print("━" * 72)
    print("(M3) fan-out 본문 깨끗함 검수 — raw + repr")
    print("━" * 72)
    if not fanout_rows:
        print("  fan-out 발동 0건 — 덤프 없음")
        return

    seen_bodies: dict[str, list[str]] = {}
    for r in fanout_rows:
        a = r.get("answer", "")
        seen_bodies.setdefault(a, []).append(r.get("question", ""))
    for i, (body, qs) in enumerate(
        sorted(seen_bodies.items(), key=lambda kv: -len(kv[1])), 1
    ):
        unique_qs = sorted(set(qs))
        print(f"  [#{i}] 노출 {len(qs)}회, unique 질문 {len(unique_qs)}개")
        if len(unique_qs) <= 3:
            for uq in unique_qs:
                print(f"        질문: {uq!r}")
        else:
            for uq in unique_qs[:3]:
                print(f"        질문: {uq!r}")
            print(f"        질문: ... ({len(unique_qs) - 3}개 더)")
        print("  ─── raw ───")
        for ln in body.split("\n"):
            print(f"    {ln}")
        print("  ─── repr (\\n 위치 / 빈 줄 / 라벨 결합 확인) ───")
        print(f"    {body!r}")
        print()


if __name__ == "__main__":
    main()
