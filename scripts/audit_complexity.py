"""Step 1 — Cyclomatic complexity Top 20 (ast 기반, radon 대체).

각 함수의 CC = 1 + (if + elif + for + while + and + or + try + except + boolop branch).
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXCLUDE = {".claude", "__pycache__", "node_modules", ".git", "data", "reports"}


def cc(node: ast.AST) -> int:
    """Cyclomatic complexity for a function node."""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.AsyncFor)):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.ExceptHandler):
            score += 1
        elif isinstance(child, (ast.IfExp,)):  # ternary
            score += 1
    return score


def is_excl(p: Path) -> bool:
    try:
        rel_parts = p.relative_to(ROOT).parts
    except ValueError:
        rel_parts = p.parts
    return any(part in EXCLUDE for part in rel_parts)


def main() -> int:
    results = []
    for path in ROOT.rglob("*.py"):
        if is_excl(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                loc = end - node.lineno + 1
                c = cc(node)
                results.append({
                    "file": rel,
                    "lineno": node.lineno,
                    "end_lineno": end,
                    "name": node.name,
                    "loc": loc,
                    "cc": c,
                })

    # Top 20 by complexity
    by_cc = sorted(results, key=lambda r: (-r["cc"], -r["loc"]))[:20]
    print("# Cyclomatic Complexity Top 20")
    print(f"# total functions: {len(results)}")
    print(f"# average cc: {sum(r['cc'] for r in results)/len(results):.1f}" if results else "")
    print(f"# functions with cc >= 10: {sum(1 for r in results if r['cc'] >= 10)}")
    print(f"# functions with cc >= 20: {sum(1 for r in results if r['cc'] >= 20)}")
    print()
    print(f'{"#":<3} {"file":<55} {"line":<6} {"loc":<5} {"cc":<4} name')
    print("-" * 110)
    for i, r in enumerate(by_cc, 1):
        print(f'{i:<3} {r["file"]:<55} {r["lineno"]:<6} {r["loc"]:<5} {r["cc"]:<4} {r["name"]}')

    # JSON 출력 (별도 파일에 저장)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if out:
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[saved] {out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
