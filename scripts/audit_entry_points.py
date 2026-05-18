"""Step 2 — 진입점 추적: chat.py 등 단일 모듈의 함수 좌표 + 함수 간 호출 그래프 추출.

산출: JSON Lines
  [
    {
      "name": "chat_stream",
      "lineno": 469,
      "end_lineno": 513,
      "loc": 45,
      "cc": 76,
      "calls": ["_resolve_user_id", "_format_contact_answer", ...],
      "external_calls": ["session_store.get_or_create", "router_inst.route_and_search", ...]
    },
    ...
  ]
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def cyclomatic(node: ast.AST) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.AsyncFor)):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.ExceptHandler):
            score += 1
        elif isinstance(child, ast.IfExp):
            score += 1
    return score


def extract_calls(node: ast.AST) -> tuple[list[str], list[str]]:
    """함수 호출 추출 (내부 호출 = 단순 Name, 외부 호출 = Attribute chain)."""
    internal: set[str] = set()
    external: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        f = child.func
        if isinstance(f, ast.Name):
            internal.add(f.id)
        elif isinstance(f, ast.Attribute):
            # foo.bar.baz() → "foo.bar.baz"
            parts: list[str] = []
            cur: ast.AST = f
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            external.add(".".join(reversed(parts)))
    return sorted(internal), sorted(external)


def collect(path: Path) -> list[dict]:
    src = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    rows: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        internal, external = extract_calls(node)
        rows.append({
            "name": node.name,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "lineno": node.lineno,
            "end_lineno": end,
            "loc": end - node.lineno + 1,
            "cc": cyclomatic(node),
            "internal_calls": internal,
            "external_calls": external,
        })
    return rows


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: audit_entry_points.py <file.py> [<out.json>]", file=sys.stderr)
        return 2
    target = Path(argv[1])
    rows = collect(target)
    out_text = json.dumps(rows, ensure_ascii=False, indent=2)
    if len(argv) >= 3:
        Path(argv[2]).write_text(out_text, encoding="utf-8")
        print(f"[saved] {argv[2]} ({len(rows)} functions)", file=sys.stderr)
    else:
        print(out_text)

    # 텍스트 요약 — stdout
    print(f"\n# {target} — {len(rows)} functions", file=sys.stderr)
    for r in sorted(rows, key=lambda x: x["lineno"]):
        kind = "async " if r["is_async"] else ""
        print(
            f"{r['lineno']:>4}:{r['end_lineno']:<4} loc={r['loc']:>4} cc={r['cc']:>3}  "
            f"{kind}{r['name']}  internal={len(r['internal_calls'])} external={len(r['external_calls'])}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
