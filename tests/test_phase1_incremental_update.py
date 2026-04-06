"""
Phase 1 테스트 — IncrementalUpdater
ChromaStore를 mock하여 실제 DB 없이 테스트합니다.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest

from app.crawler.blacklist import ContentBlacklist
from app.crawler.change_detector import ChangeEvent, ChangeType
from app.ingestion.incremental_update import IncrementalUpdater
from app.crawler.crawl_logger import UpdateReport


def _make_event(
    source_id: str,
    change_type: ChangeType,
    content: str = "테스트 내용입니다. " * 10,
    title: str = "테스트 제목",
    is_pinned: bool = True,  # 기본값 True → 기존 테스트가 고정공지(벡터 경로)를 탐
) -> ChangeEvent:
    return ChangeEvent(
        source_id=source_id,
        change_type=change_type,
        old_hash="oldhash" if change_type != ChangeType.NEW else None,
        new_hash="newhash" if change_type != ChangeType.DELETED else None,
        title=title,
        content=content,
        attachments=[],
        metadata={
            "content_type": "notice",
            "source_name": "학사공지",
            "is_pinned": is_pinned,
        },
    )


@pytest.fixture
def mock_chroma():
    store = MagicMock()
    store.delete_by_source.return_value = 2
    store.add_chunks.return_value = None
    return store


@pytest.fixture
def real_blacklist(tmp_path):
    with patch("app.crawler.blacklist.CRAWL_META_DIR", tmp_path), \
         patch("app.crawler.blacklist.BLACKLIST_FILE", tmp_path / "blacklist.json"):
        yield ContentBlacklist()


@pytest.fixture
def updater(mock_chroma, real_blacklist):
    return IncrementalUpdater(
        chroma_store=mock_chroma,
        blacklist=real_blacklist,
    )


class TestIncrementalUpdaterNew:
    def test_new_event_calls_add_chunks(self, updater, mock_chroma):
        event = _make_event("http://example.com/1", ChangeType.NEW)
        report = updater.process_events([event])

        mock_chroma.add_chunks.assert_called_once()
        assert report.added > 0
        assert report.updated == 0
        assert report.deleted == 0

    def test_new_chunk_source_file_is_url(self, updater, mock_chroma):
        event = _make_event("http://example.com/notice/1", ChangeType.NEW)
        updater.process_events([event])

        chunks = mock_chroma.add_chunks.call_args[0][0]
        assert all(c.source_file == "http://example.com/notice/1" for c in chunks)

    def test_new_chunk_doc_type_from_metadata(self, updater, mock_chroma):
        event = _make_event("http://example.com/1", ChangeType.NEW)
        updater.process_events([event])

        chunks = mock_chroma.add_chunks.call_args[0][0]
        assert all(c.doc_type == "notice" for c in chunks)


class TestIncrementalUpdaterModified:
    def test_modified_event_deletes_then_adds(self, updater, mock_chroma):
        event = _make_event("http://example.com/1", ChangeType.MODIFIED)
        report = updater.process_events([event])

        mock_chroma.delete_by_source.assert_called_once_with("http://example.com/1")
        mock_chroma.add_chunks.assert_called_once()
        assert report.updated > 0

    def test_modified_preserves_source_id(self, updater, mock_chroma):
        event = _make_event("http://example.com/1", ChangeType.MODIFIED)
        updater.process_events([event])

        chunks = mock_chroma.add_chunks.call_args[0][0]
        assert all(c.source_file == "http://example.com/1" for c in chunks)


class TestIncrementalUpdaterDeleted:
    def test_deleted_event_calls_delete(self, updater, mock_chroma):
        event = _make_event("http://example.com/1", ChangeType.DELETED, content="")
        report = updater.process_events([event])

        mock_chroma.delete_by_source.assert_called_once_with("http://example.com/1")
        mock_chroma.add_chunks.assert_not_called()
        assert report.deleted == 1

    def test_deleted_does_not_add_chunks(self, updater, mock_chroma):
        event = _make_event("http://example.com/1", ChangeType.DELETED, content="")
        updater.process_events([event])
        mock_chroma.add_chunks.assert_not_called()


class TestIncrementalUpdaterBlacklist:
    def test_blacklisted_source_skipped(self, updater, mock_chroma, real_blacklist):
        real_blacklist.block("http://example.com/1", reason="테스트")
        event = _make_event("http://example.com/1", ChangeType.NEW)
        report = updater.process_events([event])

        mock_chroma.add_chunks.assert_not_called()
        assert report.skipped == 1
        assert report.added == 0

    def test_non_blacklisted_processed_normally(self, updater, mock_chroma, real_blacklist):
        real_blacklist.block("http://example.com/blocked", reason="차단")
        events = [
            _make_event("http://example.com/blocked", ChangeType.NEW),
            _make_event("http://example.com/normal", ChangeType.NEW),
        ]
        report = updater.process_events(events)

        assert report.skipped == 1
        assert report.added > 0


class TestIncrementalUpdaterReport:
    def test_mixed_events_report(self, updater, mock_chroma):
        events = [
            _make_event("http://example.com/1", ChangeType.NEW),
            _make_event("http://example.com/2", ChangeType.MODIFIED),
            _make_event("http://example.com/3", ChangeType.DELETED, content=""),
        ]
        report = updater.process_events(events)

        assert report.added > 0
        assert report.updated > 0
        assert report.deleted == 1
        assert report.errors == []

    def test_error_handling_does_not_stop_other_events(self, updater, mock_chroma):
        mock_chroma.add_chunks.side_effect = [Exception("DB 오류"), None]
        events = [
            _make_event("http://example.com/1", ChangeType.NEW),
            _make_event("http://example.com/2", ChangeType.NEW),
        ]
        report = updater.process_events(events)

        assert len(report.errors) == 1
        assert report.added > 0  # 두 번째는 성공


class TestIncrementalUpdaterChunking:
    def test_empty_content_produces_no_chunks(self, updater, mock_chroma):
        event = _make_event("http://example.com/1", ChangeType.NEW, content="")
        report = updater.process_events([event])

        mock_chroma.add_chunks.assert_not_called()
        assert report.added == 0

    def test_cohort_detected_in_chunks(self, updater, mock_chroma):
        content = "2024학번 이후 학생은 다음 사항을 준수해야 합니다. " * 5
        event = _make_event("http://example.com/1", ChangeType.NEW, content=content)
        updater.process_events([event])

        chunks = mock_chroma.add_chunks.call_args[0][0]
        assert any(c.cohort_from == 2024 for c in chunks)


# ══════════════════════════════════════════════════════
# 비고정 공지 → 그래프만 업데이트, 벡터 스킵 (공지 경량화)
# ══════════════════════════════════════════════════════

class TestNonPinnedNoticeGraphOnly:
    """비고정 공지는 ChromaDB에 임베딩하지 않고 그래프만 업데이트한다.

    원칙 2(비용·지연): 벡터 검색 대상 축소로 노이즈 제거 + 인덱스 경량화.
    """

    def test_non_pinned_new_skips_chroma(self, updater, mock_chroma):
        event = _make_event("http://example.com/regular", ChangeType.NEW, is_pinned=False)
        report = updater.process_events([event])

        mock_chroma.add_chunks.assert_not_called()
        assert report.added == 0

    def test_non_pinned_modified_cleans_legacy_chunks(self, updater, mock_chroma):
        """이전에 벡터에 들어간 레거시 청크가 있으면 삭제만 한다."""
        event = _make_event("http://example.com/regular", ChangeType.MODIFIED, is_pinned=False)
        report = updater.process_events([event])

        mock_chroma.delete_by_source.assert_called()
        mock_chroma.add_chunks.assert_not_called()
        assert report.updated == 0

    def test_pinned_notice_still_ingested(self, updater, mock_chroma):
        """고정공지는 기존대로 벡터 DB에 인제스트된다."""
        event = _make_event("http://example.com/pinned", ChangeType.NEW, is_pinned=True)
        report = updater.process_events([event])

        mock_chroma.add_chunks.assert_called_once()
        assert report.added > 0
