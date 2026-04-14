"""
피드백 + 별점 엔드포인트.

chat_app.py:_save_feedback() + _render_rating() 로직 이식.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter

from backend.session import session_store
from backend.schemas.feedback import FeedbackCreate, RatingUpdate
from backend.dependencies import get_chat_logger

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["feedback"])

_FEEDBACK_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "feedback"


@router.post("/feedback")
async def submit_feedback(body: FeedbackCreate):
    """사용자 자유 피드백 저장 → data/feedback/feedback.jsonl."""
    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "text": body.text,
        "timestamp": datetime.now().isoformat(),
        "session_id": body.session_id,
    }
    with open(_FEEDBACK_DIR / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"ok": True}


@router.post("/rating")
async def submit_rating(body: RatingUpdate):
    """메시지 별점(1~5) 저장."""
    data = session_store.get(body.session_id)
    if data is None:
        return {"ok": False, "error": "세션을 찾을 수 없습니다."}

    messages = data.get("messages", [])
    idx = body.message_index
    if idx < 0 or idx >= len(messages):
        return {"ok": False, "error": "유효하지 않은 메시지 인덱스입니다."}

    # 세션 메시지에 별점 기록
    messages[idx]["rated"] = True
    messages[idx]["rating"] = body.rating
    session_store.update(body.session_id, "messages", messages)

    # 대응하는 질문(바로 앞 user 메시지) 찾기
    question = ""
    for i in range(idx - 1, -1, -1):
        if messages[i].get("role") == "user":
            question = messages[i].get("content", "")
            break

    # 로그 파일에 별점 업데이트
    try:
        chat_logger = get_chat_logger()
        if chat_logger:
            chat_logger.update_rating(
                session_id=body.session_id,
                question=question,
                rating=body.rating,
            )
    except Exception:
        pass

    return {"ok": True}
