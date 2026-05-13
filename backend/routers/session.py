"""
세션 CRUD 엔드포인트.

Streamlit st.session_state 대체 — 서버 사이드 세션 관리.
"""

from fastapi import APIRouter, HTTPException, Request

from backend.session import session_store
from backend.schemas.session import SessionCreate, SessionInfo, ProfileUpdate
from backend.utils.i18n import get_lang_from_request, api_msg, normalize_student_type

router = APIRouter(prefix="/api/session", tags=["session"])


@router.post("", response_model=SessionInfo)
async def create_session(body: SessionCreate):
    """새 세션 생성."""
    sid = session_store.create(lang=body.lang)
    data = session_store.get(sid)
    return SessionInfo(
        session_id=sid,
        lang=data.get("lang", "ko"),
        messages_count=0,
    )


@router.get("/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str, request: Request):
    """세션 정보 조회."""
    data = session_store.get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail=api_msg("session_not_found", get_lang_from_request(request)))
    return SessionInfo(
        session_id=session_id,
        lang=data.get("lang", "ko"),
        user_profile=data.get("user_profile"),
        has_transcript=bool(data.get("_transcript_data")),
        messages_count=len(data.get("messages", [])),
    )


@router.put("/{session_id}/profile")
async def update_profile(session_id: str, body: ProfileUpdate, request: Request):
    """프로필 설정 (온보딩). student_type은 KO/EN 양쪽 수용 → KO 정규화 후 저장."""
    data = session_store.get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail=api_msg("session_not_found", get_lang_from_request(request)))
    normalized_type = normalize_student_type(body.student_type) or "내국인"
    profile = {
        "student_id": body.student_id,
        "department": body.department,
        "student_type": normalized_type,
    }
    session_store.update(session_id, "user_profile", profile)
    return {"ok": True}


@router.put("/{session_id}/lang")
async def update_lang(session_id: str, request: Request, lang: str = "ko"):
    """언어 변경."""
    if lang not in ("ko", "en"):
        raise HTTPException(status_code=400, detail="lang must be 'ko' or 'en'")
    data = session_store.get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail=api_msg("session_not_found", get_lang_from_request(request)))
    session_store.update(session_id, "lang", lang)
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """세션 삭제."""
    session_store.delete(session_id)
    return {"ok": True}
