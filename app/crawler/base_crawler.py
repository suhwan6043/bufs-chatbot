"""
크롤러 베이스 클래스 - 모든 BUFS 크롤러의 공통 인터페이스와 유틸리티

BUFS 홈페이지는 그누보드(gnuboard5) CMS를 사용합니다.
"""

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from urllib.robotparser import RobotFileParser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.crawler.change_detector import CrawledItem

logger = logging.getLogger(__name__)

BUFS_BASE_URL = "https://www.bufs.ac.kr"


class BaseCrawler(ABC):
    """
    [역할] BUFS 크롤러 공통 베이스
    [준수] robots.txt 체크, 요청 간격, 타임아웃, User-Agent
    [에러] 개별 실패는 skip, 전체 중단 없음
    """

    # 요청 간격 (초) — 서버 부하 방지
    REQUEST_DELAY = 1.0

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": settings.crawler.user_agent,
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._robots: RobotFileParser | None = None

    # ── 추상 메서드 ────────────────────────────────────────────────

    @abstractmethod
    def crawl(self) -> list[CrawledItem]:
        """크롤링 실행. CrawledItem 리스트 반환."""

    @abstractmethod
    def get_targets(self) -> list[dict]:
        """크롤링 대상 URL 목록 반환."""

    # ── 공통 유틸 ──────────────────────────────────────────────────

    def _fetch(self, url: str) -> str | None:
        """
        HTTP GET 요청을 수행하고 HTML 문자열을 반환합니다.
        실패 시 None 반환 (예외 전파 안 함).
        """
        if not self._is_allowed(url):
            logger.warning("robots.txt에 의해 차단된 URL: %s", url)
            return None

        try:
            time.sleep(self.REQUEST_DELAY)
            resp = self._session.get(
                url,
                timeout=settings.crawler.request_timeout,
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException as e:
            logger.warning("HTTP 요청 실패 [%s]: %s", url, e)
            return None

    def _soup(self, html: str) -> BeautifulSoup:
        """HTML 문자열을 BeautifulSoup 객체로 변환합니다."""
        return BeautifulSoup(html, "lxml")

    def _text_from_html(self, html: str) -> str:
        """HTML에서 스크립트/스타일을 제거하고 순수 텍스트를 반환합니다."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        lines = [line.strip() for line in soup.get_text(separator="\n").splitlines()]
        return "\n".join(line for line in lines if line)

    def _compute_hash(self, content: str) -> str:
        """콘텐츠의 SHA-256 해시를 반환합니다."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _abs_url(self, href: str) -> str:
        """상대 URL을 절대 URL로 변환합니다."""
        if href.startswith("http"):
            return href
        return urljoin(BUFS_BASE_URL, href)

    def _is_allowed(self, url: str) -> bool:
        """robots.txt 규칙에 따라 해당 URL 크롤링이 허용되는지 확인합니다."""
        if self._robots is None:
            self._robots = RobotFileParser()
            robots_url = f"{BUFS_BASE_URL}/robots.txt"
            try:
                self._robots.set_url(robots_url)
                self._robots.read()
            except Exception:
                # robots.txt 읽기 실패 시 허용으로 간주
                self._robots = None
                return True
        return self._robots.can_fetch(settings.crawler.user_agent, url)
