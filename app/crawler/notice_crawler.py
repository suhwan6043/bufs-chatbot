"""
BUFS 공지사항 크롤러 - 그누보드5(gnuboard5) 기반 게시판

대상: https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca
구조: 그누보드5 표준 HTML 구조 사용

크롤링 범위: 이번 학기 게시글만 수집
  - 1학기: 해당 연도 3월 1일 이후
  - 2학기: 해당 연도 9월 1일 이후
"""

import logging
import re
from datetime import date, datetime
from urllib.parse import urlencode, urlparse, parse_qs

from app.config import settings
from app.crawler.base_crawler import BaseCrawler, BUFS_BASE_URL
from app.crawler.change_detector import CrawledItem

logger = logging.getLogger(__name__)


def _current_semester_start() -> date:
    """
    현재 학기 시작일을 반환합니다.
      - 1학기 (2월 20일~8월): 해당 연도 2월 20일
      - 2학기 (9월~1월): 해당 연도 9월 1일
    """
    today = date.today()
    if today.month >= 9:
        return date(today.year, 9, 1)
    elif today.month >= 2:
        return date(today.year, 2, 20)
    else:
        # 1월: 직전 연도 2학기
        return date(today.year - 1, 9, 1)


def _current_semester_label() -> str:
    """현재 학기 레이블 반환 (예: '2026-1')"""
    today = date.today()
    if today.month >= 9:
        return f"{today.year}-2"
    elif today.month >= 2:
        return f"{today.year}-1"
    else:
        return f"{today.year - 1}-2"


def _parse_post_date(date_str: str) -> date | None:
    """
    그누보드 날짜 문자열을 date 객체로 변환합니다.
    지원 형식:
      - "2026. 03. 19"  (목록 페이지)
      - "2026-03-19"
      - "26-03-19"      (연도 2자리)
    """
    date_str = date_str.strip()
    # "2026. 03. 19" → "2026-03-19"
    normalized = re.sub(r'[\s.]+', '-', date_str).strip('-')
    # 연속된 하이픈 제거
    normalized = re.sub(r'-+', '-', normalized)

    for fmt in ("%Y-%m-%d", "%y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    logger.debug("날짜 파싱 실패: %r", date_str)
    return None


class NoticeCrawler(BaseCrawler):
    """
    BUFS 그누보드 게시판 크롤러.

    그누보드5 표준 HTML 구조:
      목록: #bo_list > table > tbody > tr
            td.td_subject > a  (제목/링크)
            td.td_datetime     (날짜)
      상세: #bo_v_title 또는 h1   (제목)
            #bo_v_atc            (본문)
            #bo_vc_file a        (첨부파일)
    """

    # ── 크롤링 대상 게시판 목록 ─────────────────────────────────────
    DEFAULT_TARGETS = [
        {
            "name": "학사공지",
            "list_url": f"{BUFS_BASE_URL}/bbs/board.php?bo_table=notice_aca",
            "bo_table": "notice_aca",
            "content_type": "notice",
        },
        {
            "name": "일반공지",
            "list_url": f"{BUFS_BASE_URL}/bbs/board.php?bo_table=notice",
            "bo_table": "notice",
            "content_type": "notice",
        },
    ]

    def get_targets(self) -> list[dict]:
        return self.DEFAULT_TARGETS

    def crawl(self) -> list[CrawledItem]:
        """
        모든 대상 게시판을 크롤링하여 이번 학기 게시글만 반환합니다.
        """
        semester_start = _current_semester_start()
        semester_label = _current_semester_label()
        logger.info(
            "크롤링 시작: 학기=%s, 기준일=%s",
            semester_label, semester_start.isoformat(),
        )

        all_items: list[CrawledItem] = []
        for target in self.get_targets():
            items = self._crawl_board(target, semester_start, semester_label)
            all_items.extend(items)
            logger.info(
                "[%s] 수집 완료: %d건",
                target["name"], len(items),
            )

        logger.info("전체 수집 완료: %d건", len(all_items))
        return all_items

    # ── 게시판 단위 크롤링 ────────────────────────────────────────

    def _crawl_board(
        self,
        target: dict,
        semester_start: date,
        semester_label: str,
    ) -> list[CrawledItem]:
        """
        단일 게시판의 목록 페이지를 순회하며 이번 학기 게시글을 수집합니다.

        날짜가 semester_start 이전이 되면 즉시 중단합니다.
        """
        items: list[CrawledItem] = []
        base_url = target["list_url"]

        for page in range(1, settings.crawler.max_pages_per_board + 1):
            list_url = f"{base_url}&page={page}" if page > 1 else base_url
            logger.debug("목록 페이지 요청: %s", list_url)

            html = self._fetch(list_url)
            if not html:
                break

            posts, stop = self._parse_list_page(html, target, semester_start)

            for url, title, post_date in posts:
                item = self._crawl_post(url, title, post_date, target, semester_label)
                if item:
                    items.append(item)

            if stop:
                logger.info(
                    "[%s] 학기 시작일(%s) 이전 게시글 도달, 순회 중단",
                    target["name"], semester_start.isoformat(),
                )
                break

        return items

    def _parse_list_page(
        self,
        html: str,
        target: dict,
        semester_start: date,
    ) -> tuple[list[tuple[str, str, date | None]], bool]:
        """
        목록 페이지에서 (URL, 제목, 날짜) 튜플 목록을 추출합니다.

        Returns:
            (posts, stop_flag)
            stop_flag=True이면 이 페이지에 학기 이전 게시글이 있어 순회 중단 신호
        """
        soup = self._soup(html)
        posts: list[tuple[str, str, date | None]] = []
        stop = False

        # BUFS 커스텀 스킨: div.tbl_wrap > table > tbody
        # 그누보드5 기본: #bo_list > table > tbody
        tbody = (
            soup.select_one("div.tbl_wrap table tbody")
            or soup.select_one("#bo_list table tbody")
            or soup.select_one("table tbody")
        )
        if not tbody:
            logger.warning("게시판 목록 파싱 실패: tbody 없음")
            return posts, True

        for tr in tbody.select("tr"):
            # 제목 링크: td.td_subject > a
            subject_td = tr.select_one("td.td_subject")
            if not subject_td:
                continue

            link_tag = subject_td.select_one("a[href]")
            if not link_tag:
                continue

            href = self._abs_url(link_tag["href"])
            # bo_table 파라미터 검증 (다른 게시판 링크 필터링)
            if target["bo_table"] not in href:
                continue

            title = link_tag.get_text(strip=True)
            if not title:
                continue

            # 날짜: BUFS 커스텀=td.td_date, 그누보드5 기본=td.td_datetime
            date_td = tr.select_one("td.td_date") or tr.select_one("td.td_datetime")
            post_date = None
            if date_td:
                post_date = _parse_post_date(date_td.get_text(strip=True))

            # 학기 기준일 이전 게시글 → 중단 신호
            if post_date and post_date < semester_start:
                stop = True
                break

            posts.append((href, title, post_date))

        return posts, stop

    # ── 게시글 단위 크롤링 ────────────────────────────────────────

    def _crawl_post(
        self,
        url: str,
        title: str,
        post_date: date | None,
        target: dict,
        semester_label: str,
    ) -> CrawledItem | None:
        """단일 게시글 상세 페이지를 크롤링합니다."""
        html = self._fetch(url)
        if not html:
            return None

        soup = self._soup(html)

        # ── 제목 ──────────────────────────────────────────────────
        # 그누보드5: #bo_v_title 또는 h1
        title_tag = soup.select_one("#bo_v_title") or soup.select_one("h1")
        if title_tag:
            title = title_tag.get_text(strip=True) or title

        # ── 본문 ──────────────────────────────────────────────────
        # 그누보드5 기본 선택자로 시도, 없으면 폴백 체인
        content_tag = soup.select_one("#bo_v_atc")

        # 기본 선택자가 없거나 텍스트가 너무 짧으면 폴백 시도
        _MIN_BODY_LEN = 30
        if not content_tag or len(content_tag.get_text(strip=True)) < _MIN_BODY_LEN:
            original = content_tag  # 원본 보존
            for selector in (".bo_v_atc", "#bo_v_con", ".view-content", "article"):
                candidate = soup.select_one(selector)
                if candidate and len(candidate.get_text(strip=True)) >= _MIN_BODY_LEN:
                    content_tag = candidate
                    break
            else:
                # 폴백에서도 못 찾으면 원본(짧더라도) 사용
                if original:
                    content_tag = original

        if not content_tag:
            logger.warning("본문 없음: %s", url)
            return None

        content_text = self._text_from_html(str(content_tag))
        if not content_text.strip():
            logger.debug("빈 본문: %s", url)
            return None

        # 제목을 본문 앞에 붙여 검색 품질 향상
        full_content = f"[공지] {title}\n\n{content_text}"

        # ── 첨부파일 링크 ─────────────────────────────────────────
        # BUFS 커스텀 스킨: section.fileDownload a
        # 그누보드5 기본: #bo_vc_file a, .bo_v_file a
        _ATTACH_EXTS = {".pdf", ".hwp", ".hwpx", ".xlsx", ".xls", ".zip", ".docx"}
        attachments: list[str] = []
        for a_tag in soup.select(
            "section.fileDownload a[href], "
            "#bo_vc_file a[href], "
            ".bo_v_file a[href]"
        ):
            href = a_tag.get("href", "")
            if not href:
                continue
            abs_href = self._abs_url(href)
            # download.php 링크는 무조건 포함 (gnuboard5 다운로드 URL)
            # 직접 파일 링크는 확장자로 필터링
            if "download.php" in href:
                attachments.append(abs_href)
            else:
                from pathlib import Path
                if Path(href.split("?")[0]).suffix.lower() in _ATTACH_EXTS:
                    attachments.append(abs_href)

        # ── 날짜 문자열 (메타데이터용) ────────────────────────────
        date_str = post_date.isoformat() if post_date else ""

        return CrawledItem(
            source_id=url,
            title=title,
            content=full_content,
            content_type=target["content_type"],
            content_hash=self._compute_hash(full_content),
            crawled_at=datetime.now(),
            source_name=target["name"],
            attachments=attachments,
            metadata={
                "content_type": target["content_type"],
                "source_name": target["name"],
                "post_date": date_str,
                "semester": semester_label,
                "crawled_at": datetime.now().isoformat(timespec="seconds"),
                "bo_table": target["bo_table"],
            },
        )
