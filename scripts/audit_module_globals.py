"""Step 6 — 모듈 상단 _name 변수 ast 추출 (글로벌 싱글톤 후보).

각 .py 파일의 모듈 레벨에서 `_name = ...` 패턴을 찾아 위치 + 초기값 종류를
JSON으로 저장. 글로벌 캐시·싱글톤·락 등을 식별.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXCLUDE = {".claude", "__pycache__", "node_modules", ".git", "data", "reports"}


def is_excl(p: Path) -> bool:
    try:
        rel_parts = p.relative_to(ROOT).parts
    except ValueError:
        rel_parts = p.parts
    return any(part in EXCLUDE for part in rel_parts)


def describe_value(v: ast.AST) -> str:
    if isinstance(v, ast.Constant):
        if v.value is None:
            return "None"
        return f"const:{type(v.value).__name__}"
    if isinstance(v, ast.Call):
        f = v.func
        if isinstance(f, ast.Name):
            return f"call:{f.id}()"
        if isinstance(f, ast.Attribute):
            parts = []
            cur = f
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            return "call:" + ".".join(reversed(parts)) + "()"
        return "call:?()"
    if isinstance(v, ast.Dict):
        return f"dict[{len(v.keys)}]"
    if isinstance(v, ast.List):
        return f"list[{len(v.elts)}]"
    if isinstance(v, ast.Set):
        return f"set[{len(v.elts)}]"
    if isinstance(v, ast.Lambda):
        return "lambda"
    return type(v).__name__


def collect():
    rows = []
    for path in ROOT.rglob("*.py"):
        if is_excl(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for node in tree.body:
            # 모듈 레벨만 — 함수/클래스 안은 제외
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.startswith("_"):
                        rows.append({
                            "file": rel,
                            "line": node.lineno,
                            "name": t.id,
                            "value_kind": describe_value(node.value),
                        })
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id.startswith("_"):
                    rows.append({
                        "file": rel,
                        "line": node.lineno,
                        "name": node.target.id,
                        "value_kind": describe_value(node.value) if node.value else "ann_only",
                    })
    return rows


def main():
    rows = collect()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[saved] {out} ({len(rows)} module-level _name globals)", file=sys.stderr)
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))

    print(f"\n# {len(rows)} module-level globals (_name)", file=sys.stderr)
    # 싱글톤 후보 (None 초기화 + 락이 있는 경우)
    by_file = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r["name"])
    for f, names in sorted(by_file.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"  {f}: {names}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
