"""
변경 감지기 - 크롤링 콘텐츠의 신규/수정/삭제를 SHA-256 해시로 감지합니다.

저장소: data/crawl_meta/content_hashes.json
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

CRAWL_META_DIR = DATA_DIR / "crawl_meta"
HASH_FILE = CRAWL_META_DIR / "content_hashes.json"


class ChangeType(str, Enum):
    NEW = "new"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class CrawledItem:
    """크롤러가 수집한 단일 콘텐츠 항목"""
    source_id: str          # 고유 식별자 (URL/파일 경로/faq://FAQ-xxxx)
    title: str
    content: str            # HTML 제거된 순수 텍스트 (FAQ의 경우 Q+A 결합)
    content_type: str       # "notice", "news", "event", "guide", "timetable", "faq"
    content_hash: str       # SHA-256 of content
    crawled_at: datetime
    source_name: str        # "학사공지", "일반공지", "FAQ" 등
    attachments: list[str] = field(default_factory=list)  # 첨부파일 URL
    metadata: dict = field(default_factory=dict)
    is_pinned: bool = False  # 고정공지 여부


@dataclass
class ChangeEvent:
    """변경 감지 결과 이벤트"""
    source_id: str
    change_type: ChangeType
    old_hash: Optional[str]
    new_hash: Optional[str]
    title: str
    content: str            # DELETED일 때는 ""
    attachments: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class ChangeDetector:
    """
    [역할] 크롤링 콘텐츠의 변경 여부를 SHA-256 해시로 감지
    [저장] data/crawl_meta/content_hashes.json
    [감지] NEW / MODIFIED / DELETED 세 가지 이벤트 타입
    """

    def __init__(self) -> None:
        CRAWL_META_DIR.mkdir(parents=True, exist_ok=True)
        self._hashes: dict[str, dict] = self._load()

    # ── 공개 API ─────────────────────────────────────────────────

    def detect(self, items: list[CrawledItem]) -> list[ChangeEvent]:
        """
        현재 크롤링 결과와 저장된 해시를 비교하여 변경 이벤트 목록을 반환합니다.

        - 이전에 없었던 항목  → ChangeType.NEW
        - 해시가 달라진 항목  → ChangeType.MODIFIED
        - 이전엔 있었으나 없어진 항목 → ChangeType.DELETED
        """
        events: list[ChangeEvent] = []
        current_ids = {item.source_id for item in items}

        # NEW / MODIFIED 감지
        for item in items:
            old_entry = self._hashes.get(item.source_id)
            if old_entry is None:
                events.append(ChangeEvent(
                    source_id=item.source_id,
                    change_type=ChangeType.NEW,
                    old_hash=None,
                    new_hash=item.content_hash,
                    title=item.title,
                    content=item.content,
                    attachments=item.attachments,
                    metadata=item.metadata,
                ))
            elif old_entry["content_hash"] != item.content_hash:
                events.append(ChangeEvent(
                    source_id=item.source_id,
                    change_type=ChangeType.MODIFIED,
                    old_hash=old_entry["content_hash"],
                    new_hash=item.content_hash,
                    title=item.title,
                    content=item.content,
                    attachments=item.attachments,
                    metadata=item.metadata,
                ))
            # 해시 동일 → 이벤트 없음 (변경 없음)

        # DELETED 감지: 이전에 추적하던 항목이 현재 크롤링 결과에 없을 때
        for source_id, entry in self._hashes.items():
            if source_id not in current_ids:
                events.append(ChangeEvent(
                    source_id=source_id,
                    change_type=ChangeType.DELETED,
                    old_hash=entry["content_hash"],
                    new_hash=None,
                    title=entry.get("title", ""),
                    content="",
                    attachments=[],
                    metadata=entry.get("metadata", {}),
                ))

        logger.info(
            "변경 감지: NEW=%d, MODIFIED=%d, DELETED=%d",
            sum(1 for e in events if e.change_type == ChangeType.NEW),
            sum(1 for e in events if e.change_type == ChangeType.MODIFIED),
            sum(1 for e in events if e.change_type == ChangeType.DELETED),
        )
        return events

    def commit(self, events: list[ChangeEvent]) -> None:
        """
        감지된 변경 이벤트를 해시 저장소에 반영합니다.
        (IncrementalUpdater가 처리를 완료한 뒤 호출해야 합니다.)
        """
        now_iso = datetime.now().isoformat(timespec="seconds")
        for event in events:
            if event.change_type in (ChangeType.NEW, ChangeType.MODIFIED):
                existing = self._hashes.get(event.source_id, {})
                self._hashes[event.source_id] = {
                    "content_hash": event.new_hash,
                    "title": event.title,
                    "last_seen": now_iso,
                    "first_seen": existing.get("first_seen", now_iso),
                    "metadata": event.metadata,
                }
            elif event.change_type == ChangeType.DELETED:
                self._hashes.pop(event.source_id, None)
        self._save()

    def get_all_tracked(self) -> dict[str, dict]:
        """현재 추적 중인 모든 항목의 메타데이터를 반환합니다."""
        return dict(self._hashes)

    def remove_tracking(self, source_id: str) -> bool:
        """특정 항목의 추적을 중단합니다. 성공 여부 반환."""
        if source_id in self._hashes:
            del self._hashes[source_id]
            self._save()
            return True
        return False

    def is_tracked(self, source_id: str) -> bool:
        """해당 source_id가 현재 추적 중인지 반환합니다."""
        return source_id in self._hashes

    # ── 내부 유틸 ─────────────────────────────────────────────────

    def _load(self) -> dict[str, dict]:
        if HASH_FILE.exists():
            try:
                return json.loads(HASH_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("해시 파일 로드 실패, 빈 상태로 시작: %s", e)
        return {}

    def _save(self) -> None:
        try:
            HASH_FILE.write_text(
                json.dumps(self._hashes, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("해시 파일 저장 실패: %s", e)
