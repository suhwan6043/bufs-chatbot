"""Step 1 — 디렉터리별 LOC 매트릭스 (radon 없이 ast/wc 기반).

출력: CSV (디렉터리, 파일 수, 총 LOC, 평균 LOC, 코드 LOC, 주석 LOC, 빈 줄)
"""
from __future__ import annotations

import ast
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXCLUDE_DIRS = {".claude", "__pycache__", "node_modules", ".git", "data", "reports"}


def is_excluded(p: Path) -> bool:
    try:
        rel_parts = p.relative_to(ROOT).parts
    except ValueError:
        rel_parts = p.parts
    return any(part in EXCLUDE_DIRS for part in rel_parts)


def count_lines(path: Path) -> tuple[int, int, int, int]:
    """returns (total, code, comment, blank)"""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0, 0, 0, 0
    total = code = comment = blank = 0
    in_docstring = False
    for line in txt.splitlines():
        total += 1
        s = line.strip()
        if not s:
            blank += 1
        elif s.startswith("#"):
            comment += 1
        elif s.startswith('"""') or s.startswith("'''"):
            # 단순 휴리스틱: docstring 시작/끝
            if in_docstring:
                in_docstring = False
            else:
                if not (s.endswith('"""') and len(s) > 3) and not (s.endswith("'''") and len(s) > 3):
                    in_docstring = True
            comment += 1
        elif in_docstring:
            comment += 1
        else:
            code += 1
    return total, code, comment, blank


def main() -> int:
    by_dir: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0, 0])  # files, total, code, comment, blank

    for path in ROOT.rglob("*.py"):
        if is_excluded(path):
            continue
        rel = path.relative_to(ROOT)
        # 디렉터리: 첫 두 레벨 (app/pipeline, backend/routers, scripts 등)
        parts = rel.parts
        if len(parts) >= 3:
            dir_key = f"{parts[0]}/{parts[1]}"
        elif len(parts) == 2:
            dir_key = parts[0]
        else:
            dir_key = "(root)"
        t, c, cm, b = count_lines(path)
        by_dir[dir_key][0] += 1
        by_dir[dir_key][1] += t
        by_dir[dir_key][2] += c
        by_dir[dir_key][3] += cm
        by_dir[dir_key][4] += b

    # CSV 출력
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["directory", "files", "total_loc", "code_loc", "comment_loc", "blank_loc", "avg_loc"])
    rows = sorted(by_dir.items(), key=lambda x: -x[1][1])
    for d, (n, t, c, cm, b) in rows:
        avg = t / n if n else 0
        w.writerow([d, n, t, c, cm, b, f"{avg:.0f}"])

    # stderr에 요약
    total_files = sum(v[0] for v in by_dir.values())
    total_loc = sum(v[1] for v in by_dir.values())
    print(f"\n[summary] files={total_files} total_loc={total_loc} dirs={len(by_dir)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
