"""
BUFS Academic Chatbot - 데이터 모델
파이프라인 전체에서 사용되는 데이터 구조를 정의합니다.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Intent(str, Enum):
    GRADUATION_REQ = "GRADUATION_REQ"
    REGISTRATION = "REGISTRATION"
    SCHEDULE = "SCHEDULE"
    COURSE_INFO = "COURSE_INFO"
    MAJOR_CHANGE = "MAJOR_CHANGE"
    ALTERNATIVE = "ALTERNATIVE"
    GENERAL = "GENERAL"


class PDFType(str, Enum):
    DIGITAL = "digital"
    SCANNED = "scanned"


@dataclass
class PageContent:
    """PDF에서 추출된 페이지 단위 콘텐츠"""
    page_number: int
    text: str
    tables: list = field(default_factory=list)
    headers: list = field(default_factory=list)
    source_file: str = ""


@dataclass
class Chunk:
    """벡터 DB에 저장되는 텍스트 청크"""
    chunk_id: str
    text: str
    page_number: int
    source_file: str
    student_id: Optional[str] = None
    doc_type: str = ""
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


@dataclass
class ChatResponse:
    """최종 응답"""
    answer: str
    sources: list = field(default_factory=list)
    intent: Intent = Intent.GENERAL
    student_id: Optional[str] = None
    validation_passed: bool = True
    warnings: list = field(default_factory=list)
