"""
SQLite user database — CAMCHAT user authentication.

Security:
- PBKDF2-SHA256 (600,000 iterations, OWASP 2024)
- Per-user 32-byte random salt
- Timing-safe comparison (hmac.compare_digest)
- No plaintext passwords ever stored or logged
"""

import sqlite3
import hashlib
import hmac
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "users.db"
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 32
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """Thread-local SQLite connection with WAL mode."""
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create users table if not exists. Called once at startup."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                nickname TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                student_id TEXT NOT NULL,
                department TEXT NOT NULL,
                student_type TEXT NOT NULL DEFAULT '내국인',
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        """)
        conn.commit()
        conn.close()


def _hash_password(password: str, salt: bytes) -> str:
    """PBKDF2-SHA256 hash with per-user salt."""
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return dk.hex()


def _verify_password(password: str, stored_hash: str, salt_hex: str) -> bool:
    """Timing-safe password verification."""
    salt = bytes.fromhex(salt_hex)
    candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


def create_user(
    username: str,
    nickname: str,
    password: str,
    student_id: str,
    department: str,
    student_type: str = "내국인",
) -> Optional[dict]:
    """
    Register a new user. Returns user dict or None if username taken.
    Password is NEVER stored in plaintext.
    """
    salt = os.urandom(_SALT_BYTES)
    pw_hash = _hash_password(password, salt)
    now = datetime.utcnow().isoformat()

    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO users (username, nickname, password_hash, salt,
                   student_id, department, student_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (username, nickname, pw_hash, salt.hex(),
                 student_id, department, student_type, now),
            )
            conn.commit()
            user_id = conn.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            conn.close()
            return {
                "id": user_id,
                "username": username,
                "nickname": nickname,
                "student_id": student_id,
                "department": department,
                "student_type": student_type,
            }
        except sqlite3.IntegrityError:
            conn.close()
            return None  # username duplicate


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """
    Verify credentials. Returns user dict or None.
    Uses timing-safe comparison to prevent timing attacks.
    """
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

    if row is None:
        # Perform dummy hash to prevent timing leak on missing user
        _hash_password(password, os.urandom(_SALT_BYTES))
        return None

    if not _verify_password(password, row["password_hash"], row["salt"]):
        return None

    # Update last_login
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), row["id"]),
        )
        conn.commit()
        conn.close()

    return {
        "id": row["id"],
        "username": row["username"],
        "nickname": row["nickname"],
        "student_id": row["student_id"],
        "department": row["department"],
        "student_type": row["student_type"],
    }


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Fetch user by ID (for JWT validation)."""
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, username, nickname, student_id, department, student_type FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        conn.close()

    if row is None:
        return None
    return dict(row)
