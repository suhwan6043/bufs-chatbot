"""
User authentication router — registration, login, profile.

Security measures:
- PBKDF2-SHA256 password hashing (600K iterations)
- IP-based brute-force lockout (5 attempts / 15 min)
- JWT tokens with HMAC-SHA256 signature
- Timing-safe comparisons throughout
- No password logging
"""

import base64
import hashlib
import hmac
import json as _json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from backend.database import (
    create_user, authenticate_user, get_user_by_id,
    list_chat_messages, count_chat_messages,
    list_notifications, count_unread_notifications,
    mark_notification_read, mark_all_notifications_read,
)
from backend.session import session_store
from backend.schemas.user import (
    UserRegister, UserLogin, UserInfo, AuthToken,
    ChatHistoryItem, ChatHistoryResponse,
    NotificationItem, NotificationListResponse, UnreadCountResponse,
)
from backend.utils.i18n import get_lang_from_request, api_msg, normalize_student_type

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/user", tags=["user"])
security = HTTPBearer(auto_error=False)

# ── JWT Config ──
_JWT_SECRET = os.getenv("USER_JWT_SECRET", "camchat_user_secret_change_in_production_2025")
_JWT_EXPIRE_HOURS = 24

# ── Brute-force protection ──
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 900  # 15 minutes
_login_attempts: dict[str, list[float]] = {}
_blacklisted_tokens: set[str] = set()


# ── JWT helpers ──

def _create_user_token(user: dict) -> tuple[str, str]:
    """Create JWT for authenticated user."""
    exp = datetime.utcnow() + timedelta(hours=_JWT_EXPIRE_HOURS)
    payload = {
        "user_id": user["id"],
        "username": user["username"],
        "nickname": user["nickname"],
        "exp": exp.isoformat(),
        "iat": datetime.utcnow().isoformat(),
    }
    payload_b64 = base64.urlsafe_b64encode(
        _json.dumps(payload).encode()
    ).decode().rstrip("=")
    sig = hmac.new(
        _JWT_SECRET.encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()[:32]
    token = f"{payload_b64}.{sig}"
    return token, exp.isoformat()


def _verify_user_token(token: str) -> Optional[dict]:
    """Verify JWT and return payload or None."""
    if token in _blacklisted_tokens:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        expected = hmac.new(
            _JWT_SECRET.encode(), payload_b64.encode(), hashlib.sha256
        ).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(padded))
        if datetime.utcnow() > datetime.fromisoformat(payload["exp"]):
            return None
        return payload
    except Exception:
        return None


def _check_rate_limit(ip: str, lang: str = "ko"):
    """Raise 429 if IP has too many failed login attempts."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Clean old attempts
    attempts = [t for t in attempts if now - t < _LOCKOUT_SECONDS]
    _login_attempts[ip] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=api_msg("rate_limit_login", lang),
        )


def _record_failed_attempt(ip: str):
    """Record a failed login attempt for rate limiting."""
    _login_attempts.setdefault(ip, []).append(time.time())


# ── Auth dependency ──

async def require_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Require valid user JWT. Returns user payload."""
    lang = get_lang_from_request(request)
    if credentials is None:
        raise HTTPException(status_code=401, detail=api_msg("auth_required", lang))
    payload = _verify_user_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail=api_msg("token_invalid", lang))
    return payload


async def require_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """
    Optional JWT verification. Returns user payload if valid, else None.
    - 헤더 없음 → None
    - 헤더 있지만 토큰 무효 → None (에러 throw 안 함)

    로그인/비로그인 공통 업로드 경로(transcript, 피드백 등)에서 사용.
    """
    if credentials is None:
        return None
    try:
        return _verify_user_token(credentials.credentials)
    except Exception:
        return None


# ── Endpoints ──

@router.post("/register", response_model=AuthToken)
async def register(body: UserRegister, request: Request):
    """Register a new user account."""
    lang = get_lang_from_request(request)
    # Validate + normalize student_type (KO/EN 양쪽 수용 → KO 저장)
    normalized_type = normalize_student_type(body.student_type)
    if normalized_type is None:
        raise HTTPException(status_code=400, detail=api_msg("invalid_student_type", lang))

    user = create_user(
        username=body.username,
        nickname=body.nickname,
        password=body.password,  # hashed inside create_user
        student_id=body.student_id,
        department=body.department,
        student_type=normalized_type,
    )
    if user is None:
        raise HTTPException(status_code=409, detail=api_msg("username_taken", lang))

    logger.info("User registered: %s", body.username)
    token, expires_at = _create_user_token(user)
    return AuthToken(
        token=token,
        expires_at=expires_at,
        user=UserInfo(**user),
    )


@router.post("/login", response_model=AuthToken)
async def login(body: UserLogin, request: Request):
    """Authenticate user and return JWT."""
    lang = get_lang_from_request(request)
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip, lang=lang)

    user = authenticate_user(body.username, body.password)
    if user is None:
        _record_failed_attempt(client_ip)
        raise HTTPException(status_code=401, detail=api_msg("login_failed", lang))

    # Clear failed attempts on success
    _login_attempts.pop(client_ip, None)
    logger.info("User logged in: %s", body.username)

    token, expires_at = _create_user_token(user)
    return AuthToken(
        token=token,
        expires_at=expires_at,
        user=UserInfo(**user),
    )


@router.get("/me", response_model=UserInfo)
async def get_me(request: Request, payload: dict = Depends(require_user)):
    """Get current user info from JWT."""
    user = get_user_by_id(payload["user_id"])
    if user is None:
        raise HTTPException(status_code=404, detail=api_msg("user_not_found", get_lang_from_request(request)))
    return UserInfo(**user)


@router.post("/logout")
async def logout(
    session_id: Optional[str] = Query(default=None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """JWT blacklist + 서버 세션 메모리 purge.

    로그아웃 시 호출 측(프론트)이 현재 session_id를 넘기면, 해당 세션 엔트리를
    session_store에서 완전히 삭제한다 — transcript·messages·consent 모두 제거.
    """
    if credentials and credentials.credentials:
        _blacklisted_tokens.add(credentials.credentials)
    if session_id:
        session_store.delete(session_id)
    return {"ok": True}


# ── 본인 채팅 이력 ─────────────────────────────────────────

@router.get("/chat-history", response_model=ChatHistoryResponse)
async def get_chat_history(
    limit: int = 50,
    offset: int = 0,
    payload: dict = Depends(require_user),
):
    """본인이 로그인 상태에서 했던 질문·답변 이력 (최신순)."""
    uid = int(payload["user_id"])
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    items = list_chat_messages(uid, limit=limit, offset=offset)
    return ChatHistoryResponse(
        total=count_chat_messages(uid),
        items=[ChatHistoryItem(**it) for it in items],
    )


# ── 알림 ───────────────────────────────────────────────────

def _to_notification_item(row: dict) -> NotificationItem:
    return NotificationItem(
        id=int(row["id"]),
        kind=str(row["kind"]),
        faq_id=row.get("faq_id"),
        chat_message_id=row.get("chat_message_id"),
        title=str(row.get("title") or ""),
        body=str(row.get("body") or ""),
        read=row.get("read_at") is not None,
        created_at=str(row.get("created_at") or ""),
    )


@router.get("/notifications", response_model=NotificationListResponse)
async def get_notifications(
    limit: int = 50,
    payload: dict = Depends(require_user),
):
    """본인 알림 목록 (미읽음 우선)."""
    uid = int(payload["user_id"])
    rows = list_notifications(uid, limit=limit)
    return NotificationListResponse(
        unread_count=count_unread_notifications(uid),
        items=[_to_notification_item(r) for r in rows],
    )


@router.get("/notifications/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(payload: dict = Depends(require_user)):
    """페이지 방문 시 호출할 경량 엔드포인트 — 미읽음 개수만 반환."""
    return UnreadCountResponse(unread_count=count_unread_notifications(int(payload["user_id"])))


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: int,
    request: Request,
    payload: dict = Depends(require_user),
):
    """개별 알림을 읽음 처리. 소유자 검증은 DB 헬퍼에서 user_id 매칭으로 수행."""
    uid = int(payload["user_id"])
    ok = mark_notification_read(notification_id, uid)
    if not ok:
        raise HTTPException(status_code=404, detail=api_msg("notification_not_found", get_lang_from_request(request)))
    return {"ok": True}


@router.post("/notifications/read-all")
async def mark_all_read(payload: dict = Depends(require_user)):
    """전체 읽음 처리 — 반환값: 갱신된 행 수."""
    uid = int(payload["user_id"])
    updated = mark_all_notifications_read(uid)
    return {"ok": True, "updated": updated}
