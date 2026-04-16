"""성적표 API Pydantic 스키마."""

from typing import Any, Optional
from pydantic import BaseModel, Field


class TranscriptStatus(BaseModel):
    has_transcript: bool = False
    remaining_seconds: int = 0
    masked_name: str = ""
    gpa: float = 0.0
    # 학점은 소수점 허용 (1.5학점 교양·융합 수업 대응)
    total_acquired: float = 0.0
    total_required: float = 0.0
    total_shortage: float = 0.0
    progress_pct: int = 0
    dual_major: str = ""
    dual_shortage: float = 0.0


class UploadResponse(BaseModel):
    ok: bool = True
    masked_name: str = ""
    credits: Optional[dict] = None
    profile: Optional[dict] = None
    error: str = ""


# ── 학사 리포트 분석 스키마 (2026-04-16) ───────────────────────

class AnalysisCategory(BaseModel):
    """카테고리별 이수 현황. is_required는 '필수' 패턴 자동 판정."""
    name: str
    acquired: float = 0.0
    required: float = 0.0
    shortage: float = 0.0
    progress_pct: int = 0
    is_required: bool = False


class SemesterSummary(BaseModel):
    """학기별 수강 요약."""
    term: str
    credits: float = 0.0
    course_count: int = 0
    gpa: Optional[float] = None


class RetakeCandidate(BaseModel):
    """재수강 후보 과목."""
    course: str
    term: str = ""
    credits: float = 0.0
    grade: str = ""


class GraduationProjection(BaseModel):
    """졸업 예정 학기 + 조기졸업 자격 판정."""
    expected_term: str = "unknown"
    semesters_remaining: int = 0
    can_early_graduate: bool = False
    early_eligible_reasons: list[str] = Field(default_factory=list)
    early_blocked_reasons: list[str] = Field(default_factory=list)


class ActionItemResp(BaseModel):
    """객관·규정 근거 액션 (주관 없음)."""
    type: str
    severity: str           # info | warn | error
    title: str
    description: str
    action_label: Optional[str] = None
    source: str
    target_count: Optional[float] = None
    meta: dict = Field(default_factory=dict)


class TranscriptAnalysisResponse(BaseModel):
    """학사 리포트 페이지 전체 데이터."""
    has_transcript: bool = False
    profile: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
    categories: list[AnalysisCategory] = Field(default_factory=list)
    semesters: list[SemesterSummary] = Field(default_factory=list)
    grade_distribution: dict[str, int] = Field(default_factory=dict)
    retake_candidates: list[RetakeCandidate] = Field(default_factory=list)
    registration_limit: dict = Field(default_factory=dict)
    dual_major: dict = Field(default_factory=dict)
    graduation: GraduationProjection = Field(default_factory=GraduationProjection)
    actions: list[ActionItemResp] = Field(default_factory=list)
