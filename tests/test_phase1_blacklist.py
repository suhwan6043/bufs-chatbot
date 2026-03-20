"""
Phase 1 테스트 — ContentBlacklist
"""

from unittest.mock import patch

import pytest

from app.crawler.blacklist import ContentBlacklist


@pytest.fixture
def blacklist(tmp_path):
    with patch("app.crawler.blacklist.CRAWL_META_DIR", tmp_path), \
         patch("app.crawler.blacklist.BLACKLIST_FILE", tmp_path / "blacklist.json"):
        yield ContentBlacklist()


class TestContentBlacklist:
    def test_block_and_is_blocked(self, blacklist):
        assert not blacklist.is_blocked("http://example.com/1")
        blacklist.block("http://example.com/1", reason="테스트 차단")
        assert blacklist.is_blocked("http://example.com/1")

    def test_unblock(self, blacklist):
        blacklist.block("http://example.com/1", reason="차단")
        result = blacklist.unblock("http://example.com/1")
        assert result is True
        assert not blacklist.is_blocked("http://example.com/1")

    def test_unblock_nonexistent_returns_false(self, blacklist):
        result = blacklist.unblock("http://notexist.com")
        assert result is False

    def test_list_blocked(self, blacklist):
        blacklist.block("http://example.com/1", reason="사유1")
        blacklist.block("http://example.com/2", reason="사유2")
        listed = blacklist.list_blocked()
        assert len(listed) == 2
        source_ids = {e["source_id"] for e in listed}
        assert source_ids == {"http://example.com/1", "http://example.com/2"}

    def test_count(self, blacklist):
        assert blacklist.count() == 0
        blacklist.block("http://example.com/1")
        assert blacklist.count() == 1
        blacklist.block("http://example.com/2")
        assert blacklist.count() == 2
        blacklist.unblock("http://example.com/1")
        assert blacklist.count() == 1

    def test_block_overwrites_existing(self, blacklist):
        blacklist.block("http://example.com/1", reason="원래 사유")
        blacklist.block("http://example.com/1", reason="새 사유")
        listed = blacklist.list_blocked()
        assert len(listed) == 1
        assert listed[0]["reason"] == "새 사유"
