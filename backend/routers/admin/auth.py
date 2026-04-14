"""
관리자 JWT 인증 — pages/admin.py _verify_password() + _check_auth() 이식.

보안:
  1. hmac.compare_digest + SHA-256 (타이밍 안전 비교)
  2. IP별 브루트포스 잠금 (5회 실패 → 15분)
  3. JWT 토큰 30분 만료
  4. 감사 로그
"""

import hashlib
import hmac
import os
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings, _ADMIN_PW_DEFAULT
from backend.schemas.admin import AdminLogin, AdminToken

logger = logging.getLogger(__name__)
router = APIRouter()

# JWT 설정
_JWT_SECRET = os.getenv("JWT_SECRET", "camchat-admin-secret-change-me")
_JWT_EXPIRE_MIN = int(os.getenv("JWT_EXPIRE_MINUTES", "30"))
_MAX_ATTEMPTS = settings.admin.max_login_attempts
_LOCKOUT_SECS = settings.admin.lockout_minutes * 60

# 브루트포스 추적 (IP 기반)
_failed_attempts: dict[str, int] = {}
_lockout_until: dict[str, float] = {}

# 토큰 블랙리스트 (로그아웃)
_blacklisted_tokens: set[str] = set()

security = HTTPBearer(auto_error=False)


# ── 감사 로그 ──
def _audit(action: str, detail: str = "") -> None:
    log_dir = Path(settings.graph.graph_path).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {action}"
    if detail:
        line += f" | {detail}"
    try:
        with open(log_dir / "admin_audit.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ── JWT 토큰 (경량: python-jose 없이 수동 구현) ──
import base64, json as _json

def _create_token() -> tuple[str, str]:
    """간단 JWT-like 토큰 생성 (HS256). (token, expires_at)."""
    exp = datetime.utcnow() + timedelta(minutes=_JWT_EXPIRE_MIN)
    payload = {"admin": True, "exp": exp.isoformat(), "iat": datetime.utcnow().isoformat()}
    payload_b64 = base64.urlsafe_b64encode(_json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_JWT_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:32]
    token = f"{payload_b64}.{sig}"
    return token, exp.isoformat()


def _verify_token(token: str) -> bool:
    """토큰 검증: 서명 + 만료 + 블랙리스트."""
    if token in _blacklisted_tokens:
        return False
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return False
        payload_b64, sig = parts
        expected_sig = hmac.new(_JWT_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected_sig):
            return False
        # 패딩 복원
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(padded))
        exp = datetime.fromisoformat(payload["exp"])
        if datetime.utcnow() > exp:
            return False
        return True
    except Exception:
        return False


# ── 의존성: 인증 확인 ──
async def require_admin(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """관리자 인증 확인 Depends."""
    if credentials is None or not _verify_token(credentials.credentials):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    return True


# ── 엔드포인트 ──

@router.post("/login", response_model=AdminToken)
async def admin_login(body: AdminLogin, request: Request):
    """관리자 로그인 → JWT 토큰 발급."""
    client_ip = request.client.host if request.client else "unknown"

    # 기본 비밀번호 차단
    if settings.admin.password == _ADMIN_PW_DEFAULT:
        raise HTTPException(status_code=403, detail="ADMIN_PASSWORD 환경변수를 설정하세요.")

    # 잠금 확인
    lockout = _lockout_until.get(client_ip, 0)
    if time.time() < lockout:
        remaining = int(lockout - time.time()) // 60 + 1
        raise HTTPException(status_code=429, detail=f"잠금 상태입니다. {remaining}분 후 재시도하세요.")

    # 비밀번호 검증
    a = hashlib.sha256(body.password.encode("utf-8")).digest()
    b = hashlib.sha256(settings.admin.password.encode("utf-8")).digest()
    if hmac.compare_digest(a, b):
        # 성공
        _failed_attempts.pop(client_ip, None)
        _lockout_until.pop(client_ip, None)
        _audit("LOGIN_SUCCESS", f"ip={client_ip}")
        token, expires_at = _create_token()
        return AdminToken(token=token, expires_at=expires_at)
    else:
        # 실패
        failed = _failed_attempts.get(client_ip, 0) + 1
        _failed_attempts[client_ip] = failed
        _audit("LOGIN_FAILED", f"ip={client_ip} attempts={failed}")
        if failed >= _MAX_ATTEMPTS:
            _lockout_until[client_ip] = time.time() + _LOCKOUT_SECS
            _failed_attempts[client_ip] = 0
            _audit("ACCOUNT_LOCKED", f"ip={client_ip} lockout={settings.admin.lockout_minutes}분")
            raise HTTPException(status_code=429, detail=f"시도 횟수 초과. {settings.admin.lockout_minutes}분 잠금.")
        raise HTTPException(status_code=401, detail=f"비밀번호 오류. 남은 시도: {_MAX_ATTEMPTS - failed}회")


@router.post("/logout")
async def admin_logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """관리자 로그아웃 → 토큰 무효화."""
    if credentials and credentials.credentials:
        _blacklisted_tokens.add(credentials.credentials)
        _audit("LOGOUT")
    return {"ok": True}
