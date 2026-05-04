"""
성적표 업로드/상태/삭제 엔드포인트.

chat_app.py:_handle_transcript_upload() (840~907줄) 로직을 이식.
SecureTranscriptStore, UploadValidator 등 기존 코드를 수정 없이 호출.

2026-04-16 개선:
- consent 강제 확인 (기존 silent fail 버그 수정)
- 로그인 사용자 자동 DB 저장 (user_transcripts) → 재로그인 후 복원 가능
"""

import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File

from backend.database import (
    upsert_user_transcript,
    get_user_transcript,
    delete_user_transcript,
)
from backend.routers.user import require_user_optional
from backend.session import session_store
from backend.schemas.transcript import (
    TranscriptStatus, UploadResponse,
    TranscriptAnalysisResponse, AnalysisCategory, SemesterSummary,
    RetakeCandidate, GraduationProjection, ActionItemResp,
)
from backend.utils.i18n import get_lang_from_session, api_msg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/transcript", tags=["transcript"])


def _profile_to_masked_json(profile: Any) -> str:
    """
    파싱된 StudentAcademicProfile을 PII 마스킹 후 JSON 직렬화.
    DB 영구 저장용 — 실명·학번 원본은 저장하지 않는다.
    """
    def _to_dict(obj):
        if is_dataclass(obj):
            return {k: _to_dict(v) for k, v in asdict(obj).items()}
        if isinstance(obj, list):
            return [_to_dict(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _to_dict(v) for k, v in obj.items()}
        return obj

    data = _to_dict(profile)
    # 민감 필드 마스킹 (성명은 이미 PIIRedactor.mask_name 처리됐으나 이중 확인)
    if isinstance(data, dict) and "profile" in data and isinstance(data["profile"], dict):
        # 학번 원본은 입학연도만 유지 (기존 SecureTranscriptStore 정책과 동일)
        data["profile"]["학번"] = ""
        # 성명은 masked_name만 별도 필드로 보관, 원본은 공란
        data["profile"]["성명"] = ""
    return json.dumps(data, ensure_ascii=False)


@router.post("/upload", response_model=UploadResponse)
async def upload_transcript(
    session_id: str = Query(...),
    file: UploadFile = File(...),
    user_payload: Optional[dict] = Depends(require_user_optional),
):
    """
    성적표 업로드 → 파싱 → 세션 저장 + (로그인 시) DB 영구 저장.
    """
    from app.transcript import TranscriptParser, TranscriptVersionManager
    from app.transcript.security import (
        SecureTranscriptStore,
        UploadValidator,
        PIIRedactor,
        audit_log,
    )

    # backend 재시작 시 세션이 사라졌을 수 있음 — 자동 재생성
    session_id, data = session_store.get_or_create(session_id)

    # 2026-04-16: consent 강제 확인 (기존 store() 내부 silent fail 방지)
    if not SecureTranscriptStore.has_consent(data):
        audit_log("UPLOAD_NO_CONSENT", session_id, "")
        return UploadResponse(
            ok=False,
            error="개인정보 처리 동의가 필요합니다. /api/transcript/consent 를 먼저 호출해 주세요.",
        )

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

    # 5) 보안 저장 (세션 메모리 — 성명/학번 원본 삭제됨)
    SecureTranscriptStore.store(data, profile, session_id)
    TranscriptVersionManager.store_snapshot(profile, data)

    # 6) user_profile 자동 갱신
    user_profile = {
        "student_id": profile.profile.입학연도,
        "department": profile.profile.전공 or profile.profile.학부과,
        "student_type": profile.profile.student_type or "내국인",
    }
    session_store.update(session_id, "user_profile", user_profile)

    c = profile.credits

    # 7) 로그인 사용자: DB 영구 저장 (PII 마스킹 JSON)
    if user_payload and user_payload.get("user_id"):
        try:
            masked_json = _profile_to_masked_json(profile)
            uid = int(user_payload["user_id"])
            upsert_user_transcript(
                user_id=uid,
                parsed_json=masked_json,
                masked_name=masked,
                gpa=float(c.평점평균 or 0),
                total_acquired=float(c.총_취득학점 or 0),
                total_required=float(c.총_졸업기준 or 0),
                total_shortage=float(c.총_부족학점 or 0),
            )
            audit_log("UPLOAD_DB_SAVED", session_id, f"user_id={uid}")
        except Exception as exc:
            # DB 저장 실패는 업로드 자체를 실패시키지 않는다 (세션 저장은 이미 성공)
            logger.error("user_transcripts 저장 실패: %s", exc, exc_info=True)
            audit_log("UPLOAD_DB_FAILED", session_id, str(exc)[:80])

    # 응답 데이터
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
async def transcript_status(
    session_id: str = Query(...),
    user_payload: Optional[dict] = Depends(require_user_optional),
):
    """
    성적표 상태 조회.
    - 세션에 있으면 세션 우선
    - 세션에 없고 로그인 사용자면 DB에서 캐시 요약만 복원해 즉시 반환
      (전체 파싱 객체 복원은 chat 요청 시 _enrich_analysis에서 on-demand)
    """
    from app.transcript.security import SecureTranscriptStore

    data = session_store.get(session_id)
    if data is not None:
        transcript = SecureTranscriptStore.retrieve(data)
        if transcript:
            remaining = SecureTranscriptStore.remaining_seconds(data)
            c = transcript.credits
            p = transcript.profile
            masked = getattr(transcript, "_masked_name", "등록됨")
            progress_pct = 0
            if c.총_졸업기준 > 0:
                progress_pct = min(100, int((c.총_취득학점 / c.총_졸업기준) * 100))
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

    # 세션 없음 — 로그인 사용자면 DB 복원 시도 (요약만, 세션 ttl은 생략)
    if user_payload and user_payload.get("user_id"):
        row = get_user_transcript(int(user_payload["user_id"]))
        if row:
            progress_pct = 0
            if row["total_required"] > 0:
                progress_pct = min(
                    100,
                    int((row["total_acquired"] / row["total_required"]) * 100),
                )
            return TranscriptStatus(
                has_transcript=True,
                remaining_seconds=-1,  # DB 복원분은 TTL 없음 (-1 = 영구)
                masked_name=row["masked_name"],
                gpa=float(row["gpa"] or 0),
                total_acquired=float(row["total_acquired"] or 0),
                total_required=float(row["total_required"] or 0),
                total_shortage=float(row["total_shortage"] or 0),
                progress_pct=progress_pct,
                dual_major="",
                dual_shortage=0,
            )

    return TranscriptStatus()


@router.delete("")
async def delete_transcript(
    session_id: str = Query(...),
    user_payload: Optional[dict] = Depends(require_user_optional),
):
    """성적표 즉시 삭제 — 세션 + (로그인) DB 양쪽 제거."""
    from app.transcript.security import SecureTranscriptStore

    data = session_store.get(session_id)
    if data is not None:
        SecureTranscriptStore.destroy(data)

    if user_payload and user_payload.get("user_id"):
        try:
            delete_user_transcript(int(user_payload["user_id"]))
        except Exception as exc:
            logger.error("user_transcripts 삭제 실패: %s", exc, exc_info=True)

    return {"ok": True}


@router.post("/consent")
async def update_consent(session_id: str = Query(...), consent: bool = True):
    """개인정보 동의 설정/해제."""
    from app.transcript.security import SecureTranscriptStore

    # backend 재시작 시 세션이 사라졌을 수 있음 — 자동 재생성
    session_id, data = session_store.get_or_create(session_id)

    if consent:
        SecureTranscriptStore.grant_consent(data, session_id)
    else:
        SecureTranscriptStore.revoke_consent(data)
    return {"ok": True}


# ── 학사 리포트 분석 (2026-04-16) ──────────────────────────────

@router.get("/analysis", response_model=TranscriptAnalysisResponse)
async def transcript_analysis(
    session_id: str = Query(...),
    user_payload: Optional[dict] = Depends(require_user_optional),
):
    """
    학사 리포트 페이지용 구조화 분석.

    - 세션에 transcript 있으면 우선 사용
    - 없고 로그인 사용자면 DB에서 복원 (chat.py _rehydrate_transcript_from_json 재사용)
    - TranscriptAnalyzer.build_full_analysis() + action_rules.evaluate_all()
    - LLM 호출 0건 (4원칙 #2)
    """
    from app.config import settings
    from app.transcript.security import SecureTranscriptStore
    from app.transcript.analyzer import TranscriptAnalyzer
    from app.transcript.action_rules import RuleContext, evaluate_all
    from backend.dependencies import get_router

    data = session_store.get(session_id)
    transcript = None
    if data is not None:
        transcript = SecureTranscriptStore.retrieve(data)

    # 세션 없음 → 로그인 사용자면 DB 복원
    if not transcript and user_payload and user_payload.get("user_id"):
        try:
            row = get_user_transcript(int(user_payload["user_id"]))
            if row and row.get("parsed_json"):
                from backend.routers.chat import _rehydrate_transcript_from_json
                transcript = _rehydrate_transcript_from_json(row["parsed_json"])
        except Exception as exc:
            logger.warning("transcript DB 복원 실패 (analysis): %s", exc)

    if not transcript:
        return TranscriptAnalysisResponse(has_transcript=False)

    # Graph 조회 (재수강·조기졸업 등 동적 규정)
    graph = None
    try:
        router_inst = get_router()
        graph = getattr(router_inst, "academic_graph", None)
    except Exception:
        pass

    analyzer = TranscriptAnalyzer(transcript, graph=graph)
    try:
        analysis = analyzer.build_full_analysis()
    except Exception as exc:
        logger.exception("build_full_analysis 실패: %s", exc)
        raise HTTPException(status_code=500, detail=api_msg("analysis_failed", get_lang_from_session(data)))

    # Rule engine (plugin registry 자동 호출)
    ctx = RuleContext(
        profile=transcript.profile,
        credits=transcript.credits,
        courses=transcript.courses,
        analyzer=analyzer,
        graph=graph,
        settings=settings.transcript_rules,
    )
    actions = evaluate_all(ctx)

    # Profile 요약 (마스킹 포함)
    masked_name = getattr(transcript, "_masked_name", "")
    profile_summary = {
        "masked_name": masked_name,
        "department": transcript.profile.전공 or transcript.profile.학부과,
        "student_group": transcript.profile.student_group,
        "grade": transcript.profile.학년,
        "semesters_completed": transcript.profile.이수학기,
        "student_type": transcript.profile.student_type,
        "dual_major": transcript.profile.복수전공 or "",
        "minor": transcript.profile.부전공 or "",
    }

    return TranscriptAnalysisResponse(
        has_transcript=True,
        profile=profile_summary,
        summary=analysis["summary"],
        categories=[AnalysisCategory(**c) for c in analysis["categories"]],
        semesters=[SemesterSummary(**s) for s in analysis["semesters"]],
        grade_distribution=analysis["grade_distribution"],
        retake_candidates=[RetakeCandidate(**c) for c in analysis["retake_candidates"]],
        registration_limit=analysis.get("registration_limit", {}),
        dual_major=analysis.get("dual_major", {}),
        graduation=GraduationProjection(**analysis["graduation"]),
        actions=[ActionItemResp(**a.to_dict()) for a in actions],
    )
