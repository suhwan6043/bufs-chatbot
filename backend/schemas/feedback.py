"""피드백/별점 API Pydantic 스키마."""

from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    session_id: str
    text: str = Field(..., min_length=1, max_length=5000)


class RatingUpdate(BaseModel):
    session_id: str
    message_index: int = Field(..., ge=0, description="메시지 인덱스")
    rating: int = Field(..., ge=1, le=5, description="1~5 별점")
