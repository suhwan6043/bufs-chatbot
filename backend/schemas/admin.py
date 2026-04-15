"""관리자 API Pydantic 스키마."""

from typing import Any, Optional
from pydantic import BaseModel, Field


# ── 인증 ──
class AdminLogin(BaseModel):
    password: str = Field(..., min_length=1)


class AdminToken(BaseModel):
    token: str
    expires_at: str


# ── 대시보드 ──
class KPIData(BaseModel):
    total_questions: int = 0
    today_questions: int = 0
    avg_duration_sec: float = 0.0
    faq_count: int = 0


class DailyCount(BaseModel):
    date: str
    count: int


class IntentCount(BaseModel):
    intent: str
    count: int


class RecentChat(BaseModel):
    time: str = ""
    question: str = ""
    intent: str = ""
    duration_ms: int = 0
    rating: str = "-"


class DashboardData(BaseModel):
    kpi: KPIData
    daily_chart: list[DailyCount] = Field(default_factory=list)
    intent_distribution: list[IntentCount] = Field(default_factory=list)
    recent_chats: list[RecentChat] = Field(default_factory=list)


# ── 졸업요건 ──
class GraduationRequirement(BaseModel):
    """졸업요건 저장 요청."""
    group: str = Field(..., description="학번 그룹 (예: 2024_2025)")
    student_type: str = Field(..., description="내국인/외국인/편입생")
    major: Optional[str] = Field(None, description="전공 (None이면 공통)")
    requirements: dict[str, Any] = Field(default_factory=dict)


class GraduationOverview(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)


# ── 조기졸업 ──
class EarlyGradSchedule(BaseModel):
    semester: str
    start_date: str
    end_date: str
    method: str = ""


class EarlyGradCriteria(BaseModel):
    group: str
    credits: int
    note: str = ""
    condition: str = ""


class EarlyGradEligibility(BaseModel):
    semester_req: str = ""
    gpa_2005: str = ""
    gpa_2006: str = ""
    gpa_2007: str = ""
    global_college: str = ""
    no_transfer: bool = True


class EarlyGradNotes(BaseModel):
    dropout: str = ""
    pass_note: str = ""
    sem7_note: str = ""


# ── 졸업요건 옵션 ──
class GraduationOptions(BaseModel):
    groups: dict[str, str] = Field(default_factory=dict)
    student_types: list[str] = Field(default_factory=list)
    dept_tree: dict[str, list[str]] = Field(default_factory=dict)


# ── 학과별 졸업인증 ──
class DeptCertSave(BaseModel):
    major: str
    cert_requirement: str = ""
    cert_subjects: str = ""
    cert_pass_criteria: str = ""
    cert_alternative: str = ""


# ── 학사일정 ──
class ScheduleEvent(BaseModel):
    event_name: str
    semester: str
    start_date: str
    end_date: str
    note: str = ""


# ── 크롤러 ──
class CrawlerStatus(BaseModel):
    enabled: bool = False
    is_running: bool = False
    interval_minutes: int = 30
    next_run: str = ""
    notice_count: int = 0


# ── 로그 ──
class LogEntry(BaseModel):
    timestamp: str = ""
    session_id: str = ""
    student_id: str = ""
    intent: str = ""
    question: str = ""
    answer: str = ""
    duration_ms: int = 0
    rating: Optional[int] = None


# ── FAQ (관리자 큐레이션 피드백 루프) ──
class FaqItem(BaseModel):
    """목록/상세 응답용."""
    id: str
    category: str = ""
    question: str = ""
    answer: str = ""
    source: str = "academic"        # "academic" | "admin"
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    source_question: Optional[str] = None
    answer_type: Optional[str] = None
    # 학생 속성 분기 필드 (없으면 전체 허용)
    student_types: list[str] = Field(default_factory=list,
        description="적용 학생유형 (빈 리스트=전체). 예: ['외국인','편입생']")
    cohort_from: Optional[int] = Field(None, description="적용 최소 학번 (예: 2023)")
    cohort_to: Optional[int] = Field(None, description="적용 최대 학번 (예: 2025)")


class FaqCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    answer: str = Field(..., min_length=1, max_length=10000)
    category: str = Field(..., min_length=1, max_length=50)
    source_question: Optional[str] = Field(default=None, max_length=2000)
    # 학생 속성 분기 필드 (선택, 없으면 전체 허용)
    student_types: list[str] = Field(default_factory=list,
        description="적용 학생유형 (빈 리스트=전체). 예: ['외국인','편입생']")
    cohort_from: Optional[int] = Field(None, description="적용 최소 학번 (예: 2023)")
    cohort_to: Optional[int] = Field(None, description="적용 최대 학번 (예: 2025)")


class FaqUpdate(BaseModel):
    question: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    answer: Optional[str] = Field(default=None, min_length=1, max_length=10000)
    category: Optional[str] = Field(default=None, min_length=1, max_length=50)
    source_question: Optional[str] = Field(default=None, max_length=2000)
    # 학생 속성 분기 필드 (선택, None이면 기존값 유지)
    student_types: Optional[list[str]] = Field(default=None)
    cohort_from: Optional[int] = Field(default=None)
    cohort_to: Optional[int] = Field(default=None)


class FaqListResponse(BaseModel):
    total: int
    items: list[FaqItem] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class UncoveredExample(BaseModel):
    question: str
    answer: str = ""
    timestamp: str = ""
    session_id: str = ""
    rating: Optional[int] = None
    refused: bool = False


class UncoveredCluster(BaseModel):
    """미답변 질의 클러스터 — 관리자에게 노출."""
    representative_question: str
    count: int
    last_asked: str = ""
    examples: list[UncoveredExample] = Field(default_factory=list)


class UncoveredResponse(BaseModel):
    scanned_days: int
    total_candidates: int
    clusters: list[UncoveredCluster] = Field(default_factory=list)
