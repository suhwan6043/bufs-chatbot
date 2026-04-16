"""적응형 컨텍스트 예산 공식 단위 테스트."""

from app.pipeline.context_merger import _adaptive_budget
from app.config import settings


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
