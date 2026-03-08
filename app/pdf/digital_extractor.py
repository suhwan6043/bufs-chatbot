"""
디지털 PDF 텍스트/테이블 추출기
텍스트 레이어가 있는 PDF에서 텍스트와 표를 추출합니다.
GPU 불필요, CPU만으로 고속 처리됩니다.
"""

import logging
from typing import List

import fitz  # PyMuPDF
import pdfplumber

from app.models import PageContent

logger = logging.getLogger(__name__)


class DigitalPDFExtractor:
    """
    [역할] 디지털 PDF -> 구조화된 텍스트 + 테이블 추출
    [도구] PyMuPDF(텍스트/구조), pdfplumber(테이블)
    [VRAM] 0GB (CPU 전용)
    [속도] 90페이지 기준 ~5초
    """

    def extract(self, pdf_path: str) -> List[PageContent]:
        """PDF에서 페이지별 텍스트와 테이블을 추출합니다."""
        text_by_page = self._extract_text(pdf_path)
        tables_by_page = self._extract_tables(pdf_path)

        results = []
        total_pages = max(
            max(text_by_page.keys(), default=0),
            max(tables_by_page.keys(), default=0),
        ) + 1

        for page_num in range(total_pages):
            text = text_by_page.get(page_num, "")
            tables = tables_by_page.get(page_num, [])

            headers = self._extract_headers(pdf_path, page_num)

            page_content = PageContent(
                page_number=page_num + 1,
                text=text.strip(),
                tables=[self._table_to_markdown(t) for t in tables if t],
                headers=headers,
                source_file=pdf_path,
            )
            results.append(page_content)

        logger.info(f"디지털 PDF 추출 완료: {pdf_path} ({total_pages}페이지)")
        return results

    def _extract_text(self, pdf_path: str) -> dict:
        """PyMuPDF로 페이지별 텍스트를 추출합니다."""
        doc = fitz.open(pdf_path)
        text_by_page = {}
        for page_num, page in enumerate(doc):
            text_by_page[page_num] = page.get_text("text")
        return text_by_page

    def _extract_tables(self, pdf_path: str) -> dict:
        """pdfplumber로 페이지별 테이블을 추출합니다."""
        tables_by_page = {}
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                if tables:
                    tables_by_page[page_num] = tables
        return tables_by_page

    def _extract_headers(self, pdf_path: str, page_num: int) -> List[str]:
        """PyMuPDF 블록 정보를 사용해 헤더(큰 폰트)를 추출합니다."""
        doc = fitz.open(pdf_path)
        if page_num >= len(doc):
            return []

        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        headers = []

        for block in blocks:
            if block.get("type") != 0:  # 텍스트 블록만
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("size", 0) >= 14:  # 14pt 이상을 헤더로 간주
                        text = span.get("text", "").strip()
                        if text:
                            headers.append(text)
        return headers

    @staticmethod
    def _table_to_markdown(table: List[list]) -> str:
        """2D 배열 테이블을 Markdown 형식으로 변환합니다."""
        if not table or not table[0]:
            return ""

        headers = table[0]
        rows = table[1:]

        md = "| " + " | ".join(str(h or "").replace("\n", " ") for h in headers) + " |"
        md += "\n| " + " | ".join("---" for _ in headers) + " |"
        for row in rows:
            cells = [str(c or "").replace("\n", " ") for c in row]
            # 열 수 맞추기
            while len(cells) < len(headers):
                cells.append("")
            md += "\n| " + " | ".join(cells[: len(headers)]) + " |"

        return md
