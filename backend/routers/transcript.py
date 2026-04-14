"""
성적표 업로드/상태/삭제 엔드포인트.

chat_app.py:_handle_transcript_upload() (840~907줄) 로직을 이식.
SecureTranscriptStore, UploadValidator 등 기존 코드를 수정 없이 호출.
"""

import logging
from fastapi import APIRouter, HTTPException, Query, UploadFile, File

from backend.session import session_store
from backend.schemas.transcript import TranscriptStatus, UploadResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/transcript", tags=["transcript"])


@router.post("/upload", response_model=UploadResponse)
async def upload_transcript(
    session_id: str = Query(...),
    file: UploadFile = File(...),
):
    """
    성적표 업로드 → 파싱 → 세션 저장.

    chat_app.py:_handle_transcript_upload() 1:1 이식.
    """
    from app.transcript import TranscriptParser, TranscriptVersionManager
    from app.transcript.security import (
        SecureTranscriptStore,
        UploadValidator,
        PIIRedactor,
        audit_log,
    )

    data = session_store.get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    file_bytes = await file.read()

    # 1) 파일 보안 검증
    ok, err = UploadValidator.validate(file_bytes, file.filename or "unknown")
    if not ok:
        audit_log("UPLOAD_REJECTED", session_id, err)
        return UploadResponse(ok=False, error=f"파일 검증 실패: {err}")

    safe_filename = UploadValidator.sanitize_filename(file.filename or "unknown")

    # 2) 파싱
    try:
        parser = TranscriptParser()
        profile = parser.parse(file_bytes, safe_filename)
    except ModuleNotFoundError as e:
        audit_log("PARSE_MODULE_MISSING", session_id, e.name or "")
        return UploadResponse(ok=False, error=f"서버 의존성 누락: {e.name}")
    except ValueError as e:
        audit_log("PARSE_INVALID", session_id, type(e).__name__)
        return UploadResponse(ok=False, error=f"파일 형식 오류: {e}")
    except Exception:
        audit_log("PARSE_FAILED", session_id, "Exception")
        logger.exception("성적표 파싱 실패")
        return UploadResponse(ok=False, error="성적표 파싱에 실패했습니다.")
    finally:
        del file_bytes

    # 3) 마스킹 이름
    masked = PIIRedactor.mask_name(profile.profile.성명)

    # 4) 버전 비교 (기존 성적표가 있으면)
    old = SecureTranscriptStore.retrieve(data)
    if old:
        TranscriptVersionManager.detect_diff(old, profile)

    # 5) 보안 저장 (성명/학번 원본 삭제됨)
    SecureTranscriptStore.store(data, profile, session_id)
    TranscriptVersionManager.store_snapshot(profile, data)

    # 6) user_profile 자동 갱신
    user_profile = {
        "student_id": profile.profile.입학연도,
        "department": profile.profile.전공 or profile.profile.학부과,
        "student_type": profile.profile.student_type or "내국인",
    }
    session_store.update(session_id, "user_profile", user_profile)

    # 응답 데이터
    c = profile.credits
    return UploadResponse(
        ok=True,
        masked_name=masked,
        credits={
            "gpa": c.평점평균,
            "total_acquired": c.총_취득학점,
            "total_required": c.총_졸업기준,
            "total_shortage": c.총_부족학점,
        },
        profile={
            "student_id": user_profile["student_id"],
            "department": user_profile["department"],
            "student_type": user_profile["student_type"],
        },
    )


@router.get("/status", response_model=TranscriptStatus)
async def transcript_status(session_id: str = Query(...)):
    """성적표 상태 조회 (남은 시간, 학점 요약)."""
    from app.transcript.security import SecureTranscriptStore

    data = session_store.get(session_id)
    if data is None:
        return TranscriptStatus()

    transcript = SecureTranscriptStore.retrieve(data)
    if not transcript:
        return TranscriptStatus()

    remaining = SecureTranscriptStore.remaining_seconds(data)
    c = transcript.credits
    p = transcript.profile
    masked = getattr(transcript, "_masked_name", "등록됨")

    progress_pct = 0
    if c.총_졸업기준 > 0:
        progress_pct = min(100, int((c.총_취득학점 / c.총_졸업기준) * 100))

    # 복수전공 정보
    dual_major = p.복수전공 or ""
    dual_shortage = 0
    for cat in c.categories:
        if "복수전공" in cat.name or "다전공" in cat.name:
            dual_shortage = cat.부족학점
            break

    return TranscriptStatus(
        has_transcript=True,
        remaining_seconds=remaining,
        masked_name=masked,
        gpa=c.평점평균,
        total_acquired=c.총_취득학점,
        total_required=c.총_졸업기준,
        total_shortage=c.총_부족학점,
        progress_pct=progress_pct,
        dual_major=dual_major,
        dual_shortage=dual_shortage,
    )


@router.delete("")
async def delete_transcript(session_id: str = Query(...)):
    """성적표 즉시 삭제."""
    from app.transcript.security import SecureTranscriptStore

    data = session_store.get(session_id)
    if data is not None:
        SecureTranscriptStore.destroy(data)
    return {"ok": True}


@router.post("/consent")
async def update_consent(session_id: str = Query(...), consent: bool = True):
    """개인정보 동의 설정/해제."""
    from app.transcript.security import SecureTranscriptStore

    data = session_store.get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    if consent:
        SecureTranscriptStore.grant_consent(data, session_id)
    else:
        SecureTranscriptStore.revoke_consent(data)
    return {"ok": True}
