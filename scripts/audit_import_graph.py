"""Step 6 — pydeps 대체: ast 기반 import 그래프 + 순환 import 탐지.

각 .py의 from/import 문에서 자기 프로젝트 모듈만 추출 → DOT 형식 출력.
graphviz 설치되어 있으면 svg 생성, 아니면 DOT 텍스트만.
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXCLUDE = {".claude", "__pycache__", "node_modules", ".git", "data", "reports"}
PROJECT_PREFIXES = ("app", "backend", "scripts", "pages", "evaluation")


def is_excl(p: Path) -> bool:
    try:
        return any(part in EXCLUDE for part in p.relative_to(ROOT).parts)
    except ValueError:
        return any(part in EXCLUDE for part in p.parts)


def mod_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    return ".".join(rel.parts)


def collect_edges():
    edges = defaultdict(set)  # caller_mod -> set(target_mod)
    modules = set()
    for path in ROOT.rglob("*.py"):
        if is_excl(path):
            continue
        m = mod_name(path)
        modules.add(m)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    edges[m].add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    edges[m].add(alias.name)
    return edges, modules


def filter_project(edges, modules):
    """프로젝트 모듈만 남기기. 'app.pipeline.foo' 형태로 정규화."""
    def to_project(mod: str) -> str | None:
        # 'app', 'app.pipeline' 등은 그대로
        if mod.startswith(PROJECT_PREFIXES):
            return mod
        return None

    filtered = defaultdict(set)
    for src, dsts in edges.items():
        for d in dsts:
            t = to_project(d)
            if t:
                filtered[src].add(t)
    return filtered


def find_cycles(graph):
    """Tarjan SCC로 사이클 탐지. 자기참조도 포함."""
    index = [0]
    stack = []
    on_stack = set()
    indices = {}
    lowlink = {}
    sccs = []

    def strongconnect(v):
        indices[v] = index[0]
        lowlink[v] = index[0]
        index[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, []):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            scc = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1 or (len(scc) == 1 and scc[0] in graph.get(scc[0], [])):
                sccs.append(scc)

    sys.setrecursionlimit(10000)
    for v in list(graph.keys()):
        if v not in indices:
            strongconnect(v)
    return sccs


def to_dot(edges, *, top_pkg_only=True):
    """DOT 형식 출력. top_pkg_only=True면 'app.pipeline.x' → 'app.pipeline'으로 압축."""
    if top_pkg_only:
        compressed = defaultdict(set)
        for src, dsts in edges.items():
            s_pkg = ".".join(src.split(".")[:2])
            for d in dsts:
                d_pkg = ".".join(d.split(".")[:2])
                if s_pkg != d_pkg:
                    compressed[s_pkg].add(d_pkg)
        edges = compressed

    lines = ["digraph imports {", '  rankdir=LR;', '  node [shape=box, fontsize=10];']
    for src, dsts in sorted(edges.items()):
        for d in sorted(dsts):
            lines.append(f'  "{src}" -> "{d}";')
    lines.append("}")
    return "\n".join(lines)


def main():
    edges, modules = collect_edges()
    project_edges = filter_project(edges, modules)
    cycles = find_cycles(project_edges)
    print(f"# {len(modules)} project modules", file=sys.stderr)
    print(f"# {sum(len(v) for v in project_edges.values())} internal import edges", file=sys.stderr)
    print(f"# {len(cycles)} cycles found", file=sys.stderr)
    if cycles:
        for c in cycles[:10]:
            print(f"  cycle: {' -> '.join(c)} -> {c[0]}", file=sys.stderr)
    dot_full = to_dot(project_edges, top_pkg_only=False)
    dot_top = to_dot(project_edges, top_pkg_only=True)

    if len(sys.argv) >= 2:
        out_dir = Path(sys.argv[1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "import_graph_full.dot").write_text(dot_full, encoding="utf-8")
        (out_dir / "import_graph_top.dot").write_text(dot_top, encoding="utf-8")
        # cycles report
        (out_dir / "cycles.txt").write_text(
            "\n".join(" -> ".join(c) + f" -> {c[0]}" for c in cycles) or "(no cycles)",
            encoding="utf-8",
        )
        print(f"[saved] {out_dir}/import_graph_full.dot, import_graph_top.dot, cycles.txt", file=sys.stderr)
    else:
        print(dot_top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
