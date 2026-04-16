"""
서버 사이드 세션 스토어 — st.session_state 대체.

인메모리 딕셔너리 기반, TTL 24시간, 5분마다 만료 정리.
향후 Redis로 교체 가능하도록 인터페이스 분리.
"""

import threading
import time
import uuid
from typing import Any, Optional


_DEFAULT_TTL = 86_400  # 24시간
_CLEANUP_INTERVAL = 300  # 5분


class SessionStore:
    """Thread-safe 인메모리 세션 스토어."""

    def __init__(self, ttl: int = _DEFAULT_TTL):
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        # 백그라운드 정리 스레드
        self._cleaner = threading.Thread(
            target=self._cleanup_loop, daemon=True, name="session-cleaner"
        )
        self._cleaner.start()

    # ── public API ──

    def create(self, lang: str = "ko") -> str:
        """새 세션 생성 → session_id 반환."""
        sid = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            self._store[sid] = {
                "data": {
                    "lang": lang,
                    "messages": [],
                    "user_profile": None,
                },
                "created_at": now,
                "last_active": now,
            }
        return sid

    def get(self, sid: str) -> Optional[dict]:
        """세션 데이터 반환. 없거나 만료 시 None."""
        with self._lock:
            entry = self._store.get(sid)
            if entry is None:
                return None
            if time.time() - entry["last_active"] > self._ttl:
                del self._store[sid]
                return None
            entry["last_active"] = time.time()
            return entry["data"]

    def get_or_create(self, sid: Optional[str], lang: str = "ko") -> tuple[str, dict]:
        """세션이 있으면 반환, 없으면 생성.
        클라이언트가 sid를 제공한 경우 해당 sid를 재사용해 생성 (backend 재시작 후 세션 복원용).
        """
        if sid:
            data = self.get(sid)
            if data is not None:
                return sid, data
            # 클라이언트 sid로 새 엔트리 생성 (backend 재시작 후 clean-state 복원)
            now = time.time()
            with self._lock:
                self._store[sid] = {
                    "data": {"lang": lang, "messages": [], "user_profile": None},
                    "created_at": now,
                    "last_active": now,
                }
            return sid, self._store[sid]["data"]
        new_sid = self.create(lang)
        return new_sid, self.get(new_sid)

    def update(self, sid: str, key: str, value: Any) -> bool:
        """세션 데이터 필드 업데이트."""
        with self._lock:
            entry = self._store.get(sid)
            if entry is None:
                return False
            entry["data"][key] = value
            entry["last_active"] = time.time()
            return True

    def delete(self, sid: str) -> bool:
        """세션 삭제."""
        with self._lock:
            return self._store.pop(sid, None) is not None

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._store)

    # ── internal ──

    def _cleanup_loop(self):
        while True:
            time.sleep(_CLEANUP_INTERVAL)
            self._cleanup()

    def _cleanup(self):
        now = time.time()
        with self._lock:
            expired = [
                sid
                for sid, entry in self._store.items()
                if now - entry["last_active"] > self._ttl
            ]
            for sid in expired:
                del self._store[sid]


# 모듈 레벨 싱글톤
session_store = SessionStore()
