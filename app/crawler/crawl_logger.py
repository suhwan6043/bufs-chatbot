"""
크롤링 이력 로거 - 크롤링 작업 실행 결과를 JSONL 파일로 기록합니다.

저장소: data/crawl_meta/crawl_history.jsonl
기존 ChatLogger(data/logs/)와 완전히 분리된 별도 저장소입니다.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

CRAWL_META_DIR = DATA_DIR / "crawl_meta"
HISTORY_FILE = CRAWL_META_DIR / "crawl_history.jsonl"


@dataclass
class UpdateReport:
    """증분 업데이트 처리 결과 보고서"""
    added: int = 0        # 신규 청크 수
    updated: int = 0      # 수정(삭제 후 재추가)된 청크 수
    deleted: int = 0      # 삭제된 청크 수
    skipped: int = 0      # 블랙리스트로 건너뛴 이벤트 수
    errors: list[str] = field(default_factory=list)
    failed_source_ids: set = field(default_factory=set)  # 처리 실패한 source_id 집합

    def has_changes(self) -> bool:
        return (self.added + self.updated + self.deleted) > 0

    def summary(self) -> str:
        return (
            f"추가={self.added}, 수정={self.updated}, "
            f"삭제={self.deleted}, 건너뜀={self.skipped}"
        )


class CrawlLogger:
    """
    [역할] 크롤링 작업 실행 결과를 JSONL 파일로 저장/조회
    [저장] data/crawl_meta/crawl_history.jsonl
    [분리] ChatLogger(Q&A 로그)와 완전히 별개 — 공유 없음
    """

    def __init__(self) -> None:
        CRAWL_META_DIR.mkdir(parents=True, exist_ok=True)

    # ── 저장 ──────────────────────────────────────────────────────

    def log_run(
        self,
        job_id: str,
        report: UpdateReport,
        duration_ms: int = 0,
        detail: str = "",
    ) -> None:
        """크롤링 작업 실행 결과를 JSONL 파일에 추가합니다."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "job_id": job_id,
            "duration_ms": duration_ms,
            "added": report.added,
            "updated": report.updated,
            "deleted": report.deleted,
            "skipped": report.skipped,
            "errors": report.errors,
            "detail": detail,
        }
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("크롤링 이력 저장 실패: %s", e)

    # ── 조회 ──────────────────────────────────────────────────────

    def read_recent(self, limit: int = 50) -> list[dict]:
        """최근 N개의 크롤링 이력을 최신순으로 반환합니다."""
        all_entries = self._load_all()
        return list(reversed(all_entries[-limit:])) if all_entries else []

    def read_by_job(self, job_id: str, limit: int = 20) -> list[dict]:
        """특정 작업 ID의 이력을 최신순으로 반환합니다."""
        all_entries = self._load_all()
        filtered = [e for e in all_entries if e.get("job_id") == job_id]
        return list(reversed(filtered[-limit:]))

    def get_last_run(self, job_id: str) -> dict | None:
        """특정 작업의 마지막 실행 결과를 반환합니다."""
        runs = self.read_by_job(job_id, limit=1)
        return runs[0] if runs else None

    # ── 내부 유틸 ─────────────────────────────────────────────────

    def _load_all(self) -> list[dict]:
        if not HISTORY_FILE.exists():
            return []
        entries = []
        try:
            for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError as e:
            logger.warning("크롤링 이력 로드 실패: %s", e)
        return entries
