"""
커뮤니티 선택기 — Intent + entities 기반 동적 그래프 커뮤니티 선택

원칙 1(스키마 진화): config 파일로 Intent-노드타입 매핑을 외부화. 새 노드 타입
                     추가 시 코드 변경 없이 config만 수정.
원칙 2(동적 커뮤니티): Intent별 필요한 커뮤니티만 선별 검색 → 노드 타입이 늘어나도
                     지연 시간 선형 증가 억제.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from app.models import Intent

logger = logging.getLogger(__name__)


_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "intent_communities.json"
)


class CommunitySelector:
    """
    Intent + entities로 검색할 그래프 노드 타입 리스트를 결정합니다.

    config 파일(`config/intent_communities.json`)이 단일 진실 공급원.
    로드 실패 시 빈 리스트 반환 → 호출부는 하드코딩 fallback으로 동작 가능.
    """

    _lock = threading.Lock()

    def __init__(self, config_path: Optional[str | Path] = None):
        self.config_path = Path(config_path or _DEFAULT_CONFIG_PATH)
        self._communities: dict[str, dict] = {}
        self._intent_routing: dict[str, list[str]] = {}
        self._keyword_boosts: dict[str, list[str]] = {}
        self._loaded = False
        self.load()

    def load(self) -> bool:
        """config 파일을 로드합니다. 성공 여부 반환."""
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.error("커뮤니티 설정 로드 실패 %s: %s", self.config_path, e)
            self._loaded = False
            return False

        with self._lock:
            self._communities = data.get("communities", {})
            self._intent_routing = data.get("intent_routing", {})
            self._keyword_boosts = data.get("keyword_boosts", {})
            self._loaded = True
        logger.info(
            "커뮤니티 설정 로드: %d 커뮤니티 / %d Intent 매핑 / %d 키워드 부스트",
            len(self._communities), len(self._intent_routing), len(self._keyword_boosts),
        )
        return True

    def reload(self) -> bool:
        """config 핫 리로드 — 운영 중 설정 변경 반영."""
        return self.load()

    @property
    def is_loaded(self) -> bool:
        return self._loaded and bool(self._intent_routing)

    def get_communities(self, intent: Intent) -> list[str]:
        """Intent에 매핑된 커뮤니티 이름 리스트."""
        if not self._loaded:
            return []
        key = intent.value if hasattr(intent, "value") else str(intent)
        return list(self._intent_routing.get(key, []))

    def get_node_types(
        self,
        intent: Intent,
        entities: Optional[dict] = None,
        question: Optional[str] = None,
    ) -> list[str]:
        """
        Intent + entities + question 키워드를 기반으로 검색할 노드 타입 리스트를 반환.

        원칙 1: keyword_boosts가 config에서 관리돼 코드 변경 없이 확장 가능.
        원칙 2: Intent 분류가 잡아내지 못한 교차 토픽(예: MAJOR_CHANGE에 '교직' 섞인 질문)을
                런타임 커뮤니티 병합으로 보정 — 무차별 확장이 아닌 키워드 히트 시에만 동작.
        """
        if not self._loaded:
            return []

        communities = self.get_communities(intent)

        # 엔티티 기반 보정 — 작은 규모로 시작
        if entities:
            if entities.get("department") and "curriculum" not in communities:
                communities.append("curriculum")
            if entities.get("scholarship_type") and "academic_support" not in communities:
                communities.append("academic_support")

        # 원칙 2: 질문 키워드 기반 런타임 커뮤니티 부스트
        if question and self._keyword_boosts:
            q = question.lower()
            for keyword, boost_comms in self._keyword_boosts.items():
                if keyword.lower() in q:
                    for comm_name in boost_comms:
                        if comm_name not in communities:
                            communities.append(comm_name)

        # 커뮤니티 → 노드 타입 flatten (중복 제거, 순서 보존)
        seen: set[str] = set()
        node_types: list[str] = []
        for comm_name in communities:
            comm = self._communities.get(comm_name, {})
            for nt in comm.get("node_types", []):
                if nt not in seen:
                    seen.add(nt)
                    node_types.append(nt)
        return node_types

    def all_registered_node_types(self) -> set[str]:
        """정합성 테스트·디버그용 — communities에 등록된 모든 노드 타입 집합."""
        types: set[str] = set()
        for comm in self._communities.values():
            types.update(comm.get("node_types", []))
        return types


# 싱글톤 인스턴스 (지연 초기화)
_default_selector: Optional[CommunitySelector] = None


def get_default_selector() -> CommunitySelector:
    """기본 CommunitySelector 싱글톤을 반환합니다."""
    global _default_selector
    if _default_selector is None:
        _default_selector = CommunitySelector()
    return _default_selector
