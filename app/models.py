"""
BUFS Academic Chatbot - 데이터 모델
파이프라인 전체에서 사용되는 데이터 구조를 정의합니다.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Intent(str, Enum):
    GRADUATION_REQ = "GRADUATION_REQ"
    EARLY_GRADUATION = "EARLY_GRADUATION"   # 조기졸업 신청·자격·기준
    REGISTRATION = "REGISTRATION"
    SCHEDULE = "SCHEDULE"
    COURSE_INFO = "COURSE_INFO"
    MAJOR_CHANGE = "MAJOR_CHANGE"
    ALTERNATIVE = "ALTERNATIVE"
    SCHOLARSHIP = "SCHOLARSHIP"             # 장학금 신청·자격·유형·금액
    LEAVE_OF_ABSENCE = "LEAVE_OF_ABSENCE"   # 휴학/복학 신청·기간·방법·서류
    TRANSCRIPT = "TRANSCRIPT"               # 성적표 기반 개인 질문
    GENERAL = "GENERAL"


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
    metadata: dict = field(default_factory=dict)


@dataclass
class QueryAnalysis:
    """쿼리 분석 결과"""
    intent: Intent
    student_id: Optional[str] = None
    student_type: Optional[str] = None  # '내국인' | '외국인' | '편입생'
    entities: dict = field(default_factory=dict)
    requires_graph: bool = False
    requires_vector: bool = True
    missing_info: list = field(default_factory=list)
    lang: str = "ko"  # 감지된 질문 언어: 'ko' | 'en'
    question_type: QuestionType = QuestionType.FACTOID  # 질문 유형 (Embedding 기반)
    matched_terms: list = field(default_factory=list)  # [{"ko": "수강신청", "en": "Course Registration"}]
    ko_query: Optional[str] = None  # EN 쿼리의 한국어 변환본 (그래프/FAQ 검색용)


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
