"""
콘텐츠 블랙리스트 - 삭제된 콘텐츠의 재학습을 차단합니다.

저장소: data/crawl_meta/blacklist.json
관리자가 admin 페이지에서 추가/해제할 수 있습니다.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

CRAWL_META_DIR = DATA_DIR / "crawl_meta"
BLACKLIST_FILE = CRAWL_META_DIR / "blacklist.json"


class ContentBlacklist:
    """
    [역할] 차단된 URL/파일 경로를 관리하여 재수집/재인제스트를 방지
    [저장] data/crawl_meta/blacklist.json
    [관리] admin 페이지 "차단 관리" 탭에서 추가/해제
    """

    def __init__(self) -> None:
        CRAWL_META_DIR.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, dict] = self._load()

    # ── 공개 API ─────────────────────────────────────────────────

    def is_blocked(self, source_id: str) -> bool:
        """해당 source_id가 차단 목록에 있는지 확인합니다."""
        return source_id in self._entries

    def block(self, source_id: str, reason: str = "", blocked_by: str = "admin") -> None:
        """
        source_id를 차단 목록에 추가합니다.
        이미 차단된 항목이면 덮어씁니다 (reason 갱신).
        """
        self._entries[source_id] = {
            "blocked_at": datetime.now().isoformat(timespec="seconds"),
            "reason": reason,
            "blocked_by": blocked_by,
        }
        self._save()
        logger.info("차단 추가: %s (사유: %s)", source_id, reason)

    def unblock(self, source_id: str) -> bool:
        """차단을 해제합니다. 성공 여부 반환."""
        if source_id in self._entries:
            del self._entries[source_id]
            self._save()
            logger.info("차단 해제: %s", source_id)
            return True
        return False

    def list_blocked(self) -> list[dict]:
        """차단 목록을 차단일 내림차순으로 반환합니다."""
        result = []
        for source_id, meta in self._entries.items():
            result.append({
                "source_id": source_id,
                "blocked_at": meta.get("blocked_at", ""),
                "reason": meta.get("reason", ""),
                "blocked_by": meta.get("blocked_by", "admin"),
            })
        return sorted(result, key=lambda x: x["blocked_at"], reverse=True)

    def count(self) -> int:
        """차단된 항목 수를 반환합니다."""
        return len(self._entries)

    # ── 내부 유틸 ─────────────────────────────────────────────────

    def _load(self) -> dict[str, dict]:
        if BLACKLIST_FILE.exists():
            try:
                return json.loads(BLACKLIST_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("블랙리스트 파일 로드 실패, 빈 상태로 시작: %s", e)
        return {}

    def _save(self) -> None:
        try:
            BLACKLIST_FILE.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("블랙리스트 파일 저장 실패: %s", e)
