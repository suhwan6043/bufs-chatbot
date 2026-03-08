"""
캠챗 대화 로그 저장/조회 모듈

질문·답변을 날짜별 JSONL 파일로 저장합니다.
  data/logs/chat_YYYY-MM-DD.jsonl
"""

import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

LOG_DIR = DATA_DIR / "logs"


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
    ) -> None:
        """Q&A 한 쌍을 오늘 날짜 JSONL 파일에 추가합니다."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "student_id": student_id or "",
            "intent": intent,
            "question": question,
            "answer": answer,
            "duration_ms": duration_ms,
        }
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
