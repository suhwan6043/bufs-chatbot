"""세션 API Pydantic 스키마."""

from typing import Optional
from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    lang: str = Field("ko", pattern="^(ko|en)$")


class SessionInfo(BaseModel):
    session_id: str
    lang: str = "ko"
    user_profile: Optional[dict] = None
    has_transcript: bool = False
    messages_count: int = 0


class ProfileUpdate(BaseModel):
    student_id: str = Field(..., description="입학연도 (예: 2023)")
    department: str = Field("", description="학과/학부")
    student_type: str = Field("내국인", description="내국인/외국인/편입생")
