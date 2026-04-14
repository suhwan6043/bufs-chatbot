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

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from backend.database import create_user, authenticate_user, get_user_by_id
from backend.schemas.user import UserRegister, UserLogin, UserInfo, AuthToken

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


def _check_rate_limit(ip: str):
    """Raise 429 if IP has too many failed login attempts."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Clean old attempts
    attempts = [t for t in attempts if now - t < _LOCKOUT_SECONDS]
    _login_attempts[ip] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="로그인 시도 횟수 초과. 15분 후 재시도하세요.",
        )


def _record_failed_attempt(ip: str):
    """Record a failed login attempt for rate limiting."""
    _login_attempts.setdefault(ip, []).append(time.time())


# ── Auth dependency ──

async def require_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Require valid user JWT. Returns user payload."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    payload = _verify_user_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="토큰이 만료되었거나 유효하지 않습니다.")
    return payload


# ── Endpoints ──

@router.post("/register", response_model=AuthToken)
async def register(body: UserRegister):
    """Register a new user account."""
    # Validate student_type
    if body.student_type not in ("내국인", "외국인", "편입생"):
        raise HTTPException(status_code=400, detail="유효하지 않은 학생 유형입니다.")

    user = create_user(
        username=body.username,
        nickname=body.nickname,
        password=body.password,  # hashed inside create_user
        student_id=body.student_id,
        department=body.department,
        student_type=body.student_type,
    )
    if user is None:
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")

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
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    user = authenticate_user(body.username, body.password)
    if user is None:
        _record_failed_attempt(client_ip)
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 잘못되었습니다.")

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
async def get_me(payload: dict = Depends(require_user)):
    """Get current user info from JWT."""
    user = get_user_by_id(payload["user_id"])
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return UserInfo(**user)


@router.post("/logout")
async def logout(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """Blacklist the current token."""
    if credentials and credentials.credentials:
        _blacklisted_tokens.add(credentials.credentials)
    return {"ok": True}
