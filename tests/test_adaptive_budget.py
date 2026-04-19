"""적응형 컨텍스트 예산 공식 단위 테스트."""

import os
from unittest.mock import patch

from app.pipeline.context_merger import _adaptive_budget, _detect_handler_cluster_size, _adaptive_cutoff
from app.config import settings
from app.models import SearchResult


def test_budget_unchanged_when_few_results():
    """n ≤ baseline_chunk_count면 base 그대로 (현행 동작 유지)."""
    baseline = settings.context_budget.baseline_chunk_count
    for n in range(0, baseline + 1):
        assert _adaptive_budget(1200, n) == 1200, f"n={n} should keep base"


def test_budget_expands_linearly():
    """n > baseline에서 청크당 per_chunk_bonus씩 선형 증가."""
    baseline = settings.context_budget.baseline_chunk_count
    bonus = settings.context_budget.per_chunk_bonus
    base = 1200
    for extra in range(1, 5):
        n = baseline + extra
        expected = min(
            base + extra * bonus,
            int(base * settings.context_budget.cap_ratio),
        )
        assert _adaptive_budget(base, n) == expected


def test_budget_capped_at_cap_ratio():
    """n이 매우 크면 base × cap_ratio에서 멈춤."""
    base = 1200
    cap = int(base * settings.context_budget.cap_ratio)
    assert _adaptive_budget(base, 100) == cap
    assert _adaptive_budget(base, 50) == cap


def test_budget_max_extra_chunks_limit():
    """max_extra_chunks 초과분은 반영 안 됨 (cap과 동일하게 도달)."""
    base = 1200
    max_extra = settings.context_budget.max_extra_chunks
    baseline = settings.context_budget.baseline_chunk_count
    bonus = settings.context_budget.per_chunk_bonus
    cap = int(base * settings.context_budget.cap_ratio)

    # 정확히 baseline + max_extra 에서 멈춰야 함
    at_limit = _adaptive_budget(base, baseline + max_extra)
    beyond_limit = _adaptive_budget(base, baseline + max_extra + 10)
    assert at_limit == beyond_limit
    # cap 또는 선형 확장값 중 낮은 값
    expected = min(base + max_extra * bonus, cap)
    assert at_limit == expected


def test_budget_scales_with_base():
    """base가 다르면 스케일도 다르게 (cap ratio 적용)."""
    # GRADUATION_REQ (base=1800)이 SCHOLARSHIP (base=1200)보다 큰 예산
    large_base = _adaptive_budget(1800, 10)
    small_base = _adaptive_budget(1200, 10)
    assert large_base > small_base


def test_budget_monotonic():
    """n이 증가하면 budget은 감소하지 않음 (monotonic non-decreasing)."""
    base = 1200
    prev = 0
    for n in range(0, 20):
        current = _adaptive_budget(base, n)
        assert current >= prev, f"n={n}: {current} < prev {prev}"
        prev = current


def test_budget_scholarship_case_expands_enough():
    """
    실제 진단 케이스 재현: 장학금 쿼리 n=10, base=1200
    기존 문제: 1200 token / 1.5 = 800자 → 2개 청크만 들어감
    수정 후: 8개 장학금 유형 모두 수용 가능한 예산이어야 함
    """
    base_scholarship = 1200
    adapted = _adaptive_budget(base_scholarship, 10)
    # 8개 × ~450자 = 3600자 = 2400 token 필요
    # cap=3000 근처에 도달해서 최소 2600 이상 보장
    assert adapted >= 2400, f"SCHOLARSHIP n=10 budget={adapted} too small"


# ── 컴포넌트별 토글 (A/B 평가용) ──

def _mock_results(n: int, score: float = 1.0) -> list:
    return [
        SearchResult(text=f"r{i}", score=score, source="s", metadata={})
        for i in range(n)
    ]


def test_cluster_detection_uniform_scores():
    """같은 score의 결과 N개 → 클러스터 크기 N."""
    assert _detect_handler_cluster_size(_mock_results(8, 1.0)) == 8
    assert _detect_handler_cluster_size(_mock_results(3, 0.9)) == 3


def test_cluster_detection_empty():
    assert _detect_handler_cluster_size([]) == 0


def test_adaptive_cutoff_with_cluster_preserve():
    """cluster_preserve=N → 적어도 N개는 ratio와 무관하게 보존."""
    # 8개 중 4개는 고점수, 나머지 4개는 노이즈
    results = (
        _mock_results(4, 0.8) + _mock_results(4, 0.05)
    )
    # cluster_preserve=0 (기본): cutoff로 저점수 제거
    no_preserve = _adaptive_cutoff(results, ratio=0.70, cluster_preserve=0)
    # cluster_preserve=8 (전체 보존): 모두 남음
    with_preserve = _adaptive_cutoff(results, ratio=0.70, cluster_preserve=8)
    assert len(with_preserve) >= len(no_preserve)
    assert len(with_preserve) == 8


def test_toggle_cluster_preserve_disabled():
    """CTX_CLUSTER_PRESERVE=false 시 cluster_preserve 비활성 — 설정만 확인."""
    with patch.dict(os.environ, {"CTX_CLUSTER_PRESERVE": "false"}):
        from app.config import ContextBudgetConfig
        cfg = ContextBudgetConfig()
        assert cfg.cluster_preserve_enabled is False


def test_toggle_adaptive_budget_disabled():
    """CTX_ADAPTIVE_BUDGET=false 시 공식 자체는 불변 (호출측에서 스킵)."""
    with patch.dict(os.environ, {"CTX_ADAPTIVE_BUDGET": "false"}):
        from app.config import ContextBudgetConfig
        cfg = ContextBudgetConfig()
        assert cfg.adaptive_budget_enabled is False


def test_toggle_fair_share_disabled():
    """CTX_FAIR_SHARE=false 시 fair_share_enabled=False."""
    with patch.dict(os.environ, {"CTX_FAIR_SHARE": "false"}):
        from app.config import ContextBudgetConfig
        cfg = ContextBudgetConfig()
        assert cfg.fair_share_enabled is False


def test_all_toggles_default_true():
    """기본값은 모두 true (PR #8 기본 동작 유지, 기존 호환성)."""
    assert settings.context_budget.cluster_preserve_enabled is True
    assert settings.context_budget.adaptive_budget_enabled is True
    assert settings.context_budget.fair_share_enabled is True
