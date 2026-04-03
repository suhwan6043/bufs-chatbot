"""
공지사항 → 그래프 변환기

원칙 1: 유연한 스키마 — 제목/내용에서 태그를 자동 감지하여 분류
원칙 2: 동적 최적화 — 태그→노드타입 매핑으로 관련 노드만 엣지 연결
원칙 3: 증분 업데이트 — 기존 공지 노드는 upsert, 삭제된 공지는 제거
"""

import logging
from typing import Optional

from app.crawler.change_detector import CrawledItem, ChangeEvent

logger = logging.getLogger(__name__)


# ── 원칙 1: 데이터 기반 자동 태그 감지 ────────────────────────
# 제목+내용에서 키워드를 매칭하여 분류 태그를 자동 생성
TAG_KEYWORDS: dict[str, list[str]] = {
    "수강신청": ["수강신청", "수강정정", "수강취소", "수강확인", "장바구니"],
    "장학금": ["장학", "국가장학", "근로장학"],
    "졸업": ["졸업", "졸업시험", "학위수여", "졸업요건"],
    "OCU": ["OCU", "사이버대학", "원격강좌", "군 복무 중"],
    "등록": ["등록안내", "등록금", "납부"],
    "성적": ["성적", "학점인정", "특별강좌"],
    "출결": ["출결", "공인결석"],
    "학생증": ["학생증", "모바일", "국제학생증"],
    "일정": ["개교기념", "휴강", "시험기간", "정전"],
    "휴복학": ["휴학", "복학"],
    "이수구분": ["이수구분", "변경신청"],
    "수업시간표": ["수업시간표", "시간표"],
    "폐강": ["폐강", "폐강과목"],
    "PSC": ["PSC", "세미나"],
    "글소역": ["글로벌소통역량", "영어진단"],
}

# ── 원칙 2: 태그→기존 노드 타입 매핑 (엣지 자동 연결) ─────────
TAG_TO_NODE_TYPE: dict[str, str] = {
    "수강신청": "수강신청규칙",
    "장학금": "장학금",
    "졸업": "졸업요건",
    "OCU": "OCU",
    "등록": "등록금반환",
    "성적": "성적처리",
    "출결": "전자출결",
    "휴복학": "휴복학",
    "일정": "학사일정",
}


def detect_tags(title: str, content: str = "") -> list[str]:
    """제목+내용에서 분류 태그를 자동 감지합니다."""
    text = f"{title} {content[:300]}"  # 앞 300자만 사용
    tags = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.append(tag)
    return tags


class NoticeGraphBuilder:
    """
    CrawledItem/ChangeEvent를 받아 그래프에 공지사항 노드와 엣지를 추가합니다.

    원칙 1: 태그 자동 감지 (데이터 기반 스키마 진화)
    원칙 2: 태그→노드타입 매핑으로 필요한 엣지만 생성 (동적 최적화)
    원칙 3: upsert 방식으로 기존 노드 보존 (증분 업데이트)
    """

    def build_from_items(
        self,
        graph,  # AcademicGraph
        items: list[CrawledItem],
    ) -> dict:
        """
        CrawledItem 목록을 그래프에 반영합니다.

        Returns:
            {"added": int, "updated": int, "edges": int}
        """
        stats = {"added": 0, "updated": 0, "edges": 0}

        for item in items:
            is_new = self._build_single_item(
                graph, item.source_id, item.title, item.content,
                item.metadata, item.is_pinned,
            )
            if is_new:
                stats["added"] += 1
            else:
                stats["updated"] += 1

        # 엣지 연결
        stats["edges"] = self._build_edges(graph)

        logger.info(
            "공지 그래프 반영: 추가=%d, 업데이트=%d, 엣지=%d",
            stats["added"], stats["updated"], stats["edges"],
        )
        return stats

    def build_from_event(
        self,
        graph,  # AcademicGraph
        event: ChangeEvent,
    ) -> Optional[str]:
        """단일 ChangeEvent를 그래프에 반영합니다. 노드 ID 반환."""
        is_pinned = event.metadata.get("is_pinned", False)
        self._build_single_item(
            graph, event.source_id, event.title, event.content,
            event.metadata, is_pinned,
        )
        self._build_edges_for_node(graph, event.title)
        return f"notice_{graph._sanitize_node_key(event.title)}"

    def _build_single_item(
        self,
        graph,
        source_url: str,
        title: str,
        content: str,
        metadata: dict,
        is_pinned: bool,
    ) -> bool:
        """단일 공지를 그래프에 추가/업데이트. 신규이면 True."""
        tags = detect_tags(title, content)
        summary = content[:200].strip() if content else ""

        node_key = graph._sanitize_node_key(title)
        node_id = f"notice_{node_key}"
        is_new = node_id not in graph.G.nodes

        data = {
            "제목": title,
            "내용요약": summary,
            "발행일": metadata.get("post_date", ""),
            "게시판": metadata.get("source_name", ""),
            "is_pinned": is_pinned,
            "태그": tags,
            "URL": source_url,
        }

        graph.add_notice(source_url, data)

        if tags:
            logger.debug("  [%s] 태그=%s", title[:40], tags)

        return is_new

    def _build_edges(self, graph) -> int:
        """모든 공지사항 노드에 대해 관련 도메인 노드와 엣지를 연결합니다."""
        edge_count = 0
        for nid, data in graph._nodes_by_type("공지사항"):
            edge_count += self._connect_edges(graph, nid, data)
        return edge_count

    def _build_edges_for_node(self, graph, title: str) -> int:
        """단일 공지 노드에 대해 엣지를 연결합니다."""
        node_key = graph._sanitize_node_key(title)
        node_id = f"notice_{node_key}"
        if node_id not in graph.G.nodes:
            return 0
        data = dict(graph.G.nodes[node_id])
        return self._connect_edges(graph, node_id, data)

    def _connect_edges(self, graph, notice_nid: str, notice_data: dict) -> int:
        """공지 노드에서 관련 도메인 노드로 '공지_참조' 엣지를 생성합니다."""
        tags = notice_data.get("태그", [])
        if not tags:
            return 0

        edge_count = 0
        existing_targets = set(graph.G.successors(notice_nid))

        for tag in tags:
            target_type = TAG_TO_NODE_TYPE.get(tag)
            if not target_type:
                continue

            # 해당 타입의 기존 노드들과 연결
            target_nodes = graph._nodes_by_type(target_type)
            for target_nid, _ in target_nodes:
                if target_nid in existing_targets:
                    continue  # 이미 연결됨
                graph.add_relation(
                    notice_nid, target_nid, "공지_참조",
                    {"태그": tag},
                )
                existing_targets.add(target_nid)
                edge_count += 1

        return edge_count

    @staticmethod
    def remove_notice(graph, source_url: str, title: str = "") -> bool:
        """공지사항 노드 및 관련 엣지를 삭제합니다."""
        return graph.remove_notice(source_url, title)
