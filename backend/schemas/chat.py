"""채팅 API Pydantic 스키마."""

from typing import Any, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """POST /api/chat 요청 바디."""
    session_id: str = Field(..., description="세션 ID")
    question: str = Field(..., min_length=1, max_length=2000, description="사용자 질문")


class SourceURL(BaseModel):
    title: str = ""
    url: str = ""


class SearchResultItem(BaseModel):
    text: str = ""
    score: float = 0.0
    source: str = ""
    page_number: int = 0
    doc_type: str = ""
    in_context: bool = False


class ChatResponse(BaseModel):
    """POST /api/chat 응답 + SSE done 이벤트 페이로드."""
    answer: str
    source_urls: list[SourceURL] = Field(default_factory=list)
    results: list[SearchResultItem] = Field(default_factory=list)
    intent: str = ""
    duration_ms: int = 0


class ChatStreamEvent(BaseModel):
    """SSE 이벤트 데이터 (token/clear/done/error)."""
    event: str  # "token", "clear", "done", "error"
    data: dict[str, Any] = Field(default_factory=dict)
