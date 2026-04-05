"""
CommunitySelector 정합성 테스트.

원칙 1: NODE_TYPES와 intent_communities.json의 정합성을 테스트로 강제.
        노드 타입이 추가됐는데 커뮤니티에 편입되지 않으면 CI에서 바로 감지한다.
원칙 2: keyword_boosts가 Intent 분류 오류를 실제로 보정하는지 검증.
"""

import pytest

from app.graphdb.academic_graph import AcademicGraph
from app.models import Intent
from app.pipeline.community_selector import CommunitySelector, get_default_selector

NODE_TYPES = AcademicGraph.NODE_TYPES


# ── 원칙 1: 스키마 정합성 ────────────────────────────────────────
def test_all_node_types_registered_in_communities():
    """AcademicGraph.NODE_TYPES에 등록된 모든 노드 타입이 최소 1개 커뮤니티에 소속돼야 한다.

    이 테스트가 깨지면 새 노드 타입이 intent_communities.json의 어느 커뮤니티에도 편입되지
    않아서 모든 Intent에서 검색 불가 상태가 된다. 교직 차단 회귀의 근본 원인이었다.
    """
    selector = CommunitySelector()
    assert selector.is_loaded, "intent_communities.json 로드 실패"

    registered = selector.all_registered_node_types()
    missing = [nt for nt in NODE_TYPES if nt not in registered]

    # 예외: FAQ는 커뮤니티에 별도로 있음
    assert not missing, (
        f"다음 노드 타입이 intent_communities.json의 어느 커뮤니티에도 속하지 않습니다: {missing}\n"
        f"새 노드 타입을 추가했다면 config/intent_communities.json의 communities 섹션에 추가하세요."
    )


def test_intent_routing_references_known_communities():
    """intent_routing의 모든 커뮤니티 이름이 communities 섹션에 정의돼 있어야 한다."""
    selector = CommunitySelector()
    known = set(selector._communities.keys())
    for intent_name, comms in selector._intent_routing.items():
        for c in comms:
            assert c in known, f"Intent {intent_name}가 알 수 없는 커뮤니티 '{c}'를 참조"


def test_keyword_boosts_reference_known_communities():
    """keyword_boosts의 모든 커뮤니티 이름이 communities 섹션에 정의돼 있어야 한다."""
    selector = CommunitySelector()
    known = set(selector._communities.keys())
    for keyword, comms in selector._keyword_boosts.items():
        for c in comms:
            assert c in known, f"keyword '{keyword}'가 알 수 없는 커뮤니티 '{c}'를 참조"


# ── 원칙 2: keyword_boosts 동작 검증 ────────────────────────────
def test_major_change_without_teacher_keyword_excludes_teacher():
    """MAJOR_CHANGE 단독일 때 '교직' 노드 타입은 선택에 포함되지 않는다."""
    selector = CommunitySelector()
    node_types = selector.get_node_types(Intent.MAJOR_CHANGE)
    assert "교직" not in node_types


def test_major_change_with_teacher_keyword_includes_teacher():
    """질문에 '교직'이 있으면 MAJOR_CHANGE여도 academic_support가 부스트돼 교직 노드 타입이 선택된다.

    이것이 기존에 끊어져 있던 'MAJOR_CHANGE + 교직' 교차 토픽의 회복 경로다.
    """
    selector = CommunitySelector()
    node_types = selector.get_node_types(
        Intent.MAJOR_CHANGE,
        entities={},
        question="제 2전공으로 교직신청 가능한가요?",
    )
    assert "교직" in node_types, (
        f"keyword_boosts가 '교직'을 감지하지 못했습니다. 선택된 타입: {node_types}"
    )


def test_registration_with_scholarship_keyword_boosts_academic_support():
    """REGISTRATION Intent여도 질문에 '장학'이 있으면 academic_support 커뮤니티가 부스트된다."""
    selector = CommunitySelector()
    node_types = selector.get_node_types(
        Intent.REGISTRATION,
        entities={},
        question="이번 학기 장학금 신청 기간이 언제인가요?",
    )
    assert "장학금" in node_types


def test_keyword_boost_noop_when_keyword_absent():
    """키워드가 없으면 boost는 동작하지 않아 비용/지연이 늘어나지 않는다."""
    selector = CommunitySelector()
    node_types = selector.get_node_types(
        Intent.MAJOR_CHANGE,
        entities={},
        question="복수전공 신청 조건이 뭐예요?",
    )
    assert "교직" not in node_types


# ── 싱글톤 ──────────────────────────────────────────
def test_default_selector_is_loaded():
    selector = get_default_selector()
    assert selector.is_loaded
