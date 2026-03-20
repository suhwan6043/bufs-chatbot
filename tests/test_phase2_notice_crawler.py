"""
Phase 2 테스트 — NoticeCrawler (mock HTTP)

실제 HTTP 요청 없이 그누보드 HTML 구조를 mock으로 테스트합니다.
"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.crawler.notice_crawler import (
    NoticeCrawler,
    _parse_post_date,
    _current_semester_start,
    _current_semester_label,
)


# ── HTML 픽스처 ────────────────────────────────────────────────────

def _make_list_html(posts: list[dict]) -> str:
    """그누보드5 형식의 목록 페이지 HTML을 생성합니다."""
    rows = ""
    for p in posts:
        rows += f"""
        <tr>
          <td class="td_num">{p.get('num', '1')}</td>
          <td class="td_subject">
            <a href="{p['href']}">{p['title']}</a>
          </td>
          <td class="td_name">관리자</td>
          <td class="td_date">{p.get('date', '2026. 03. 19')}</td>
          <td class="td_num2">100</td>
        </tr>
        """
    return f"""
    <html><body>
    <div id="bo_list">
      <table><tbody>{rows}</tbody></table>
    </div>
    </body></html>
    """


def _make_post_html(title: str, content: str, attachments: list[str] = None) -> str:
    """그누보드5 형식의 게시글 상세 페이지 HTML을 생성합니다."""
    att_html = ""
    for url in (attachments or []):
        fname = url.split("/")[-1]
        att_html += f'<a href="{url}">{fname}</a>'
    return f"""
    <html><body>
      <h1 id="bo_v_title">{title}</h1>
      <div id="bo_v_info"><span>2026-03-19</span></div>
      <div id="bo_v_atc"><p>{content}</p></div>
      <section id="bo_vc_file">{att_html}</section>
    </body></html>
    """


# ── 날짜 파싱 테스트 ────────────────────────────────────────────────

class TestParseDateDate:
    def test_dot_format(self):
        assert _parse_post_date("2026. 03. 19") == date(2026, 3, 19)

    def test_hyphen_format(self):
        assert _parse_post_date("2026-03-19") == date(2026, 3, 19)

    def test_short_year(self):
        assert _parse_post_date("26-03-19") == date(2026, 3, 19)

    def test_invalid_returns_none(self):
        assert _parse_post_date("invalid") is None

    def test_empty_returns_none(self):
        assert _parse_post_date("") is None


# ── 학기 계산 테스트 ───────────────────────────────────────────────

class TestSemesterHelpers:
    def test_spring_semester_march(self):
        with patch("app.crawler.notice_crawler.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 19)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            start = _current_semester_start()
            assert start == date(2026, 3, 1)

    def test_fall_semester_october(self):
        with patch("app.crawler.notice_crawler.date") as mock_date:
            mock_date.today.return_value = date(2026, 10, 5)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            start = _current_semester_start()
            assert start == date(2026, 9, 1)

    def test_spring_label(self):
        with patch("app.crawler.notice_crawler.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 1)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            assert _current_semester_label() == "2026-1"

    def test_fall_label(self):
        with patch("app.crawler.notice_crawler.date") as mock_date:
            mock_date.today.return_value = date(2026, 9, 1)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            assert _current_semester_label() == "2026-2"


# ── NoticeCrawler 파싱 테스트 ──────────────────────────────────────

@pytest.fixture
def crawler():
    with patch("app.crawler.base_crawler.RobotFileParser"):
        c = NoticeCrawler()
        # robots.txt 체크 비활성화
        c._robots = MagicMock()
        c._robots.can_fetch.return_value = True
        return c


class TestNoticeCrawlerParseListPage:
    def test_extracts_posts(self, crawler):
        target = {
            "name": "학사공지",
            "list_url": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca",
            "bo_table": "notice_aca",
            "content_type": "notice",
        }
        html = _make_list_html([
            {
                "href": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=1",
                "title": "2026학년도 수강신청 안내",
                "date": "2026. 03. 15",
            },
            {
                "href": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=2",
                "title": "성적 처리 안내",
                "date": "2026. 03. 10",
            },
        ])
        posts, stop = crawler._parse_list_page(html, target, date(2026, 3, 1))
        assert len(posts) == 2
        assert posts[0][1] == "2026학년도 수강신청 안내"
        assert not stop

    def test_stops_at_old_posts(self, crawler):
        """학기 시작 이전 게시글이 있으면 stop=True 반환"""
        target = {
            "name": "학사공지",
            "list_url": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca",
            "bo_table": "notice_aca",
            "content_type": "notice",
        }
        html = _make_list_html([
            {
                "href": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=1",
                "title": "이번 학기 공지",
                "date": "2026. 03. 15",
            },
            {
                "href": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=2",
                "title": "지난 학기 공지",
                "date": "2025. 12. 01",  # 학기 시작(3/1) 이전
            },
        ])
        posts, stop = crawler._parse_list_page(html, target, date(2026, 3, 1))
        assert len(posts) == 1  # 이번학기 것만
        assert stop is True

    def test_ignores_different_board_links(self, crawler):
        """다른 bo_table 링크는 무시"""
        target = {
            "name": "학사공지",
            "list_url": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca",
            "bo_table": "notice_aca",
            "content_type": "notice",
        }
        html = _make_list_html([
            {
                "href": "https://www.bufs.ac.kr/bbs/board.php?bo_table=OTHER&wr_id=1",
                "title": "다른 게시판",
                "date": "2026. 03. 15",
            },
        ])
        posts, stop = crawler._parse_list_page(html, target, date(2026, 3, 1))
        assert len(posts) == 0


class TestNoticeCrawlerCrawlPost:
    def test_extracts_title_and_content(self, crawler):
        target = {
            "name": "학사공지",
            "bo_table": "notice_aca",
            "content_type": "notice",
        }
        html = _make_post_html(
            title="2026 수강신청 안내",
            content="수강신청은 3월 2일부터 시작됩니다.",
        )
        with patch.object(crawler, "_fetch", return_value=html):
            item = crawler._crawl_post(
                "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=1",
                "2026 수강신청 안내",
                date(2026, 3, 15),
                target,
                "2026-1",
            )

        assert item is not None
        assert "수강신청" in item.title
        assert "수강신청은 3월 2일" in item.content
        assert item.content_type == "notice"
        assert item.metadata["semester"] == "2026-1"

    def test_extracts_pdf_attachments(self, crawler):
        target = {
            "name": "학사공지",
            "bo_table": "notice_aca",
            "content_type": "notice",
        }
        html = _make_post_html(
            title="공지",
            content="내용",
            attachments=[
                "https://www.bufs.ac.kr/bbs/download.php?bo_table=notice_aca&wr_id=1&no=0",
            ],
        )
        with patch.object(crawler, "_fetch", return_value=html):
            item = crawler._crawl_post(
                "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=1",
                "공지",
                date(2026, 3, 15),
                target,
                "2026-1",
            )

        assert item is not None
        assert len(item.attachments) == 1
        assert "download.php" in item.attachments[0]

    def test_returns_none_on_fetch_failure(self, crawler):
        target = {
            "name": "학사공지",
            "bo_table": "notice_aca",
            "content_type": "notice",
        }
        with patch.object(crawler, "_fetch", return_value=None):
            item = crawler._crawl_post(
                "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=99",
                "공지",
                date(2026, 3, 15),
                target,
                "2026-1",
            )
        assert item is None

    def test_content_hash_changes_with_content(self, crawler):
        target = {
            "name": "학사공지",
            "bo_table": "notice_aca",
            "content_type": "notice",
        }
        html1 = _make_post_html("공지", "내용 A")
        html2 = _make_post_html("공지", "내용 B (수정됨)")

        url = "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=1"

        with patch.object(crawler, "_fetch", return_value=html1):
            item1 = crawler._crawl_post(url, "공지", date(2026, 3, 15), target, "2026-1")

        with patch.object(crawler, "_fetch", return_value=html2):
            item2 = crawler._crawl_post(url, "공지", date(2026, 3, 15), target, "2026-1")

        assert item1.content_hash != item2.content_hash


class TestNoticeCrawlerFullCrawl:
    def test_crawl_returns_only_this_semester(self, crawler):
        """crawl()이 이번 학기 게시글만 반환하는지 통합 테스트.

        1페이지: 이번 학기 게시글 1건
        2페이지 이후: 이전 학기 게시글 → stop 신호로 순회 중단
        """
        list_html_p1 = _make_list_html([
            {
                "href": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=1",
                "title": "이번 학기 공지",
                "date": "2026. 03. 15",
            },
        ])
        # 2페이지는 학기 이전 게시글 → stop=True → 순회 중단
        list_html_p2 = _make_list_html([
            {
                "href": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca&wr_id=0",
                "title": "지난 학기 공지",
                "date": "2025. 12. 01",
            },
        ])
        post_html = _make_post_html("이번 학기 공지", "이번 학기 내용입니다.")

        def mock_fetch(url):
            if "wr_id" in url:
                return post_html
            if "page=2" in url:
                return list_html_p2
            return list_html_p1

        with patch("app.crawler.notice_crawler.date") as mock_date, \
             patch.object(crawler, "_fetch", side_effect=mock_fetch):
            mock_date.today.return_value = date(2026, 3, 19)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            items = crawler.crawl()

        assert len(items) == 1
        assert "이번 학기" in items[0].title
