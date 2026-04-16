"""Pydantic schemas for user authentication."""

from typing import Optional
from pydantic import BaseModel, Field


class UserRegister(BaseModel):
    username: str = Field(
        ..., min_length=4, max_length=20,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="영숫자 및 밑줄만 허용 (4-20자)",
    )
    nickname: str = Field(
        ..., min_length=2, max_length=20,
        description="표시 닉네임 (2-20자)",
    )
    password: str = Field(
        ..., min_length=8, max_length=64,
        description="비밀번호 (8-64자)",
    )
    student_id: str = Field(
        ..., min_length=4, max_length=4,
        description="입학연도 (예: 2023)",
    )
    department: str = Field(
        ..., min_length=1,
        description="학과/전공",
    )
    student_type: str = Field(
        default="내국인",
        description="내국인/외국인/편입생",
    )


class UserLogin(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserInfo(BaseModel):
    id: int
    username: str
    nickname: str
    student_id: str
    department: str
    student_type: str


class AuthToken(BaseModel):
    token: str
    expires_at: str
    user: UserInfo


# ── 채팅 이력 (본인 전용) ──────────────────────────────────

class ChatHistoryItem(BaseModel):
    id: int
    session_id: str
    question: str
    answer: str
    intent: str = ""
    rating: Optional[int] = None
    created_at: str


class ChatHistoryResponse(BaseModel):
    total: int
    items: list[ChatHistoryItem] = Field(default_factory=list)


# ── 알림 ───────────────────────────────────────────────────

class NotificationItem(BaseModel):
    id: int
    kind: str                  # 'faq_answered' | 'faq_updated'
    faq_id: Optional[str] = None
    chat_message_id: Optional[int] = None
    title: str
    body: str = ""
    read: bool = False
    created_at: str


class NotificationListResponse(BaseModel):
    unread_count: int
    items: list[NotificationItem] = Field(default_factory=list)


class UnreadCountResponse(BaseModel):
    unread_count: int
