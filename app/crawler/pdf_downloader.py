"""
첨부파일 다운로더 - 공지사항 첨부 PDF/HWP를 로컬에 저장합니다.

저장 경로:
  PDF   → data/pdfs/crawled/
  HWP   → data/attachments/hwp/

최대 파일 크기: 100MB
"""

import hashlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import unquote, unquote_plus, urlparse

import requests

from app.config import DATA_DIR, settings

logger = logging.getLogger(__name__)

PDF_DIR = DATA_DIR / "pdfs" / "crawled"
HWP_DIR = DATA_DIR / "attachments" / "hwp"
OTHER_DIR = DATA_DIR / "attachments" / "other"   # xlsx, zip, docx 등
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

# 다운로드 대상 확장자 (PDF만 ChromaDB 인제스트, 나머지는 저장만)
_SUPPORTED_EXTS = {".pdf", ".hwp", ".hwpx", ".xlsx", ".xls", ".docx", ".zip"}


def _sanitize_filename(name: str) -> str:
    """파일명에서 OS 허용 불가 문자를 제거합니다."""
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip()[:200]


def _filename_from_response(url: str, resp) -> str:
    """
    Content-Disposition 헤더에서 파일명을 추출합니다.
    없으면 URL path에서 추출, 그것도 없으면 URL MD5 해시로 대체합니다.
    gnuboard5 download.php URL의 경우 Content-Disposition이 필수입니다.
    """
    cd = resp.headers.get("Content-Disposition", "")
    if cd:
        # RFC 5987: filename*=UTF-8''...
        m = re.search(r"filename\*=UTF-8''(.+)", cd, re.IGNORECASE)
        if m:
            return _sanitize_filename(unquote_plus(m.group(1).strip()))
        # 일반: filename="파일명.pdf" 또는 filename=파일명.pdf
        m = re.search(r'filename=["\']?([^"\';\r\n]+)', cd)
        if m:
            raw = m.group(1).strip().strip('"\'')
            # URL 인코딩 우선 시도 (gnuboard5: percent + '+' as space)
            if "%" in raw:
                try:
                    raw = unquote_plus(raw)
                except Exception:
                    pass
            else:
                # EUC-KR 인코딩 처리 (한국 서버 일부)
                try:
                    raw = raw.encode("latin-1").decode("euc-kr")
                except Exception:
                    pass
            return _sanitize_filename(raw)

    # URL path에서 추출 (download.php가 아닌 직접 링크)
    path = unquote(urlparse(url).path)
    name = Path(path).name
    if name and "." in name:
        return _sanitize_filename(name)

    # 최후 수단: URL MD5 해시
    return hashlib.md5(url.encode()).hexdigest()[:12] + ".bin"


class PDFDownloader:
    """
    공지사항 첨부파일 다운로더.

    PDF  → data/pdfs/crawled/
    HWP  → data/attachments/hwp/

    동일 파일명 + 동일 크기면 재다운로드 스킵합니다.
    """

    def __init__(self) -> None:
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        HWP_DIR.mkdir(parents=True, exist_ok=True)
        OTHER_DIR.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": settings.crawler.user_agent})

    def download_attachments(
        self,
        urls: list[str],
        source_url: str = "",
    ) -> dict[str, list[Path]]:
        """
        첨부파일 URL 목록을 받아 신규/변경된 파일만 다운로드합니다.

        Args:
            urls:       첨부파일 URL 목록
            source_url: 게시글 URL (Referer / 세션 수립용).
                        gnuboard5는 게시글 방문 후 동일 세션으로 다운로드해야 합니다.

        Returns:
            {"pdf": [Path, ...], "hwp": [Path, ...], "other": [Path, ...]}
        """
        if not urls:
            return {"pdf": [], "hwp": [], "other": []}

        # 게시글 방문으로 세션 쿠키 수립
        if source_url:
            try:
                time.sleep(1.0)
                self._session.get(source_url, timeout=15)
                self._referer = source_url
            except Exception:
                self._referer = ""
        else:
            self._referer = ""

        result: dict[str, list[Path]] = {"pdf": [], "hwp": [], "other": []}
        for url in urls:
            path = self._download_one(url)
            if path:
                ext = path.suffix.lower()
                if ext == ".pdf":
                    result["pdf"].append(path)
                elif ext in (".hwp", ".hwpx"):
                    result["hwp"].append(path)
                else:
                    result["other"].append(path)
        return result

    # ── 하위 호환 ──────────────────────────────────────────────────

    def download_if_new(self, attachment_urls: list[str]) -> list[Path]:
        """기존 코드 호환용 - PDF 경로 목록만 반환."""
        return self.download_attachments(attachment_urls)["pdf"]

    # ── 내부 ──────────────────────────────────────────────────────

    def _download_one(self, url: str) -> Path | None:
        """단일 URL을 다운로드합니다. 스킵/실패 시 None 반환."""
        try:
            time.sleep(1.0)
            headers = {}
            if hasattr(self, "_referer") and self._referer:
                headers["Referer"] = self._referer
            with self._session.get(
                url,
                stream=True,
                timeout=settings.crawler.request_timeout,
                headers=headers,
            ) as resp:
                resp.raise_for_status()

                ct = resp.headers.get("Content-Type", "")
                if "text/html" in ct:
                    logger.warning("첨부파일 응답이 HTML (로그인 리다이렉트?): %s", url)
                    return None

                filename = _filename_from_response(url, resp)
                ext = Path(filename).suffix.lower()

                # 지원 확장자 확인; Content-Type으로 재시도
                if ext not in _SUPPORTED_EXTS:
                    if "pdf" in ct:
                        filename = Path(filename).stem + ".pdf"
                        ext = ".pdf"
                    elif "hwp" in ct or "hangul" in ct.lower():
                        filename = Path(filename).stem + ".hwp"
                        ext = ".hwp"
                    else:
                        logger.debug("지원하지 않는 첨부 형식 [%s]: %s", ct, url)
                        return None

                if ext == ".pdf":
                    dest_dir = PDF_DIR
                elif ext in (".hwp", ".hwpx"):
                    dest_dir = HWP_DIR
                else:
                    dest_dir = OTHER_DIR
                dest = dest_dir / filename

                # 크기 동일하면 스킵
                cl = resp.headers.get("Content-Length")
                if dest.exists() and cl:
                    if dest.stat().st_size == int(cl):
                        logger.debug("스킵 (동일): %s", filename)
                        return None

                # 스트리밍 저장
                total = 0
                tmp = dest.with_suffix(".tmp")
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                        total += len(chunk)
                        if total > MAX_FILE_SIZE:
                            tmp.unlink(missing_ok=True)
                            logger.warning("파일 크기 초과 (>100MB), 중단: %s", url)
                            return None

                tmp.rename(dest)
                logger.info("다운로드 완료: %s (%dKB)", filename, total // 1024)
                return dest

        except Exception as e:
            logger.warning("다운로드 실패 [%s]: %s", url, e)
            return None
