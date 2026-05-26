"""schedule_gate 시점성 게이트 단위 테스트.

2026-05-26 — dry-run 진입 전 게이트 판정 결정론성 확보.
1주차 grounding 테스트와 같은 정신:
  - "어떤 노드가 차단되어야 하는가"를 코드로 못박음
  - 환경 의존(date.today()) 제거 — SCHEDULE_GATE_TODAY 주입으로 결정론
  - fail-open 정책 (parse 실패는 PASS) 검증
  - 적용 범위 사각지대 (notice·FAQ·composite) 명시적 통과 검증
"""

from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch

import pytest

from app.pipeline.schedule_gate import (
    GateDecision,
    get_mode,
    get_today,
    is_temporally_valid,
    make_block_fallback_message,
)


TODAY = date(2026, 5, 26)


# ── 핵심 판정 ────────────────────────────────────────────────────────


def test_past_schedule_blocked():
    """종료일 < today → BLOCKED."""
    meta = {"node_type": "학사일정", "종료일": "2026-03-02"}
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is False
    assert dec.reason == "schedule_past"


def test_future_schedule_passes():
    """종료일 > today → PASS."""
    meta = {"node_type": "학사일정", "종료일": "2026-12-15"}
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is True
    assert dec.reason == "future_or_active"


def test_active_schedule_passes():
    """종료일 == today → PASS (오늘 종료되는 일정은 아직 active)."""
    meta = {"node_type": "학사일정", "종료일": "2026-05-26"}
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is True
    assert dec.reason == "future_or_active"


# ── 사각지대 (게이트 적용 대상 아님) ────────────────────────────────


def test_notice_node_passes_even_with_past_date():
    """notice는 종료일 메타 있어도 게이트 미적용 (지난 공지도 참고 가치)."""
    meta = {"node_type": "공지", "종료일": "2025-01-01"}
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is True
    assert dec.reason == "not_schedule"


def test_faq_node_passes_even_with_past_date():
    """FAQ는 게이트 미적용 — grounding 재판 회피."""
    meta = {"node_type": "faq", "종료일": "2025-01-01"}
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is True
    assert dec.reason == "not_schedule"


def test_composite_no_end_date_passes():
    """다중 일정 bundling(_query_registration 등) — 종료일 메타 없어 자연 PASS."""
    meta = {"node_type": "학사일정"}  # 종료일 키 없음
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is True
    assert dec.reason == "no_end_date"


def test_empty_end_date_passes():
    """종료일 빈 문자열도 no_end_date로 PASS."""
    meta = {"node_type": "학사일정", "종료일": ""}
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is True
    assert dec.reason == "no_end_date"


# ── fail-open (parse 실패는 PASS + 경고) ────────────────────────────


def test_invalid_date_format_fail_open():
    """ISO 파싱 실패 → fail-open PASS. 멀쩡한 답 차단 회피."""
    meta = {"node_type": "학사일정", "종료일": "invalid-date"}
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is True
    assert dec.reason == "parse_fail"


def test_oneglyph_invalid_node_fail_open():
    """데이터 오염 1건 (event_name='1', 종료일='1') — fail-open."""
    meta = {"node_type": "학사일정", "종료일": "1"}
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is True
    assert dec.reason == "parse_fail"


# ── 결정론 (env 주입) ──────────────────────────────────────────────


def test_get_today_default_is_today():
    """SCHEDULE_GATE_TODAY 미설정 시 date.today()."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SCHEDULE_GATE_TODAY", None)
        assert get_today() == date.today()


def test_get_today_env_override():
    """SCHEDULE_GATE_TODAY=YYYY-MM-DD 명시 시 해당 날짜."""
    with patch.dict(os.environ, {"SCHEDULE_GATE_TODAY": "2030-01-15"}):
        assert get_today() == date(2030, 1, 15)


def test_get_today_invalid_env_falls_back():
    """SCHEDULE_GATE_TODAY 파싱 실패 시 today() fallback (테스트 환경 보호)."""
    with patch.dict(os.environ, {"SCHEDULE_GATE_TODAY": "not-a-date"}):
        # 오늘로 떨어지면 OK (구체 날짜 비교는 today 비결정성으로 skip)
        assert isinstance(get_today(), date)


def test_get_mode_default_is_dry_run():
    """SCHEDULE_GATE_MODE 미설정 시 dry_run."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SCHEDULE_GATE_MODE", None)
        assert get_mode() == "dry_run"


def test_get_mode_enforce():
    with patch.dict(os.environ, {"SCHEDULE_GATE_MODE": "enforce"}):
        assert get_mode() == "enforce"


def test_get_mode_off():
    with patch.dict(os.environ, {"SCHEDULE_GATE_MODE": "off"}):
        assert get_mode() == "off"


def test_get_mode_invalid_falls_back():
    """비정상 값 → dry_run fallback (안전망)."""
    with patch.dict(os.environ, {"SCHEDULE_GATE_MODE": "invalid"}):
        assert get_mode() == "dry_run"


# ── 폴백 메시지 (placeholder 동작 확인) ───────────────────────────


def test_fallback_message_no_future_data():
    """has_future='no' → 학사공지 안내 (보수적 폴백)."""
    msg = make_block_fallback_message({"이벤트명": "수강신청 기간"}, "no")
    assert "학사공지" in msg


def test_fallback_message_unknown_future_data():
    """has_future='unknown' (학기 파싱 실패) → 같은 보수적 폴백."""
    msg = make_block_fallback_message({}, "unknown")
    assert "학사공지" in msg


def test_fallback_message_yes_future_data():
    """has_future='yes' → placeholder (2026-2 데이터 인입 후 확정)."""
    msg = make_block_fallback_message({"이벤트명": "수강신청 기간"}, "yes")
    # 현 단계는 placeholder, 정확한 다음 일정 lookup 미구현
    assert msg  # non-empty


# ── 통합: 실제 graph 노드 메타로 게이트 동작 ────────────────────────


def test_real_schedule_node_metadata_blocks_past():
    """실제 _schedule_to_result가 만들어 내는 metadata 패턴으로 차단 검증."""
    # _schedule_to_result 보강(2026-05-26)이 박는 메타 키 그대로
    meta = {
        "source_type": "graph",
        "node_type": "학사일정",
        "node_id": "schedule_수강신청_2026-1",
        "시작일": "2026-02-01",
        "종료일": "2026-02-15",
        "학기": "2026-1",
        "이벤트명": "수강신청",
        "direct_answer": "수강신청 기간은 2026-02-01부터 2026-02-15까지입니다.",
    }
    dec = is_temporally_valid(meta, TODAY)
    assert dec.valid is False
    assert dec.reason == "schedule_past"
