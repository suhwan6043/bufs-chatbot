"""
Phase 1 테스트 — ChangeDetector
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.crawler.change_detector import (
    ChangeDetector,
    ChangeType,
    CrawledItem,
)


def _make_item(source_id: str, content: str, content_hash: str = None) -> CrawledItem:
    import hashlib
    h = content_hash or hashlib.sha256(content.encode()).hexdigest()
    return CrawledItem(
        source_id=source_id,
        title=f"제목_{source_id}",
        content=content,
        content_type="notice",
        content_hash=h,
        crawled_at=datetime.now(),
        source_name="테스트",
    )


@pytest.fixture
def detector(tmp_path):
    """임시 디렉토리를 사용하는 ChangeDetector 픽스처"""
    with patch("app.crawler.change_detector.CRAWL_META_DIR", tmp_path), \
         patch("app.crawler.change_detector.HASH_FILE", tmp_path / "hashes.json"):
        d = ChangeDetector()
        yield d


class TestChangeDetectorNew:
    def test_new_item_detected(self, detector):
        items = [_make_item("http://example.com/1", "내용1")]
        events = detector.detect(items)
        assert len(events) == 1
        assert events[0].change_type == ChangeType.NEW
        assert events[0].source_id == "http://example.com/1"

    def test_multiple_new_items(self, detector):
        items = [
            _make_item("http://example.com/1", "내용1"),
            _make_item("http://example.com/2", "내용2"),
        ]
        events = detector.detect(items)
        assert len(events) == 2
        assert all(e.change_type == ChangeType.NEW for e in events)

    def test_no_event_for_empty_items(self, detector):
        events = detector.detect([])
        assert events == []


class TestChangeDetectorModified:
    def test_modified_after_commit(self, detector):
        import hashlib
        item = _make_item("http://example.com/1", "원래내용")
        # 1차: NEW 커밋
        events = detector.detect([item])
        detector.commit(events)

        # 2차: 내용 변경 → MODIFIED
        new_content = "바뀐내용"
        new_hash = hashlib.sha256(new_content.encode()).hexdigest()
        modified_item = _make_item("http://example.com/1", new_content, new_hash)
        events2 = detector.detect([modified_item])

        assert len(events2) == 1
        assert events2[0].change_type == ChangeType.MODIFIED
        assert events2[0].old_hash == item.content_hash
        assert events2[0].new_hash == new_hash

    def test_unchanged_no_event(self, detector):
        item = _make_item("http://example.com/1", "내용")
        events = detector.detect([item])
        detector.commit(events)

        # 동일한 내용으로 다시 감지 → 이벤트 없음
        events2 = detector.detect([item])
        assert len(events2) == 0


class TestChangeDetectorDeleted:
    def test_deleted_when_not_in_current_items(self, detector):
        item = _make_item("http://example.com/1", "내용")
        events = detector.detect([item])
        detector.commit(events)

        # 빈 목록으로 다시 감지 → DELETED
        events2 = detector.detect([])
        assert len(events2) == 1
        assert events2[0].change_type == ChangeType.DELETED
        assert events2[0].source_id == "http://example.com/1"

    def test_deleted_only_missing_items(self, detector):
        items = [
            _make_item("http://example.com/1", "내용1"),
            _make_item("http://example.com/2", "내용2"),
        ]
        events = detector.detect(items)
        detector.commit(events)

        # 1번만 남기고 2번 제거
        events2 = detector.detect([items[0]])
        deleted = [e for e in events2 if e.change_type == ChangeType.DELETED]
        assert len(deleted) == 1
        assert deleted[0].source_id == "http://example.com/2"


class TestChangeDetectorCommit:
    def test_commit_persists_new(self, detector):
        item = _make_item("http://example.com/1", "내용")
        events = detector.detect([item])
        detector.commit(events)

        assert detector.is_tracked("http://example.com/1")
        tracked = detector.get_all_tracked()
        assert tracked["http://example.com/1"]["content_hash"] == item.content_hash

    def test_commit_removes_deleted(self, detector):
        item = _make_item("http://example.com/1", "내용")
        events = detector.detect([item])
        detector.commit(events)

        # 삭제 감지 후 커밋
        events2 = detector.detect([])
        detector.commit(events2)

        assert not detector.is_tracked("http://example.com/1")

    def test_remove_tracking(self, detector):
        item = _make_item("http://example.com/1", "내용")
        events = detector.detect([item])
        detector.commit(events)

        result = detector.remove_tracking("http://example.com/1")
        assert result is True
        assert not detector.is_tracked("http://example.com/1")
