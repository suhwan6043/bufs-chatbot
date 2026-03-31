"""
정적 HTML/ASP.NET 페이지 크롤러

단일 URL을 fetch하여:
  - CrawledItem (ChromaDB 인제스트용)
  - sections 리스트 (NetworkX 그래프 노드 생성용)
를 반환한다.

섹션 경계는 h2/h3/h4 또는 텍스트가 짧은 strong/b 태그로 자동 감지하며
하드코딩된 섹션명 없이 HTML 구조에서 동적으로 추출한다.
"""

import hashlib
import logging
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag

from app.crawler.change_detector import CrawledItem

logger = logging.getLogger(__name__)


class StaticPageCrawler:
    """
    [역할] ASP.NET/정적 HTML 페이지를 크롤링하여
           CrawledItem (ChromaDB용) + sections 리스트 (그래프용)으로 파싱.
    [설계] 단일 URL 전용; 내부에서 requests.Session 사용.
    [파싱] 섹션 경계 자동 감지 — h2/h3/strong 태그 기반, 하드코딩 없음.
    """

    REQUEST_DELAY = 1.0  # 서버 부하 방지

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "BUFS-CamChat-Bot/1.0",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    # ── 퍼블릭 API ─────────────────────────────────────────────

    def fetch_and_parse(
        self, url: str, source_name: str
    ) -> tuple[CrawledItem, list[dict]]:
        """
        URL을 fetch하고 파싱합니다.

        Returns:
            (crawled_item, sections)
            - crawled_item : ChromaDB 인제스트용 CrawledItem
            - sections     : 그래프 노드용 섹션 리스트
              각 요소: {"title": str, "fields": dict, "full_text": str}
        """
        time.sleep(self.REQUEST_DELAY)

        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text

        soup = BeautifulSoup(html, "lxml")

        # ChromaDB용 전체 텍스트
        full_plain_text = self._extract_plain_text(soup)

        # 그래프용 섹션 목록
        sections = self._extract_sections(soup)

        # 페이지 제목
        page_title = self._extract_title(soup, source_name)

        content_hash = hashlib.sha256(full_plain_text.encode("utf-8")).hexdigest()

        crawled_item = CrawledItem(
            source_id=url,
            title=page_title,
            content=full_plain_text,
            content_type="guide",
            content_hash=content_hash,
            crawled_at=datetime.now(),
            source_name=source_name,
            attachments=[],
            metadata={
                "content_type": "guide",
                "source_name": source_name,
                "crawled_at": datetime.now().isoformat(timespec="seconds"),
                "section_count": len(sections),
            },
        )

        logger.info(
            "정적 페이지 파싱 완료: %s (%d섹션, %d자)",
            url, len(sections), len(full_plain_text),
        )
        return crawled_item, sections

    # ── 텍스트 추출 ──────────────────────────────────────────────

    def _extract_plain_text(self, soup: BeautifulSoup) -> str:
        """네비게이션·푸터 제거 후 순수 텍스트 추출 (ChromaDB용)."""
        work = BeautifulSoup(str(soup), "lxml")
        for tag in work(["nav", "header", "footer", "script", "style", "noscript"]):
            tag.decompose()
        lines = [line.strip() for line in work.get_text(separator="\n").splitlines()]
        return "\n".join(line for line in lines if line)

    def _extract_title(self, soup: BeautifulSoup, fallback: str) -> str:
        if soup.title:
            t = soup.title.get_text(strip=True)
            if t:
                return t
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return fallback

    # ── 섹션 파싱 ────────────────────────────────────────────────

    @staticmethod
    def _get_heading_level(tag: Tag) -> "int | None":
        """
        태그의 실제 헤딩 레벨 반환 (CSS class 우선, 없으면 tag name 폴백).

        BUFS 계열 사이트는 <h4 class="tit-h3"> 처럼 tag name과 실제 레벨이 불일치.
        반환값: 2, 3, 4, 5 중 하나, 또는 None (헤딩 아님)
        """
        name = tag.name
        cls = " ".join(tag.get("class", []))

        # CSS class 기반 (BUFS 계열 사이트)
        if "tit-h2" in cls or "content-title" in cls:
            return 2
        if "tit-h3" in cls or "sub-title" in cls:
            return 3
        if "tit-h4" in cls:
            return 4
        if "tit-h5" in cls:
            return 5

        # tag name 폴백
        if name == "h2":
            return 2
        if name == "h3":
            return 3
        if name in ("h4", "h5"):
            return int(name[1])
        if name in ("strong", "b"):
            own = tag.get_text(strip=True)
            if not own or len(own) > 30:
                return None
            parent = tag.parent
            if parent and own == parent.get_text(strip=True):
                return 4  # strong/b 단독 텍스트 → 레벨4 취급
        return None

    def _is_section_heading(self, tag: Tag) -> bool:
        """섹션 경계 역할을 하는 태그인지 판별."""
        level = self._get_heading_level(tag)
        if level is None:
            return False
        text = tag.get_text(strip=True)
        return bool(text) and len(text) <= 80

    def _extract_sections(self, soup: BeautifulSoup) -> list[dict]:
        """HTML에서 섹션 목록 추출. 섹션명은 태그에서 동적으로 결정."""
        work = BeautifulSoup(str(soup), "lxml")
        for tag in work(["nav", "header", "footer", "script", "style", "noscript"]):
            tag.decompose()

        # 주요 콘텐츠 영역 탐색
        content_area = (
            work.find(id="content")
            or work.find(id="wrap")
            or work.find(class_=re.compile(r"content|sub.?content|body.?content", re.I))
            or work.body
        )
        if not content_area:
            return []

        all_tags = list(content_area.find_all(True))
        headings = [t for t in all_tags if self._is_section_heading(t)]

        # 섹션 경계가 없으면 전체를 1섹션으로
        if not headings:
            body_text = content_area.get_text(separator="\n", strip=True)
            title_tag = work.find("title")
            page_title = title_tag.get_text(strip=True) if title_tag else "안내"
            if body_text:
                return [{"title": page_title, "fields": {"내용": body_text}, "full_text": body_text}]
            return []

        sections = []
        parent_h3: str | None = None  # 레벨3 제목 추적 (레벨4+ prefix용)

        for i, heading in enumerate(headings):
            raw_title = heading.get_text(strip=True)
            next_heading = headings[i + 1] if i + 1 < len(headings) else None
            level = self._get_heading_level(heading)

            if level is not None and level <= 2:
                # 페이지 타이틀 레벨 — parent 컨텍스트 리셋
                parent_h3 = None
                title = raw_title
            elif level == 3:
                # 카테고리 레벨 (휴학/복학/교내장학금 등) — parent로 등록
                parent_h3 = raw_title
                title = raw_title
            elif level is not None and level >= 4 and parent_h3:
                # 세부항목 — parent_h3 context prefix
                if parent_h3 not in raw_title:
                    title = f"{parent_h3} > {raw_title}"
                else:
                    title = raw_title
            else:
                title = raw_title

            fields, full_text = self._collect_section_content(heading, next_heading)
            # 내용이 없는 섹션(빈 제목줄 등)은 건너뜀
            if title and (fields or full_text.strip()):
                sections.append({
                    "title": title,
                    "fields": fields,
                    "full_text": full_text,
                })

        return sections

    def _collect_section_content(
        self, heading_tag: Tag, next_heading
    ) -> tuple[dict, str]:
        """경계 태그 사이의 내용을 fields dict + full_text로 수집."""
        fields: dict[str, str] = {}
        text_parts: list[str] = []
        key_counts: dict[str, int] = {}

        def add_field(key: str, val: str) -> None:
            val = val.strip()
            if not val:
                return
            if key in fields:
                cnt = key_counts.get(key, 1) + 1
                key_counts[key] = cnt
                # 중복 키는 번호 suffix
                fields[f"{key}_{cnt}"] = val
            else:
                fields[key] = val

        current = heading_tag.next_sibling
        while current is not None:
            if current is next_heading:
                break
            if isinstance(current, Tag):
                # 또 다른 섹션 헤딩이면 중단
                if self._is_section_heading(current) and current is not heading_tag:
                    break
                if current.name == "table":
                    for k, v in self._parse_table(current).items():
                        add_field(k, v)
                elif current.name in ("ul", "ol"):
                    items = [
                        li.get_text(separator=" ", strip=True)
                        for li in current.find_all("li")
                    ]
                    for idx, item in enumerate(items, 1):
                        add_field(f"항목_{idx}", item)
                elif current.name in ("p", "div", "span"):
                    text = current.get_text(separator=" ", strip=True)
                    if text:
                        text_parts.append(text)
            current = current.next_sibling

        return fields, "\n".join(text_parts)

    def _parse_table(self, table_tag: Tag) -> dict:
        """
        3가지 테이블 형태 처리:
          A. 첫 행이 모두 <th> → 이후 행을 데이터로 (header-row 형식)
          B. 각 행이 <th>/<td> 쌍 → th를 키로 (label-value 형식)
          C. 모두 <td> → 내용_N으로 번호 부여
        """
        rows = table_tag.find_all("tr")
        if not rows:
            return {}

        first_cells = rows[0].find_all(["th", "td"])

        # 형식 A: 헤더 행
        if (
            all(c.name == "th" for c in first_cells)
            and len(rows) > 1
            and len(first_cells) > 1
        ):
            headers = [c.get_text(strip=True) for c in first_cells]
            result: dict[str, str] = {}
            key_cnt: dict[str, int] = {}
            for row in rows[1:]:
                cells = row.find_all(["th", "td"])
                for i, cell in enumerate(cells):
                    if i < len(headers) and headers[i]:
                        key = headers[i]
                        val = cell.get_text(separator=" ", strip=True)
                        if val:
                            if key in result:
                                cnt = key_cnt.get(key, 1) + 1
                                key_cnt[key] = cnt
                                result[f"{key}_{cnt}"] = val
                            else:
                                result[key] = val
            return result

        # 형식 B/C: 행 단위 처리
        result = {}
        unnamed = 0
        for row in rows:
            th_cells = row.find_all("th")
            td_cells = row.find_all("td")
            if th_cells and td_cells:
                key = th_cells[0].get_text(strip=True)
                val = " ".join(
                    td.get_text(separator=" ", strip=True) for td in td_cells
                )
                if key and val:
                    if key in result:
                        result[key] = f"{result[key]} / {val.strip()}"
                    else:
                        result[key] = val.strip()
            elif td_cells:
                unnamed += 1
                val = " ".join(
                    td.get_text(separator=" ", strip=True) for td in td_cells
                )
                if val:
                    result[f"내용_{unnamed}"] = val.strip()

        return result
