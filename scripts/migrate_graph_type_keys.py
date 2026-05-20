"""
그래프 메타 키 마이그레이션 (P0.1 픽스, 2026-05-18)
=====================================================

문제:
  - `AcademicGraph.add_*()` 메서드가 노드 속성에 `"type"`만 저장 → 외부 코드가
    `"node_type"`으로 조회하면 None 반환 (진단 문서: 100% None).
  - `add_relation()`이 엣지 속성에 `"relation"`만 저장 → `"edge_type"` 조회 시
    100% MISSING/"unknown".

해결:
  - 기존 `.pkl`을 그대로 로드해서 모든 노드의 `type` → `node_type` 미러링,
    모든 엣지의 `relation` → `edge_type` 미러링 후 저장.
  - PDF 재파싱·FAQ 재인제스트 불필요 (수십 초 내 완료).
  - 백업 파일을 함께 생성하여 롤백 안전.

사용법:
    python scripts/migrate_graph_type_keys.py                     # data/graphs/academic_graph.pkl 마이그레이션
    python scripts/migrate_graph_type_keys.py --dry-run           # 변경 사항만 보고
    python scripts/migrate_graph_type_keys.py --path other.pkl    # 다른 경로 지정

원칙 4 준수: 경로는 .env / app/config.py의 GraphConfig.path를 SSOT로 사용.
"""

from __future__ import annotations

import argparse
import io
import pickle
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Windows cp949 한글 깨짐 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _resolve_default_path() -> Path:
    """app/config.py 의 GraphConfig 가 단일 진실원천. 실패 시 관행적 경로."""
    try:
        from app.config import settings  # noqa: E402
        gp = getattr(settings, "graph", None)
        if gp is not None and getattr(gp, "path", None):
            return Path(gp.path)
    except Exception:
        pass
    return ROOT / "data" / "graphs" / "academic_graph.pkl"


def _migrate_in_memory(g_or_nx):
    """노드·엣지 속성에 node_type / edge_type 미러링.

    AcademicGraph 인스턴스든 raw networkx 그래프든 모두 처리.
    반환값: (그대로 저장 가능한 객체, stats dict)
    """
    G = getattr(g_or_nx, "G", g_or_nx)

    node_changed = 0
    node_already_ok = 0
    for nid, attrs in G.nodes(data=True):
        tval = attrs.get("type") or attrs.get("node_type")
        if not tval:
            continue
        before = (attrs.get("type"), attrs.get("node_type"))
        attrs["type"] = tval
        attrs["node_type"] = tval
        if before == (tval, tval):
            node_already_ok += 1
        else:
            node_changed += 1

    edge_changed = 0
    edge_already_ok = 0
    for u, v, attrs in G.edges(data=True):
        eval_ = attrs.get("edge_type") or attrs.get("relation")
        if not eval_:
            continue
        before = (attrs.get("relation"), attrs.get("edge_type"))
        attrs["relation"] = eval_
        attrs["edge_type"] = eval_
        if before == (eval_, eval_):
            edge_already_ok += 1
        else:
            edge_changed += 1

    return g_or_nx, {
        "node_changed": node_changed,
        "node_already_ok": node_already_ok,
        "edge_changed": edge_changed,
        "edge_already_ok": edge_already_ok,
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
    }


def _summary_types(g_or_nx) -> tuple[Counter, Counter]:
    G = getattr(g_or_nx, "G", g_or_nx)
    node_types = Counter(d.get("node_type", "MISSING") for _, d in G.nodes(data=True))
    edge_types = Counter(d.get("edge_type", "MISSING") for _, _, d in G.edges(data=True))
    return node_types, edge_types


def main() -> int:
    parser = argparse.ArgumentParser(description="그래프 type 키 마이그레이션 (P0.1)")
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="대상 .pkl 경로. 미지정 시 app.config.settings.graph.path 사용",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="변경 사항만 출력하고 파일은 그대로 둠",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="원본을 .bak으로 백업하지 않음 (권장: 사용하지 말 것)",
    )
    args = parser.parse_args()

    path = args.path or _resolve_default_path()
    if not path.exists():
        print(f"FAIL: 그래프 파일 없음 → {path}")
        return 1

    print(f"대상: {path}  ({path.stat().st_size:,} bytes)")
    with path.open("rb") as f:
        g_or_nx = pickle.load(f)

    nt_before, et_before = _summary_types(g_or_nx)
    print("\n[before] node_type 분포 (상위 8):")
    for k, c in nt_before.most_common(8):
        print(f"  {k!r}: {c}")
    print("[before] edge_type 분포 (상위 8):")
    for k, c in et_before.most_common(8):
        print(f"  {k!r}: {c}")

    g_or_nx, stats = _migrate_in_memory(g_or_nx)
    print(
        f"\n변경: 노드 {stats['node_changed']}/{stats['total_nodes']} 업데이트 "
        f"(이미 정상 {stats['node_already_ok']}), "
        f"엣지 {stats['edge_changed']}/{stats['total_edges']} 업데이트 "
        f"(이미 정상 {stats['edge_already_ok']})"
    )

    nt_after, et_after = _summary_types(g_or_nx)
    print("\n[after] node_type 분포 (상위 8):")
    for k, c in nt_after.most_common(8):
        print(f"  {k!r}: {c}")
    print("[after] edge_type 분포 (상위 8):")
    for k, c in et_after.most_common(8):
        print(f"  {k!r}: {c}")

    missing_node = nt_after.get("MISSING", 0)
    missing_edge = et_after.get("MISSING", 0)
    if missing_node:
        print(f"\n⚠ 마이그레이션 후에도 node_type 누락 {missing_node}개 — type 키 자체가 비어있는 노드")
    if missing_edge:
        print(f"⚠ 마이그레이션 후에도 edge_type 누락 {missing_edge}개 — relation 키 자체가 비어있는 엣지")

    if args.dry_run:
        print("\n--dry-run: 저장 안 함")
        return 0

    if not args.no_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = path.with_suffix(path.suffix + f".bak-{ts}")
        shutil.copy2(path, bak)
        print(f"백업: {bak}")

    # AcademicGraph 인스턴스에 .save() 가 있으면 그걸 사용, 아니면 raw pickle dump
    save_fn = getattr(g_or_nx, "save", None)
    if callable(save_fn):
        save_fn()
        print(f"저장 (AcademicGraph.save): {path}")
    else:
        with path.open("wb") as f:
            pickle.dump(g_or_nx, f)
        print(f"저장 (pickle.dump): {path}")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
