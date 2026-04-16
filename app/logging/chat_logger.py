"""
캠챗 대화 로그 저장/조회 모듈

질문·답변을 날짜별 JSONL 파일로 저장합니다.
  data/logs/chat_YYYY-MM-DD.jsonl
"""

import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

LOG_DIR = DATA_DIR / "logs"

# 평가·테스트 러너가 설정하면 모든 Q&A 로그 기록이 건너뜀 (회귀 스크립트가 production 로그 오염 방지).
# production/사용자 트래픽에는 절대 세팅하지 말 것.
CHAT_LOG_DISABLED = os.getenv("CHAT_LOG_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}

# 요청 단위 스킵 플래그 — 라우터 진입부에서 `X-Test-Mode` 헤더를 보면 True로 세팅.
# JSONL 로그 + chat_messages DB 양쪽 모두 건너뛴다.
_skip_log_var: ContextVar[bool] = ContextVar("camchat_skip_log", default=False)


def set_skip_log(flag: bool) -> None:
    """현재 요청에서 Q&A 로그 기록을 건너뛰도록 설정. ContextVar 기반이라 요청 간 격리됨."""
    _skip_log_var.set(bool(flag))


def should_skip_log() -> bool:
    return CHAT_LOG_DISABLED or _skip_log_var.get()


class ChatLogger:
    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ── 저장 ──────────────────────────────────────────
    def _today_path(self) -> Path:
        return self.log_dir / f"chat_{date.today().isoformat()}.jsonl"

    def log(
        self,
        question: str,
        answer: str,
        session_id: str = "",
        intent: str = "",
        student_id: Optional[str] = None,
        duration_ms: int = 0,
        rating: Optional[int] = None,
        context_confidence: Optional[float] = None,
        user_id: Optional[int] = None,
        chat_message_id: Optional[int] = None,
    ) -> None:
        """Q&A 한 쌍을 오늘 날짜 JSONL 파일에 추가합니다.

        user_id: 로그인 사용자 id (비로그인=None). FAQ 이송 시 원본 질문자 식별에 사용.
        chat_message_id: backend/database.py chat_messages 테이블 row id (개인 DB 저장 완료 시).

        CHAT_LOG_DISABLED 환경변수 또는 요청의 X-Test-Mode 헤더가 참이면 조용히 건너뜀 —
        평가·테스트 러너가 production 로그 오염을 방지하는 데 사용.
        """
        if should_skip_log():
            return
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "student_id": student_id or "",
            "intent": intent,
            "question": question,
            "answer": answer,
            "duration_ms": duration_ms,
            "rating": rating,
        }
        if context_confidence is not None:
            entry["context_confidence"] = round(float(context_confidence), 4)
        if user_id is not None:
            entry["user_id"] = int(user_id)
        if chat_message_id is not None:
            entry["chat_message_id"] = int(chat_message_id)
        try:
            with open(self._today_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"로그 저장 실패: {e}")

    # ── 조회 ──────────────────────────────────────────
    @staticmethod
    def _parse_file(path: Path) -> list[dict]:
        entries = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
        return entries

    def read(self, d: date | None = None) -> list[dict]:
        """특정 날짜(기본: 오늘) 로그를 반환합니다."""
        path = self.log_dir / f"chat_{(d or date.today()).isoformat()}.jsonl"
        return self._parse_file(path)

    def read_all(self) -> list[dict]:
        """모든 날짜 로그를 시간순으로 반환합니다."""
        all_entries: list[dict] = []
        for path in sorted(self.log_dir.glob("chat_*.jsonl")):
            all_entries.extend(self._parse_file(path))
        return all_entries

    def update_rating(self, session_id: str, question: str, rating: int) -> bool:
        """특정 Q&A 항목의 별점을 업데이트합니다. 성공 여부를 반환합니다."""
        path = self._today_path()
        if not path.exists():
            return False
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            updated = False
            new_lines = []
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    new_lines.insert(0, line)
                    continue
                try:
                    entry = json.loads(line)
                    if (
                        not updated
                        and entry.get("session_id") == session_id
                        and entry.get("question") == question
                    ):
                        entry["rating"] = rating
                        updated = True
                    new_lines.insert(0, json.dumps(entry, ensure_ascii=False))
                except json.JSONDecodeError:
                    new_lines.insert(0, line)
            if updated:
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            return updated
        except Exception as e:
            logger.error(f"별점 업데이트 실패: {e}")
            return False

    def list_dates(self) -> list[date]:
        """로그가 존재하는 날짜 목록을 최신 순으로 반환합니다."""
        dates: list[date] = []
        for path in sorted(self.log_dir.glob("chat_*.jsonl"), reverse=True):
            try:
                # "chat_YYYY-MM-DD" → "YYYY-MM-DD"
                dates.append(date.fromisoformat(path.stem[5:]))
            except ValueError:
                pass
        return dates
