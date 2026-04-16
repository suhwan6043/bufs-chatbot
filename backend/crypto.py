"""
학업성적사정표 등 PII 포함 DB 컬럼 암호화 헬퍼.

- `cryptography.fernet` (AES-128-CBC + HMAC-SHA256) 사용.
- 키는 환경변수 `TRANSCRIPT_ENC_KEY` 또는 파일 `data/.transcript_enc.key` 에서 로드.
- 키 없으면 1회 자동 생성 후 파일에 저장 (dev 편의). 프로덕션은 env에 명시 주입 권장.
- 저장 포맷: `enc:v1:<urlsafe-base64>` — 접두어로 평문/암호문 구분 → 기존 평문 row 무중단 마이그레이션.

4원칙 #4 (하드코딩 금지): 키·접두어·파일 경로 모두 env로 override 가능.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_ENC_PREFIX = os.getenv("TRANSCRIPT_ENC_PREFIX", "enc:v1:")
_KEY_FILE = Path(os.getenv(
    "TRANSCRIPT_ENC_KEY_FILE",
    str(Path(__file__).resolve().parent.parent / "data" / ".transcript_enc.key"),
))

_cipher: Optional[Fernet] = None


def _load_or_create_key() -> bytes:
    """우선순위: env > 파일 > 자동 생성·저장."""
    env_key = os.getenv("TRANSCRIPT_ENC_KEY", "").strip()
    if env_key:
        return env_key.encode("ascii")

    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()

    # 자동 생성 — dev 환경 편의. 운영에선 env로 명시 주입하는 것이 권장.
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    try:
        # POSIX 시스템에서만 0600 적용 (Windows에선 무시)
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass
    logger.warning(
        "TRANSCRIPT_ENC_KEY 환경변수가 없어 %s 에 새 키를 생성했습니다. "
        "운영에서는 이 파일을 안전하게 백업하거나 env로 주입하세요.",
        _KEY_FILE,
    )
    return key


def _get_cipher() -> Fernet:
    global _cipher
    if _cipher is None:
        _cipher = Fernet(_load_or_create_key())
    return _cipher


def encrypt_text(plaintext: str) -> str:
    """평문 문자열을 Fernet으로 암호화하고 `enc:v1:` 접두어를 붙여 반환.

    빈 문자열/None은 그대로 반환 (저장 비용 절약 + 기존 비어있는 컬럼 호환).
    """
    if not plaintext:
        return plaintext
    token = _get_cipher().encrypt(plaintext.encode("utf-8"))
    return _ENC_PREFIX + token.decode("ascii")


def decrypt_text(value: str) -> str:
    """접두어가 있으면 복호화, 없으면 원문 그대로 반환 (기존 평문 row 호환)."""
    if not value:
        return value
    if not value.startswith(_ENC_PREFIX):
        return value  # legacy 평문 row
    token = value[len(_ENC_PREFIX):].encode("ascii")
    try:
        return _get_cipher().decrypt(token).decode("utf-8")
    except InvalidToken:
        logger.error("transcript 복호화 실패 — 키 불일치 또는 손상된 데이터")
        return ""  # 오류 시 빈 값 반환 (노출 방지)
