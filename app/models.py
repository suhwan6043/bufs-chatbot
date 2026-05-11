"""
BUFS Academic Chatbot - 데이터 모델
파이프라인 전체에서 사용되는 데이터 구조를 정의합니다.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Intent(str, Enum):
    """Intent 분류 enum.

    multi-task 1 (2026-05-11): 구 11개 + 분할 신설 9개 = 총 20개.
    GENERAL 폴백 40% 해소를 위해 SCHOLARSHIP·REGISTRATION·GENERAL 광범위 카테고리를 세분화.
    구 키는 dict 매핑 호환을 위해 그대로 유지되지만, query_understanding 분류기는
    신 세분화 카테고리만 반환한다.
    """
    # ── 구 11개 (그대로 유지: dict 키 호환 및 직렬화) ──
    GRADUATION_REQ = "GRADUATION_REQ"
    EARLY_GRADUATION = "EARLY_GRADUATION"   # 조기졸업 신청·자격·기준
    REGISTRATION = "REGISTRATION"           # 분할됨 → REGISTRATION_GENERAL / GRADE_OPTION / REREGISTRATION
    SCHEDULE = "SCHEDULE"
    COURSE_INFO = "COURSE_INFO"
    MAJOR_CHANGE = "MAJOR_CHANGE"
    ALTERNATIVE = "ALTERNATIVE"
    SCHOLARSHIP = "SCHOLARSHIP"             # 분할됨 → SCHOLARSHIP_APPLY / SCHOLARSHIP_QUALIFICATION / TUITION_BENEFIT
    LEAVE_OF_ABSENCE = "LEAVE_OF_ABSENCE"   # 휴학/복학 신청·기간·방법·서류
    TRANSCRIPT = "TRANSCRIPT"               # 성적표 기반 개인 질문
    GENERAL = "GENERAL"                     # 분할됨 → CERTIFICATE / CONTACT / FACILITY / GENERAL

    # ── multi-task 1 (2026-05-11) 분할 신설 ──
    # REGISTRATION 3분할
    REGISTRATION_GENERAL = "REGISTRATION_GENERAL"           # 수강신청·OCU·장바구니·신청기간
    GRADE_OPTION = "GRADE_OPTION"                           # P/NP·성적포기·등급제 선택
    REREGISTRATION = "REREGISTRATION"                       # 재수강·이수구분 변경
    # SCHOLARSHIP 3분할
    SCHOLARSHIP_APPLY = "SCHOLARSHIP_APPLY"                 # 장학금 신청·기간·서류
    SCHOLARSHIP_QUALIFICATION = "SCHOLARSHIP_QUALIFICATION" # 장학금 자격·기준·금액
    TUITION_BENEFIT = "TUITION_BENEFIT"                     # 등록금 반환·납부·분납
    # GENERAL 분할
    CERTIFICATE = "CERTIFICATE"                             # 증명서·발급
    CONTACT = "CONTACT"                                     # 학과사무실·교직원·연락처
    FACILITY = "FACILITY"                                   # 캠퍼스 시설·포털·계정


class LegacyIntent(str, Enum):
    """구 Intent 분류 (multi-task 1 이전, 11개) — 영구 격리.

    사용자 결정(2026-05-11): DB(`chat_messages.intent`), 평가 리포트, JSONL 로그의
    직렬화 호환을 위해 영구 유지. 파이프라인 내부 로직에서는 사용하지 말 것 —
    신 Intent enum 사용. 새 코드는 `to_legacy_intent()` / `from_legacy_intent()`로 변환.
    """
    GRADUATION_REQ = "GRADUATION_REQ"
    EARLY_GRADUATION = "EARLY_GRADUATION"
    REGISTRATION = "REGISTRATION"
    SCHEDULE = "SCHEDULE"
    COURSE_INFO = "COURSE_INFO"
    MAJOR_CHANGE = "MAJOR_CHANGE"
    ALTERNATIVE = "ALTERNATIVE"
    SCHOLARSHIP = "SCHOLARSHIP"
    LEAVE_OF_ABSENCE = "LEAVE_OF_ABSENCE"
    TRANSCRIPT = "TRANSCRIPT"
    GENERAL = "GENERAL"


# ── Intent ↔ LegacyIntent 매핑 ───────────────────────────────────
# 직렬화·역직렬화는 value 문자열 기반. enum 간 비교가 아닌 .value로 일관 처리.

# 신 Intent → 구 LegacyIntent (DB·로그·평가 리포트 출력 시 사용)
_NEW_TO_LEGACY: dict[str, str] = {
    "GRADUATION_REQ":            "GRADUATION_REQ",
    "EARLY_GRADUATION":          "EARLY_GRADUATION",
    "REGISTRATION":              "REGISTRATION",       # 구 멤버 자체도 호환 매핑
    "REGISTRATION_GENERAL":      "REGISTRATION",
    "GRADE_OPTION":              "REGISTRATION",
    "REREGISTRATION":            "REGISTRATION",
    "SCHEDULE":                  "SCHEDULE",
    "COURSE_INFO":               "COURSE_INFO",
    "MAJOR_CHANGE":              "MAJOR_CHANGE",
    "ALTERNATIVE":               "ALTERNATIVE",
    "SCHOLARSHIP":               "SCHOLARSHIP",
    "SCHOLARSHIP_APPLY":         "SCHOLARSHIP",
    "SCHOLARSHIP_QUALIFICATION": "SCHOLARSHIP",
    "TUITION_BENEFIT":           "SCHOLARSHIP",
    "LEAVE_OF_ABSENCE":          "LEAVE_OF_ABSENCE",
    "TRANSCRIPT":                "TRANSCRIPT",
    "CERTIFICATE":               "GENERAL",
    "CONTACT":                   "GENERAL",
    "FACILITY":                  "GENERAL",
    "GENERAL":                   "GENERAL",
}

# 구 LegacyIntent → 신 Intent (DB·평가 리포트의 구 키를 신 코드에 흘릴 때)
# 1:N의 경우 가장 일반적/포괄적 카테고리를 선택 (보수적 디폴트).
_LEGACY_TO_NEW: dict[str, str] = {
    "GRADUATION_REQ":   "GRADUATION_REQ",
    "EARLY_GRADUATION": "EARLY_GRADUATION",
    "REGISTRATION":     "REGISTRATION_GENERAL",
    "SCHEDULE":         "SCHEDULE",
    "COURSE_INFO":      "COURSE_INFO",
    "MAJOR_CHANGE":     "MAJOR_CHANGE",
    "ALTERNATIVE":      "ALTERNATIVE",
    "SCHOLARSHIP":      "SCHOLARSHIP_QUALIFICATION",
    "LEAVE_OF_ABSENCE": "LEAVE_OF_ABSENCE",
    "TRANSCRIPT":       "TRANSCRIPT",
    "GENERAL":          "GENERAL",
}


def to_legacy_intent(intent) -> str:
    """신 Intent → 구 LegacyIntent value 문자열.

    DB(`chat_messages.intent`)·JSONL 로그·평가 리포트 직렬화 시 사용.
    매핑 실패 시 'GENERAL' 폴백.
    """
    key = intent.value if isinstance(intent, Intent) else str(intent)
    return _NEW_TO_LEGACY.get(key, LegacyIntent.GENERAL.value)


def from_legacy_intent(legacy) -> "Intent":
    """구 LegacyIntent value 문자열 → 신 Intent.

    DB·평가 리포트의 구 키를 신 코드에 다시 흘릴 때 사용.
    1:N 매핑은 가장 일반적인 신 카테고리로 폴백.
    """
    key = legacy.value if isinstance(legacy, LegacyIntent) else str(legacy)
    new_value = _LEGACY_TO_NEW.get(key, Intent.GENERAL.value)
    try:
        return Intent(new_value)
    except ValueError:
        return Intent.GENERAL


class QuestionType(str, Enum):
    """질문 유형 — 토픽(Intent)과 직교하는 추가 차원.
    Embedding 유사도 기반 분류로 vector/graph 가중치를 동적 변조.
    """
    OVERVIEW = "overview"         # 주제 개요 요청 (짧고 일반적)
    FACTOID = "factoid"           # 단순 사실 질문 (날짜, 금액, 수치)
    PROCEDURAL = "procedural"     # 절차/방법 질문
    REASONING = "reasoning"       # 조건 기반 추론


class PDFType(str, Enum):
    DIGITAL = "digital"
    SCANNED = "scanned"


@dataclass
class PageContent:
    """PDF에서 추출된 페이지 단위 콘텐츠"""
    page_number: int
    text: str
    tables: list = field(default_factory=list)       # markdown 문자열 리스트
    raw_tables: list = field(default_factory=list)   # 2D 배열 리스트 (원본)
    headers: list = field(default_factory=list)
    source_file: str = ""


@dataclass
class Chunk:
    """벡터 DB에 저장되는 텍스트 청크"""
    chunk_id: str
    text: str
    page_number: int
    source_file: str
    student_id: Optional[str] = None  # 인제스트 출처 기록용 (필터링 미사용)
    doc_type: str = ""
    cohort_from: int = 2016  # 이 청크가 적용되는 최소 학번 (포함)
    cohort_to: int = 2030    # 이 청크가 적용되는 최대 학번 (포함)
    semester: str = ""       # 학기 (예: "2026-1", "2025-2"), 빈 문자열 = 전 학기 공통
    student_types: str = ""  # 허용 학생유형 파이프 구분 문자열 "내국인|외국인|편입생". 빈 문자열 = 전체 허용
    metadata: dict = field(default_factory=dict)


@dataclass
class QueryAnalysis:
    """쿼리 분석 결과"""
    intent: Intent
    student_id: Optional[str] = None
    student_type: Optional[str] = None  # '내국인' | '외국인' | '편입생'
    grade: Optional[int] = None         # 추정 학년 (1~6), 프롬프트 컨텍스트 전용
    entities: dict = field(default_factory=dict)
    requires_graph: bool = False
    requires_vector: bool = True
    missing_info: list = field(default_factory=list)
    lang: str = "ko"  # 감지된 질문 언어: 'ko' | 'en'
    question_type: QuestionType = QuestionType.FACTOID  # 질문 유형 (Embedding 기반)
    matched_terms: list = field(default_factory=list)  # [{"ko": "수강신청", "en": "Course Registration"}]
    ko_query: Optional[str] = None  # EN 쿼리의 한국어 변환본 (그래프/FAQ 검색용)
    normalized_query: Optional[str] = None  # glossary 정규화된 쿼리 (학식→학생식당 등)


@dataclass
class SearchResult:
    """검색 결과 (Vector 또는 Graph)"""
    text: str
    score: float = 0.0
    source: str = ""
    page_number: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class MergedContext:
    """통합된 검색 컨텍스트"""
    vector_results: list = field(default_factory=list)
    graph_results: list = field(default_factory=list)
    formatted_context: str = ""
    total_tokens_estimate: int = 0
    direct_answer: str = ""
    source_urls: list = field(default_factory=list)
    # source_urls 형식: [{"title": "공지 제목", "url": "https://..."}, ...]
    # 원칙 2: 하이브리드 시스템(IDF·Cross-Encoder·RRF) 점수를 집약한 관련성 신호
    # 1.0 = direct_answer 확보, 0.0 = 관련 컨텍스트 없음
    context_confidence: float = 0.0


@dataclass
class ChatResponse:
    """최종 응답"""
    answer: str
    sources: list = field(default_factory=list)
    intent: Intent = Intent.GENERAL
    student_id: Optional[str] = None
    validation_passed: bool = True
    warnings: list = field(default_factory=list)
