"""그래프 허브 fan-out 분할 포스트프로세싱.

작업 3 (2026-04-18): `grad_*`·`reg_*` 등 허브 노드가 많은 `조건` 자식을 가질 때,
조건의 `원본키` 필드 기준으로 sub-hub 계층을 삽입하여 fan-out을 줄인다.

변환 전:
    grad_2023_내국인 --[조건/제약한다/요구한다/면제_적용]--> cond_*

변환 후:
    grad_2023_내국인 --[카테고리]--> subhub_{원본키}_grad_2023_내국인
                                        --[원래 relation]--> cond_*

원칙:
- 1 유연한 스키마: 카테고리는 데이터의 `원본키` 필드에서 자동 유도
- 2 비용·지연: 탐색 공간 축소 (카테고리 필터가 허브 레벨에서 즉시 작동)
- 3 지식 생애주기: 인제스트 후 idempotent 실행 가능 (이미 분할된 구조는 건드리지 않음)
- 4 하드코딩 금지: 카테고리 화이트리스트 없음

사용:
    python scripts/split_fanout_hubs.py
    # 결과: 기존 pkl 덮어쓰기 + 콘솔에 요약 출력
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.graphdb.academic_graph import AcademicGraph  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 분할 대상 부모 노드 타입 (허브가 될 수 있는 cohort/규칙 노드)
_HUB_PARENT_TYPES = {"졸업요건", "수강신청규칙"}

# 재배선 대상 엣지 relation (cond 노드로 향하는 것들)
_REWIRE_RELATIONS = {"조건", "제약한다", "요구한다", "면제_적용", "포함한다"}


def main() -> int:
    logger.info("fan-out 허브 분할 시작")
    graph = AcademicGraph()
    G = graph.G

    # 대상 허브 노드 수집
    hub_nids: list[str] = []
    for nid, data in G.nodes(data=True):
        if data.get("type") in _HUB_PARENT_TYPES:
            hub_nids.append(nid)
    logger.info("허브 노드: %d (타입=%s)", len(hub_nids), _HUB_PARENT_TYPES)

    # 각 허브의 자식 엣지 중 "조건" 타입 자식만 sub-hub 경유로 변환
    rewired_count = 0
    created_subhubs: set[str] = set()
    skipped_no_category = 0

    for parent_nid in hub_nids:
        # (succ_nid, edge_data) 수집 — 순회 중 수정 방지
        children = list(G.successors(parent_nid))
        for succ_nid in children:
            succ_data = G.nodes.get(succ_nid, {}) or {}
            # 조건 노드만 재배선 대상
            if succ_data.get("type") != "조건":
                continue
            edge_data = G.edges.get((parent_nid, succ_nid), {}) or {}
            relation = edge_data.get("relation") or edge_data.get("type")
            if relation not in _REWIRE_RELATIONS:
                continue
            # 카테고리 = 원본키 (조건 노드의 필드)
            category = succ_data.get("원본키") or ""
            if not category:
                skipped_no_category += 1
                continue

            # sub-hub 확보
            subhub_id = graph.ensure_subhub(parent_nid, category)
            if subhub_id == parent_nid:
                continue
            created_subhubs.add(subhub_id)

            # 기존 엣지 제거 후 subhub → cond로 재배선
            G.remove_edge(parent_nid, succ_nid)
            graph.add_relation(subhub_id, succ_nid, relation, dict(edge_data))
            rewired_count += 1

    logger.info("재배선 엣지: %d", rewired_count)
    logger.info("생성된 sub-hub: %d (총 고유 카테고리 포함)", len(created_subhubs))
    logger.info("카테고리 없는 조건(스킵): %d", skipped_no_category)

    # 인덱스 갱신 (서브허브 타입 등록)
    graph._build_index()

    # 저장
    graph.save()
    logger.info("저장 완료: %s", ROOT / "data" / "graphs" / "academic_graph.pkl")

    # 요약 통계
    sub_hub_count = len(graph._type_index.get("서브허브", []))
    logger.info("최종 서브허브 노드 수: %d", sub_hub_count)

    # 샘플 출력
    sample_parent = None
    for h in hub_nids:
        if "grad_2023_내국인" in h:
            sample_parent = h
            break
    if sample_parent:
        succ_types: dict[str, int] = {}
        for s in G.successors(sample_parent):
            t = G.nodes[s].get("type", "?")
            succ_types[t] = succ_types.get(t, 0) + 1
        logger.info("샘플(%s) 자식 타입 분포: %s", sample_parent, succ_types)

    return 0


if __name__ == "__main__":
    sys.exit(main())
