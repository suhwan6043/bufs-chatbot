"""
학사 리포트 액션 규칙 엔진 — Plugin Registry.

## 4원칙 준수
- **원칙 1 (스키마 진화)**: 카테고리명을 **패턴 매칭**으로 처리. 학사안내 신규 카테고리
  (예: "AI융합전공_기본")가 파싱되면 별도 코드 변경 없이 자동 포함.
- **원칙 2 (비용·지연)**: Pure Python, LLM 호출 0건. 규칙 단위 lazy 평가.
- **원칙 3 (지식 생애주기)**: 모든 수치 기준(재수강 제한, 조기졸업 GPA 등)은
  `AcademicGraph.get_*` 메서드로 동적 조회. 학사안내 재인제스트 시 자동 반영.
  graph 조회 실패 시에만 `TranscriptRulesConfig` fallback.
- **원칙 4 (하드코딩 금지)**: magic number 없음. 모든 임계치는 `ctx.settings`.

## Plugin 패턴
새 규칙 추가 시:
    @rule
    def rule_xxx(ctx: RuleContext) -> list[ActionItem]:
        ...
dispatch/렌더링 코드 영향 0. 규칙 파일만 수정.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

from app.transcript.models import (
    CourseRecord, CreditsSummary, StudentAcademicProfile, StudentProfile,
)

if TYPE_CHECKING:
    from app.graphdb.academic_graph import AcademicGraph
    from app.config import TranscriptRulesConfig
    from app.transcript.analyzer import TranscriptAnalyzer

logger = logging.getLogger(__name__)


# ── 데이터 구조 ──────────────────────────────────────────────

@dataclass
class ActionItem:
    """리포트 페이지 액션 아이템 (객관적·규정 근거 문장만)."""
    type: str                             # "shortage" | "retake" | "registration" | ...
    severity: str                         # "info" | "warn" | "error"
    title: str
    description: str
    action_label: str
    source: str                           # 규정 출처 ("graph:grad_2024_내국인" 등)
    target_count: Optional[float] = None  # 필요 학점/과목 수
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "action_label": self.action_label,
            "source": self.source,
            "target_count": self.target_count,
            "meta": self.meta,
        }


@dataclass
class RuleContext:
    """규칙 평가에 필요한 데이터 번들."""
    profile: StudentProfile
    credits: CreditsSummary
    courses: list[CourseRecord]
    analyzer: "TranscriptAnalyzer"
    graph: Optional["AcademicGraph"]
    settings: "TranscriptRulesConfig"


# ── Plugin Registry ─────────────────────────────────────────

_RULE_REGISTRY: list[Callable[[RuleContext], list[ActionItem]]] = []


def rule(fn: Callable[[RuleContext], list[ActionItem]]):
    """@rule 데코레이터 — 등록 (dispatch 코드 수정 불필요)."""
    _RULE_REGISTRY.append(fn)
    return fn


def evaluate_all(ctx: RuleContext) -> list[ActionItem]:
    """모든 규칙 평가. 개별 규칙 실패 시 다른 규칙 계속 (4원칙 #2 안전성)."""
    results: list[ActionItem] = []
    for r in _RULE_REGISTRY:
        try:
            out = r(ctx) or []
            results.extend(out)
        except Exception as exc:
            logger.warning("rule %s failed: %s", getattr(r, "__name__", "?"), exc)
    # severity 우선순위 정렬: error > warn > info
    order = {"error": 0, "warn": 1, "info": 2}
    results.sort(key=lambda a: order.get(a.severity, 9))
    return results


# ── 규칙 구현 (초기 8개) ────────────────────────────────────

@rule
def rule_shortage_by_category(ctx: RuleContext) -> list[ActionItem]:
    """
    카테고리별 부족 학점 — 4원칙 #1 패턴 매칭.
    "필수/기본/심화" 포함 → error, 그 외 → warn.
    """
    cfg = ctx.settings
    out: list[ActionItem] = []
    for cat in ctx.credits.categories:
        if cat.name == "총계":
            continue
        shortage = float(cat.부족학점 or 0)
        if shortage <= cfg.shortage_warn_min:
            continue
        is_required = any(kw in cat.name for kw in ("필수", "기본", "심화"))
        severity = "error" if is_required and shortage >= cfg.shortage_warn_min else "warn"
        out.append(ActionItem(
            type="shortage",
            severity=severity,
            title=f"{cat.name} {shortage:g}학점 부족",
            description=(
                f"현재 {cat.취득학점:g}학점 취득 / 졸업기준 {cat.졸업기준:g}학점 "
                f"— {shortage:g}학점 추가 이수 필요"
            ),
            action_label="다음 학기 수강 계획에 포함",
            source="transcript:credit_summary",
            target_count=shortage,
            meta={"category": cat.name, "is_required": is_required},
        ))
    return out


@rule
def rule_graduation_readiness(ctx: RuleContext) -> list[ActionItem]:
    """총 부족 학점 종합 판정."""
    total_short = float(ctx.credits.총_부족학점 or 0)
    if total_short <= 0:
        return [ActionItem(
            type="graduation_ready",
            severity="info",
            title="졸업 학점 요건 충족",
            description=(
                f"총 {ctx.credits.총_취득학점:g}/{ctx.credits.총_졸업기준:g}학점 취득 완료. "
                f"졸업인증·졸업시험 등 개별 요건은 학사지원팀 확인."
            ),
            action_label="졸업 신청 절차 확인",
            source="transcript:credit_summary",
        )]
    return []  # 부족 시에는 category 규칙이 상세 처리


@rule
def rule_retake_candidates(ctx: RuleContext) -> list[ActionItem]:
    """재수강 후보 요약 — settings.retake_grade_threshold 이하 과목."""
    threshold = ctx.settings.retake_grade_threshold
    candidates = ctx.analyzer.retake_candidates(threshold=threshold)
    if not candidates:
        return []
    total_credits = sum(float(c.학점 or 0) for c in candidates)
    return [ActionItem(
        type="retake",
        severity="warn",
        title=f"재수강 후보 {len(candidates)}과목 ({total_credits:g}학점)",
        description=(
            f"{threshold} 이하 성적 과목 — 평점 향상·졸업 학점 영향 검토 필요"
        ),
        action_label="재수강 대상 상세 확인",
        source=f"transcript:grade<={threshold}",
        target_count=float(len(candidates)),
        meta={"total_credits": total_credits},
    )]


@rule
def rule_retake_limit_from_graph(ctx: RuleContext) -> list[ActionItem]:
    """
    학번별 재수강 제한 규정 안내 — graph에서 동적 조회.
    원칙 #3: 학사안내 재인제스트 시 규정 자동 갱신.
    """
    if not ctx.graph or not ctx.profile.입학연도:
        return []
    try:
        rule_data = ctx.graph.get_retake_rule(ctx.profile.입학연도)
    except Exception:
        return []
    if not rule_data:
        return []

    # 이미 재수강한 학점 계산
    retake_credits = sum(float(c.학점 or 0) for c in ctx.courses if c.is_retake)
    bits: list[str] = []
    for k, v in rule_data.items():
        if v and k not in ("type", "id"):
            bits.append(f"{k}: {v}")
    desc = " / ".join(bits) if bits else "학번별 재수강 제한 규정 적용"

    return [ActionItem(
        type="retake_limit",
        severity="info",
        title=f"재수강 현황 {retake_credits:g}학점 / 규정 확인",
        description=desc,
        action_label="재수강 제한 규정 보기",
        source=f"graph:retake_{ctx.profile.입학연도}",
        meta={"retake_credits_used": retake_credits},
    )]


@rule
def rule_registration_limit(ctx: RuleContext) -> list[ActionItem]:
    """다음 학기 수강신청 한도 알림. GPA 우수 시 확장 자격 공지."""
    cfg = ctx.settings
    gpa = float(ctx.credits.평점평균 or 0)
    limit = ctx.analyzer.registration_limit()
    applied = limit.get("적용_최대학점") or cfg.fallback_reg_max
    basic = limit.get("기본_최대학점") or cfg.fallback_reg_max
    extended = limit.get("우수_최대학점") or cfg.fallback_reg_max_extended

    if gpa >= cfg.excellent_gpa_threshold:
        return [ActionItem(
            type="registration",
            severity="info",
            title=f"다음 학기 최대 {extended}학점까지 수강 가능",
            description=(
                f"직전학기 평점 {gpa:.2f} ≥ {cfg.excellent_gpa_threshold} — "
                f"우수자 확장 학점 적용"
            ),
            action_label="수강신청 계획 세우기",
            source="graph:reg_rule" if limit.get("우수_최대학점") else "config:fallback",
            target_count=float(extended),
        )]
    return [ActionItem(
        type="registration",
        severity="info",
        title=f"다음 학기 기본 최대 {basic}학점",
        description=(
            f"평점 {gpa:.2f} — 우수자 확장({extended}학점) 자격 {cfg.excellent_gpa_threshold} 이상 필요"
        ),
        action_label="수강신청 계획 세우기",
        source="graph:reg_rule" if limit.get("기본_최대학점") else "config:fallback",
        target_count=float(basic),
    )]


@rule
def rule_dual_major(ctx: RuleContext) -> list[ActionItem]:
    """복수전공 진행률 — 설정된 경우만."""
    status = ctx.analyzer.dual_major_status()
    if not status.get("active"):
        return []
    shortage = float(status.get("부족학점") or 0)
    earned = float(status.get("취득학점") or 0)
    required = float(status.get("기준학점") or 0)
    name = status.get("전공명") or ""
    if shortage > 0:
        return [ActionItem(
            type="dual_major",
            severity="warn",
            title=f"복수전공 {name} — {shortage:g}학점 부족",
            description=f"취득 {earned:g} / 기준 {required:g}학점",
            action_label="복수전공 수강 계획 확인",
            source="transcript:credit_summary",
            target_count=shortage,
            meta={"major_name": name},
        )]
    return [ActionItem(
        type="dual_major",
        severity="info",
        title=f"복수전공 {name} 이수 완료",
        description=f"취득 {earned:g} / 기준 {required:g}학점",
        action_label="졸업 신청 준비",
        source="transcript:credit_summary",
    )]


@rule
def rule_early_graduation(ctx: RuleContext) -> list[ActionItem]:
    """
    조기졸업 자격 판정 — graph 기준 동적 조회.
    원칙 #3: graph에서 기준 변경 시 자동 반영.
    """
    proj = ctx.analyzer._graduation_projection()
    if not proj:
        return []
    can = proj.get("can_early_graduate")
    eligible = proj.get("early_eligible_reasons") or []
    blocked = proj.get("early_blocked_reasons") or []
    source = "graph:early_grad"
    if ctx.graph and ctx.profile.입학연도:
        try:
            info = ctx.graph.get_early_graduation_info(ctx.profile.입학연도)
            if info:
                source = f"graph:early_grad_{ctx.profile.입학연도}"
        except Exception:
            source = "config:fallback"
    if can:
        return [ActionItem(
            type="early_graduation",
            severity="info",
            title="조기졸업 자격 충족",
            description=" / ".join(eligible) if eligible else "GPA·학점 조건 충족",
            action_label="조기졸업 신청 절차 확인",
            source=source,
            meta={"reasons": eligible},
        )]
    # 조기졸업 불가는 noise — 학기 많이 남았을 때만 알림
    if blocked and proj.get("semesters_remaining", 0) <= 2:
        return [ActionItem(
            type="early_graduation",
            severity="info",
            title="조기졸업 자격 미충족",
            description=" / ".join(blocked[:2]),
            action_label="정규 졸업 일정 확인",
            source=source,
            meta={"reasons": blocked},
        )]
    return []


@rule
def rule_graduation_certification(ctx: RuleContext) -> list[ActionItem]:
    """졸업인증 체크 — transcript.credits.졸업인증 dict 기반."""
    cert = ctx.credits.졸업인증 or {}
    if not cert:
        return []
    missing = [k for k, v in cert.items() if str(v).upper() in ("N", "NO", "미이수", "")]
    if not missing:
        return [ActionItem(
            type="certification",
            severity="info",
            title="졸업인증 전체 충족",
            description=f"인증 항목: {', '.join(cert.keys())}",
            action_label=None,
            source="transcript:certifications",
        )] if cert else []
    return [ActionItem(
        type="certification",
        severity="error",
        title=f"졸업인증 미충족 {len(missing)}항목",
        description=f"미충족: {', '.join(missing)}",
        action_label="해당 인증 신청·제출",
        source="transcript:certifications",
        target_count=float(len(missing)),
        meta={"missing": missing},
    )]


@rule
def rule_graduation_exam(ctx: RuleContext) -> list[ActionItem]:
    """졸업시험 응시 여부 — 마지막 학기 근접 시 강조."""
    exam = ctx.credits.졸업시험 or {}
    if not exam:
        return []
    pending = [k for k, v in exam.items() if str(v).upper() in ("N", "NO", "미응시", "")]
    if not pending:
        return [ActionItem(
            type="graduation_exam",
            severity="info",
            title="졸업시험 응시 완료",
            description=f"응시 완료: {', '.join(exam.keys())}",
            action_label=None,
            source="transcript:graduation_exam",
        )]
    proj = ctx.analyzer._graduation_projection()
    sev = "error" if (proj.get("semesters_remaining", 99) <= 1) else "warn"
    return [ActionItem(
        type="graduation_exam",
        severity=sev,
        title=f"졸업시험 미응시 {len(pending)}과정",
        description=f"미응시: {', '.join(pending)}",
        action_label="다음 졸업시험 일정 확인 후 신청",
        source="transcript:graduation_exam",
        target_count=float(len(pending)),
        meta={"pending": pending},
    )]
