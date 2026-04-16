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

from backend.crypto import encrypt_text, decrypt_text

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
    """Create tables if not exist. Called once at startup."""
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                intent TEXT NOT NULL DEFAULT '',
                rating INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON chat_messages(user_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS faq_subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faq_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                chat_message_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (chat_message_id) REFERENCES chat_messages(id) ON DELETE SET NULL,
                UNIQUE(faq_id, user_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_faq_subscribers_faq ON faq_subscribers(faq_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                faq_id TEXT,
                chat_message_id INTEGER,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                read_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (chat_message_id) REFERENCES chat_messages(id) ON DELETE SET NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_user "
            "ON notifications(user_id, read_at, created_at DESC)"
        )
        # 2026-04-16: 로그인 사용자 학업성적사정표 영구 저장.
        # 세션 메모리(SecureTranscriptStore, 30분 TTL)와 이중 저장 —
        # DB는 재로그인 후 자동 복원용, 원본 학번/성명은 저장하지 않음 (PII 마스킹 후 JSON).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                parsed_json TEXT NOT NULL,
                masked_name TEXT NOT NULL,
                gpa REAL NOT NULL DEFAULT 0,
                total_acquired REAL NOT NULL DEFAULT 0,
                total_required REAL NOT NULL DEFAULT 0,
                total_shortage REAL NOT NULL DEFAULT 0,
                uploaded_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_transcripts_user "
            "ON user_transcripts(user_id)"
        )
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


# ── Chat history (개인 질문 이력) ─────────────────────────────

def insert_chat_message(
    user_id: int,
    session_id: str,
    question: str,
    answer: str,
    intent: str = "",
) -> int:
    """로그인 사용자 질문·답변을 저장하고 row id를 반환."""
    now = datetime.utcnow().isoformat()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            """INSERT INTO chat_messages
               (user_id, session_id, question, answer, intent, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, session_id, question, answer, intent or "", now),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
    return int(new_id)


def list_chat_messages(user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    """본인 질문 이력을 최신순 반환."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, session_id, question, answer, intent, rating, created_at
               FROM chat_messages
               WHERE user_id = ?
               ORDER BY created_at DESC, id DESC
               LIMIT ? OFFSET ?""",
            (user_id, limit, offset),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def count_chat_messages(user_id: int) -> int:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.close()
    return int(row["c"]) if row else 0


def get_chat_message(message_id: int) -> Optional[dict]:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM chat_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        conn.close()
    return dict(row) if row else None


# ── FAQ 구독자 (이송 시 연결) ────────────────────────────────

def add_faq_subscriber(
    faq_id: str,
    user_id: int,
    chat_message_id: Optional[int] = None,
) -> bool:
    """FAQ와 사용자 연결. 이미 존재하면 False."""
    now = datetime.utcnow().isoformat()
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO faq_subscribers
                   (faq_id, user_id, chat_message_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (faq_id, user_id, chat_message_id, now),
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            conn.close()
            return False


def list_faq_subscribers(faq_id: str) -> list[dict]:
    """해당 FAQ에 연결된 구독자 목록 (user_id, chat_message_id)."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT user_id, chat_message_id FROM faq_subscribers
               WHERE faq_id = ?""",
            (faq_id,),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def delete_faq_subscribers(faq_id: str) -> int:
    """FAQ 삭제 시 구독자 정리. 삭제된 행 수 반환."""
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "DELETE FROM faq_subscribers WHERE faq_id = ?",
            (faq_id,),
        )
        conn.commit()
        deleted = cur.rowcount
        conn.close()
    return int(deleted)


# ── 알림 ────────────────────────────────────────────────────

def create_notification(
    user_id: int,
    kind: str,
    title: str,
    body: str = "",
    faq_id: Optional[str] = None,
    chat_message_id: Optional[int] = None,
) -> int:
    now = datetime.utcnow().isoformat()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            """INSERT INTO notifications
               (user_id, kind, faq_id, chat_message_id, title, body, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, kind, faq_id, chat_message_id, title, body or "", now),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
    return int(new_id)


def list_notifications(user_id: int, limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, kind, faq_id, chat_message_id, title, body, read_at, created_at
               FROM notifications
               WHERE user_id = ?
               ORDER BY (read_at IS NOT NULL), created_at DESC, id DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def count_unread_notifications(user_id: int) -> int:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND read_at IS NULL",
            (user_id,),
        ).fetchone()
        conn.close()
    return int(row["c"]) if row else 0


def mark_notification_read(notification_id: int, user_id: int) -> bool:
    """소유자 검증 후 읽음 처리. 본인 것이 아니면 False."""
    now = datetime.utcnow().isoformat()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            """UPDATE notifications
               SET read_at = ?
               WHERE id = ? AND user_id = ? AND read_at IS NULL""",
            (now, notification_id, user_id),
        )
        conn.commit()
        updated = cur.rowcount
        conn.close()
    return updated > 0


def mark_all_notifications_read(user_id: int) -> int:
    now = datetime.utcnow().isoformat()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            """UPDATE notifications
               SET read_at = ?
               WHERE user_id = ? AND read_at IS NULL""",
            (now, user_id),
        )
        conn.commit()
        updated = cur.rowcount
        conn.close()
    return int(updated)


# ── 학업성적사정표 (user_transcripts) ────────────────────────

def upsert_user_transcript(
    user_id: int,
    parsed_json: str,
    masked_name: str,
    gpa: float,
    total_acquired: float,
    total_required: float,
    total_shortage: float,
) -> int:
    """
    사용자당 1개 transcript 유지 (신규 업로드 시 덮어쓰기).
    parsed_json은 PII 마스킹 후 직렬화된 문자열이어야 한다.
    저장 시 Fernet(AES) 대칭암호화 적용 — DB 파일 유출 시에도 원문 노출 방지.
    masked_name 도 암호화 (마스킹 후지만 노출 최소화).
    Returns: 레코드 id
    """
    now = datetime.utcnow().isoformat()
    enc_json = encrypt_text(parsed_json)
    enc_masked = encrypt_text(masked_name)
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO user_transcripts
               (user_id, parsed_json, masked_name, gpa, total_acquired,
                total_required, total_shortage, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 parsed_json = excluded.parsed_json,
                 masked_name = excluded.masked_name,
                 gpa = excluded.gpa,
                 total_acquired = excluded.total_acquired,
                 total_required = excluded.total_required,
                 total_shortage = excluded.total_shortage,
                 uploaded_at = excluded.uploaded_at""",
            (user_id, enc_json, enc_masked, float(gpa),
             float(total_acquired), float(total_required),
             float(total_shortage), now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM user_transcripts WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.close()
    return int(row["id"]) if row else 0


def get_user_transcript(user_id: int) -> Optional[dict]:
    """로그인 사용자의 저장된 transcript 조회. 없으면 None.

    저장 시 암호화된 parsed_json/masked_name은 읽을 때 자동 복호화.
    레거시 평문 row도 decrypt_text의 접두어 체크로 호환.
    """
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            """SELECT id, user_id, parsed_json, masked_name, gpa,
                      total_acquired, total_required, total_shortage, uploaded_at
               FROM user_transcripts WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        conn.close()
    if row is None:
        return None
    d = dict(row)
    d["parsed_json"] = decrypt_text(d.get("parsed_json") or "")
    d["masked_name"] = decrypt_text(d.get("masked_name") or "")
    return d


def delete_user_transcript(user_id: int) -> bool:
    """사용자 transcript 영구 삭제. 삭제됐으면 True."""
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "DELETE FROM user_transcripts WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
        deleted = cur.rowcount
        conn.close()
    return deleted > 0
