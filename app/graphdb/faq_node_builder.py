"""
FAQ → 그래프 변환기

원칙 1: 유연한 스키마 — FAQ를 그래프 1급 시민(노드)으로 승격,
        카테고리 자동 감지로 향후 신규 카테고리 자동 수용
원칙 2: 동적 최적화 — 카테고리 루트 노드 + 관련 도메인 엣지로 FAQ 커뮤니티 형성
원칙 3: 증분 업데이트 — faq_id 기준 upsert, 삭제된 FAQ는 자동 정리
"""

import json
import logging
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# ── 원칙 2: 카테고리 → 관련 도메인 노드 타입 매핑 (FAQ_참조 엣지 생성 기준) ──
# 각 카테고리의 FAQ는 아래 노드 타입의 모든 기존 노드들과 엣지로 연결된다.
# 새 카테고리가 등장해도 기본값(엣지 없음)으로 동작 가능 → 코드 변경 없이 운영.
CATEGORY_TO_NODE_TYPES: dict[str, list[str]] = {
    "수강신청": ["수강신청규칙", "학사일정"],
    "계절학기": ["계절학기", "수강신청규칙"],
    "졸업": ["졸업요건", "조기졸업"],
    "학적변동": ["휴복학"],
    "OCU": ["OCU"],
    "증명서/발급": [],
    "성적/시험": ["성적처리"],
    "전공/전과": ["학과전공", "전공이수방법"],
    "교육과정/이수": ["전공이수방법", "졸업요건"],
    "교직": ["교직", "학과전공"],
    "장학/학비": ["장학금", "등록금반환"],
    "출결": ["전자출결"],
    "기타": [],
}


class FaqNodeBuilder:
    """
    FAQ JSON을 받아 그래프에 FAQ 노드와 카테고리 루트를 추가합니다.

    원칙 1: NODE_TYPES에 "FAQ" 등록됨 → 스키마 1급 시민
    원칙 2: 카테고리별 루트 노드(`faq_root_{카테고리}`)로 커뮤니티 형성
    원칙 3: faq_id 키 기반 upsert, 미존재 FAQ는 자동 제거
    """

    def build_from_json(
        self,
        graph,  # AcademicGraph
        faq_path: str | Path,
    ) -> dict:
        """
        FAQ JSON 파일 전체를 그래프에 반영합니다.

        Returns:
            {"added": int, "updated": int, "removed": int, "edges": int,
             "categories": list[str]}
        """
        path = Path(faq_path)
        if not path.exists():
            logger.warning("FAQ 파일 없음: %s", path)
            return {"added": 0, "updated": 0, "removed": 0, "edges": 0, "categories": []}

        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "faq" in data:
            items = data["faq"]
        else:
            items = data

        return self.build_from_items(graph, items)

    def build_from_items(
        self,
        graph,
        items: Iterable[dict],
    ) -> dict:
        """FAQ 항목 리스트를 그래프에 반영합니다."""
        stats = {"added": 0, "updated": 0, "removed": 0, "edges": 0, "categories": []}

        # 1. 기존 FAQ 노드 ID 수집 (upsert 후 삭제 식별용)
        existing_faq_ids = {
            nid: graph.G.nodes[nid].get("faq_id", "")
            for nid in list(graph._type_index.get("FAQ", []))
            if nid in graph.G.nodes
        }
        seen_faq_ids: set[str] = set()
        categories: set[str] = set()

        # 2. 각 FAQ 항목을 노드로 등록 (upsert)
        for item in items:
            faq_id = item.get("id") or item.get("faq_id")
            question = (item.get("question") or "").strip()
            answer = (item.get("answer") or "").strip()
            category = (item.get("category") or "기타").strip()

            if not faq_id or not question or not answer:
                continue

            seen_faq_ids.add(faq_id)
            categories.add(category)

            node_key = graph._sanitize_node_key(faq_id)
            node_id = f"faq_{node_key}"
            is_new = node_id not in graph.G.nodes

            # 원칙 1(유연한 스키마): JSON에 선언된 answer_type 같은 선택 필드는
            # 그래프 노드 메타에 그대로 전파해 검색 단계에서 활용(예: 리다이렉트 FAQ).
            node_metadata = {
                "출처파일": item.get("source_file", ""),
            }
            if item.get("answer_type"):
                node_metadata["answer_type"] = item["answer_type"]

            graph.add_faq_node(
                faq_id=faq_id,
                question=question,
                answer=answer,
                category=category,
                metadata=node_metadata,
            )

            if is_new:
                stats["added"] += 1
            else:
                stats["updated"] += 1

        # 3. 삭제된 FAQ 정리 (원칙 3: 증분 업데이트)
        for nid, faq_id in existing_faq_ids.items():
            if faq_id and faq_id not in seen_faq_ids:
                if nid in graph.G.nodes:
                    graph.G.remove_node(nid)
                    # type_index도 갱신
                    if nid in graph._type_index.get("FAQ", []):
                        graph._type_index["FAQ"].remove(nid)
                    stats["removed"] += 1

        # 4. 카테고리 루트 노드 생성 + "포함한다" 엣지
        stats["edges"] += self._build_category_roots(graph, categories)

        # 5. FAQ → 관련 도메인 노드 "FAQ_참조" 엣지
        stats["edges"] += self._build_domain_edges(graph)

        stats["categories"] = sorted(categories)
        logger.info(
            "FAQ 그래프 반영: 추가=%d, 업데이트=%d, 제거=%d, 엣지=%d, 카테고리=%d",
            stats["added"], stats["updated"], stats["removed"],
            stats["edges"], len(categories),
        )
        return stats

    def _build_category_roots(self, graph, categories: set[str]) -> int:
        """카테고리별 루트 노드와 FAQ 노드 간 '포함한다' 엣지를 생성합니다."""
        edge_count = 0
        for category in categories:
            root_key = graph._sanitize_node_key(category)
            root_id = f"faq_root_{root_key}"
            if root_id not in graph.G.nodes:
                graph.G.add_node(
                    root_id,
                    type="FAQ",
                    구분=f"FAQ 카테고리: {category}",
                    카테고리=category,
                    is_category_root=True,
                )
                graph._index_add(root_id, "FAQ")

            # 해당 카테고리 FAQ 노드들을 루트에 연결
            for faq_nid in list(graph._type_index.get("FAQ", [])):
                if faq_nid == root_id or faq_nid not in graph.G.nodes:
                    continue
                node_data = graph.G.nodes[faq_nid]
                if node_data.get("is_category_root"):
                    continue
                if node_data.get("카테고리") != category:
                    continue
                if not graph.G.has_edge(root_id, faq_nid):
                    graph.G.add_edge(root_id, faq_nid, relation="포함한다")
                    edge_count += 1
        return edge_count

    def _build_domain_edges(self, graph) -> int:
        """FAQ 노드 → 관련 도메인 노드로 'FAQ_참조' 엣지를 생성합니다."""
        edge_count = 0
        for faq_nid in list(graph._type_index.get("FAQ", [])):
            if faq_nid not in graph.G.nodes:
                continue
            data = graph.G.nodes[faq_nid]
            if data.get("is_category_root"):
                continue
            category = data.get("카테고리", "")
            target_types = CATEGORY_TO_NODE_TYPES.get(category, [])
            if not target_types:
                continue
            existing_targets = set(graph.G.successors(faq_nid))
            for ttype in target_types:
                for tnid in graph._type_index.get(ttype, []):
                    if tnid in existing_targets or tnid not in graph.G.nodes:
                        continue
                    graph.G.add_edge(faq_nid, tnid, relation="FAQ_참조")
                    existing_targets.add(tnid)
                    edge_count += 1
        return edge_count
