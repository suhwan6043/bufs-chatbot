"""schedule_* 시점성(temporal) 게이트 — **dry-run 전용** (2026-05-26).

## 배경 — Stage-6 위험 발굴

5/26 시점성 측정에서 47개 schedule_* 노드 중 25건(53.2%)이 종료일 < 오늘.
운영에서 지난 일정(예: "2026-1 수강신청 기간")이 direct_answer로 채택돼
학생에게 오도 정보 제공 위험. 1주차 grounding → cohort 정합성 → PR #25 →
AnswerUnit → results 패턴 → 시점성으로 6단계 위험 추적 끝에 발굴된 1순위.

## 본 모듈의 역할 — 시점성 판정기 (단순)

  - `is_temporally_valid(metadata, today)` — 종료일 < today면 invalid
  - 적용 범위: schedule_* 노드 한정 (notice·FAQ·composite 제외)
  - 종료일 메타 없으면 자연 PASS (composite·기타 타입)
  - 종료일 파싱 실패 = PASS + 경고 로그 (fail-open)
  - has_future 신호는 본 모듈 책임 아님 — context_merger가 graph 헬퍼
    (`academic_graph.has_future_semester_data`)로 별도 합성

grounding.py 모델 미러 — 판정기는 단순, 호출자가 로그·후처리 합성.

## 모드 (env)

  - `SCHEDULE_GATE_MODE=dry_run` (default): 검사만, BLOCKED여도 PASS + 로그.
    **차단율·has_future 분포 측정용 — 1주차 grounding dry-run 정신**.
  - `SCHEDULE_GATE_MODE=enforce`: BLOCKED면 context_merger가 continue →
    다음 후보. dry-run 측정 후 수동으로 켤 것. **자동 enforce 금지**.
  - `SCHEDULE_GATE_MODE=off`: 게이트 자체 비활성 (디버깅용).

## 기준일 (env, 결정론·재현성)

  - `SCHEDULE_GATE_TODAY=YYYY-MM-DD`: 명시 시 해당 날짜로 판정.
    단위 테스트·dry-run 측정 재현성 확보. 미설정 시 `date.today()`.

## enforce 진입 기준 (측정 후 결정)

dry-run 측정에서 두 가지를 본다:
  1. 차단율 (past 비율) — 현재 추정 53.2%
  2. has_future_semester_data 분포 (graph 헬퍼)
     - "yes" 다수: enforce 켜도 폴백이 멀쩡 (다음 학기 데이터 있음)
     - "no" 다수: enforce 켜면 schedule 질문 전체가 "공지 확인" 폴백.
       (b) 데이터 갱신이 (a) 게이트보다 시급. enforce 보류 가능성.

현재 시점(2026-05-26) 데이터: 2026-2 학기 노드 = 0건이라 100% "no" 예상.
즉 본 게이트의 dry-run 측정이 "(b) 데이터 갱신의 시급성"을 숫자로 증명하는
도구가 될 가능성. 1주차 grounding이 "차단기 동결, 진짜 위험 다른 곳"으로
끝난 패턴 재현.

## 적용 범위 사각지대 (명시)

  ❌ composite schedule (다중 일정 bundling, _query_registration 등) —
     단일 종료일 없어 메타 미보강. 게이트는 종료일 메타 없으면 자연 PASS.
     composite 시점성은 별도 트랙 (측정 후 결정).
  ❌ notice_* (지난 공지도 참고 가치 있음, 시점성 의미 다름)
  ❌ FAQ 텍스트 내장 날짜 (메타 필드 아니라 잡을 수 없음)
  ❌ EN direct 경로 (chat.py가 `analysis.lang != "en"` 분기, EN은 항상 LLM)
  ✅ schedule_* 노드 + 종료일 메타 보유 (47개 중 45개, invalid·누락 2개 제외)

## 폴백 답 (enforce 시 필수, dry-run엔 placeholder)

차단 시 그 자리에 무엇을 내보낼지 — has_future_semester_data 분기:
  - "yes" → "다음 학기 일정은 ..." (placeholder, 2026-2 데이터 인입 후 실측 후 확정)
  - "no"/"unknown" → "정확한 최신 일정은 학사공지 (URL)를 확인하세요"

dry-run 단계엔 호출 안 됨 — placeholder 자리만 잡음.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


def _get_mode() -> str:
    """SCHEDULE_GATE_MODE env → 'dry_run' (default) | 'label' | 'enforce' | 'off'.

    label 모드 (2026-05-26 추가):
      past + next_semester_has_data="no" 케이스에 한해 응답 뒤에 맥락 라벨을
      덧붙임. 본문은 한 글자도 바꾸지 않음. 차단 안 함. 디폴트는 여전히 dry_run.
    """
    v = os.getenv("SCHEDULE_GATE_MODE", "dry_run").strip().lower()
    if v not in {"dry_run", "label", "enforce", "off"}:
        logger.warning("SCHEDULE_GATE_MODE invalid value=%r, falling back to dry_run", v)
        return "dry_run"
    return v


def _get_today() -> date:
    """SCHEDULE_GATE_TODAY env (YYYY-MM-DD) → 명시 시 해당 날짜, 미설정 시 today.
    파싱 실패 시 경고 + today fallback (테스트 환경 보호).
    """
    raw = os.getenv("SCHEDULE_GATE_TODAY", "").strip()
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        logger.warning("SCHEDULE_GATE_TODAY parse fail value=%r, falling back to today()", raw)
        return date.today()


@dataclass
class GateDecision:
    """게이트 판정 결과.

    valid=True  → 시점성 OK (또는 게이트 적용 대상 아님), 채택 가능
    valid=False → 종료일 < today, BLOCKED
    reason      → "schedule_past" | "not_schedule" | "no_end_date" |
                  "parse_fail" | "future_or_active"
    """
    valid: bool
    reason: str


def is_temporally_valid(
    metadata: dict,
    today: Optional[date] = None,
) -> GateDecision:
    """schedule_* 노드의 시점성 판정.

    Args:
        metadata: SearchResult.metadata. node_type, 종료일 키를 본다.
        today: 기준일. None이면 _get_today() (env 또는 date.today()).

    Returns:
        GateDecision(valid, reason). valid=False면 BLOCKED.

    판정 규칙:
        1. node_type != "학사일정"  → PASS (not_schedule, 게이트 대상 아님)
        2. 종료일 메타 없음          → PASS (no_end_date, composite·누락)
        3. 종료일 ISO 파싱 실패      → PASS + 경고 (parse_fail, fail-open)
        4. 종료일 < today           → BLOCK (schedule_past)
        5. else                     → PASS (future_or_active)
    """
    if today is None:
        today = _get_today()

    node_type = metadata.get("node_type", "")
    if node_type != "학사일정":
        return GateDecision(valid=True, reason="not_schedule")

    end_raw = metadata.get("종료일", "")
    if not end_raw:
        return GateDecision(valid=True, reason="no_end_date")

    try:
        end_d = date.fromisoformat(end_raw)
    except (ValueError, TypeError):
        logger.warning(
            "schedule_gate parse_fail end=%r node_id=%r → fail-open PASS",
            end_raw, metadata.get("node_id", ""),
        )
        return GateDecision(valid=True, reason="parse_fail")

    if end_d < today:
        return GateDecision(valid=False, reason="schedule_past")

    return GateDecision(valid=True, reason="future_or_active")


def make_block_fallback_message(
    metadata: dict,
    has_future_semester_data: str,
) -> str:
    """BLOCKED 시 그 자리에 내보낼 폴백 문구 (enforce 모드 전용 placeholder).

    Args:
        metadata: 차단된 노드의 metadata (이벤트명, 학기 등 활용 가능)
        has_future_semester_data: "yes" | "no" | "unknown"

    Returns:
        사용자 안내 문구.

    현재 dry-run 단계 — 호출되지 않음. enforce 진입 전 실제 트래픽 측정 후
    문구 확정. "yes" 갈래는 2026-2 데이터 인입 후 실측하며 확정 예정.
    """
    if has_future_semester_data == "yes":
        # placeholder — 2026-2 데이터 인입 후 실측 후 확정.
        # event_name = metadata.get("이벤트명", "")
        # 다음 학기 같은 이벤트 노드 lookup → "다음 일정: X, Y에 시작" 형태
        return "다음 학기 일정 안내는 학사공지를 확인하세요."

    # "no" / "unknown" / 기타 — 가장 보수적 폴백
    return (
        "정확한 최신 학사일정은 학사공지를 확인해 주세요. "
        "(https://www.bufs.ac.kr 또는 학생포털)"
    )


def get_mode() -> str:
    """현재 SCHEDULE_GATE_MODE 값 노출 (호출자 로깅용)."""
    return _get_mode()


def get_today() -> date:
    """현재 기준일 노출 (호출자 로깅용)."""
    return _get_today()


# ── label 모드 (2026-05-26) — 본문 변경 없이 맥락 라벨 부착 ──────────────
# 조건: (모드=label) AND (reason='schedule_past') AND (next_semester_has_data='no')
# 즉 "지난 일정인데 다음 학기 노드도 graph에 없는 케이스"에만 부착.
# active/future/parse_fail/not_schedule/no_end_date는 절대 부착하지 않음.
# has_future='yes'/'unknown' 분기는 이번 범위 밖 (placeholder — 데이터 인입 후).

PAST_SCHEDULE_LABEL_TEMPLATE = (
    "\n※ 가장 최근 {current}학기 기준이며, "
    "{next}학기 일정은 아직 공지되지 않았습니다."
)


def should_attach_past_label(mode: str, reason: str, has_future: str) -> bool:
    """label 모드에서 라벨 부착 조건을 만족하는지 판정.

    Args:
        mode: SCHEDULE_GATE_MODE 값 (dry_run/label/enforce/off)
        reason: GateDecision.reason (schedule_past/future_or_active/...)
        has_future: graph.has_future_semester_data 결과 (yes/no/unknown)

    Returns:
        True면 호출자가 append_past_schedule_label(direct_answer, ...) 호출.

    의도된 좁은 조건:
        - mode=label 일 때만 (dry_run/enforce/off에서는 False — 디폴트 동작 보존)
        - schedule_past 일 때만 (active/future 일정엔 절대 안 붙임)
        - has_future='no' 일 때만 (yes/unknown 분기는 placeholder)
    """
    return (
        mode == "label"
        and reason == "schedule_past"
        and has_future == "no"
    )


def append_past_schedule_label(
    direct_answer: str,
    current_semester: str,
    next_semester: str,
) -> str:
    """direct_answer 본문 뒤에 맥락 라벨을 덧붙임 (본문은 한 글자도 안 바꿈).

    Args:
        direct_answer: 원본 응답 텍스트
        current_semester: 매칭 노드의 학기 메타 (예: "2026-1")
        next_semester: graph.next_semester() 결과 (예: "2026-2")

    Returns:
        본문 + "\\n※ ..." 형태. 인자 누락(빈 문자열·None) 시 원본 그대로
        반환 (composite·MISSING fail-open).

    학기 증분 규칙은 호출자(context_merger)가 graph.next_semester로 계산해
    전달해야 함. 본 함수는 학기 문자열을 다시 파싱하지 않음 — DRY.
    """
    if not direct_answer or not current_semester or not next_semester:
        return direct_answer
    suffix = PAST_SCHEDULE_LABEL_TEMPLATE.format(
        current=current_semester, next=next_semester,
    )
    return direct_answer + suffix
