"""성적표 API Pydantic 스키마."""

from typing import Optional
from pydantic import BaseModel


class TranscriptStatus(BaseModel):
    has_transcript: bool = False
    remaining_seconds: int = 0
    masked_name: str = ""
    gpa: float = 0.0
    total_acquired: int = 0
    total_required: int = 0
    total_shortage: int = 0
    progress_pct: int = 0
    dual_major: str = ""
    dual_shortage: int = 0


class UploadResponse(BaseModel):
    ok: bool = True
    masked_name: str = ""
    credits: Optional[dict] = None
    profile: Optional[dict] = None
    error: str = ""
