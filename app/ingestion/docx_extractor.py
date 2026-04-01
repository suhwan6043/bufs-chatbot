"""
DOCX 추출기 - python-docx를 이용해 Word 문서에서 텍스트/테이블을 추출합니다.

반환값: List[PageContent]
  - 문서 전체를 단일 PageContent로 반환 (DOCX는 페이지 경계가 없음)
  - 섹션(제목) 단위로 분할해 여러 PageContent를 반환할 수도 있음
"""

import logging
from pathlib import Path
from typing import List

from app.models import PageContent

logger = logging.getLogger(__name__)

# 헤딩 스타일 이름 (한/영 혼용)
_HEADING_STYLES = {
    "heading 1", "heading 2", "heading 3",
    "제목 1", "제목 2", "제목 3",
}

# 헤딩 스타일이 아닌 '굵은 단일 줄' 단락도 섹션 시작으로 처리
_MIN_SECTION_LEN = 200   # 이 글자 수 이상 모이면 하나의 PageContent로 분리


def _table_to_markdown(table) -> str:
    """docx Table 객체를 마크다운 테이블 문자열로 변환합니다."""
    rows = []
    for i, row in enumerate(table.rows):
        # 셀 병합 시 동일 텍스트가 중복될 수 있으므로 인접 중복 제거
        cells = []
        prev = None
        for cell in row.cells:
            txt = cell.text.strip().replace("\n", " ")
            if txt != prev:
                cells.append(txt)
                prev = txt
        rows.append("| " + " | ".join(cells) + " |")
        if i == 0:
            rows.append("|" + "|".join(["---"] * len(cells)) + "|")
    return "\n".join(rows)


def _is_heading(para) -> bool:
    """단락이 헤딩 스타일인지 확인합니다."""
    style_name = (para.style.name or "").lower()
    return any(h in style_name for h in _HEADING_STYLES)


class DocxExtractor:
    """
    DOCX 파일에서 텍스트와 테이블을 추출하는 클래스.

    사용법:
        extractor = DocxExtractor()
        pages = extractor.extract("path/to/file.docx")
    """

    def extract(self, path: str) -> List[PageContent]:
        """
        DOCX 파일을 읽어 PageContent 목록을 반환합니다.

        - 문서를 헤딩(제목) 기준으로 섹션 분리
        - 섹션이 _MIN_SECTION_LEN 글자 이상이면 별도 PageContent
        - 테이블은 마크다운으로 변환하여 tables 필드에 저장

        Args:
            path: DOCX 파일 경로

        Returns:
            List[PageContent] (빈 파일이면 [])
        """
        try:
            from docx import Document  # python-docx
        except ImportError:
            logger.error("python-docx가 설치되지 않았습니다: pip install python-docx")
            return []

        try:
            doc = Document(path)
        except Exception as e:
            logger.error("DOCX 열기 실패 [%s]: %s", path, e)
            return []

        filename = Path(path).name
        sections: List[PageContent] = []

        # 문서 body 순회 (단락 + 테이블 혼재)
        current_text_parts: List[str] = []
        current_tables: List[str] = []
        current_page = 1

        def _flush():
            """현재 섹션을 PageContent로 저장하고 초기화합니다."""
            nonlocal current_page
            text = "\n\n".join(p for p in current_text_parts if p.strip())
            tables = [t for t in current_tables if t.strip()]
            if text.strip() or tables:
                sections.append(PageContent(
                    page_number=current_page,
                    text=text,
                    tables=tables,
                    source_file=str(path),
                ))
                current_page += 1

        for block in doc.element.body:
            tag = block.tag.split("}")[-1] if "}" in block.tag else block.tag

            if tag == "p":
                # 단락 처리
                from docx.oxml.ns import qn
                para_obj = None
                # docx.Document.paragraphs는 element.body의 단락들을 래핑한 것
                # 직접 element → Paragraph 변환
                try:
                    from docx.text.paragraph import Paragraph
                    para_obj = Paragraph(block, doc)
                except Exception:
                    continue

                text = para_obj.text.strip()
                if not text:
                    continue

                if _is_heading(para_obj) and (
                    len("\n\n".join(current_text_parts)) >= _MIN_SECTION_LEN
                    or current_tables
                ):
                    _flush()
                    current_text_parts = []
                    current_tables = []

                current_text_parts.append(text)

            elif tag == "tbl":
                # 테이블 처리
                try:
                    from docx.table import Table
                    tbl_obj = Table(block, doc)
                    md = _table_to_markdown(tbl_obj)
                    if md.strip():
                        current_tables.append(md)
                except Exception as e:
                    logger.debug("테이블 파싱 오류 [%s]: %s", filename, e)

        # 마지막 섹션
        _flush()

        if not sections:
            logger.warning("DOCX에서 추출된 내용 없음: %s", filename)

        logger.info("DOCX 추출 완료: %s → %d섹션", filename, len(sections))
        return sections
