"""
PDF 출처 페이지 렌더링 엔드포인트.

chat_app.py:_render_pdf_page() (1353~1414줄) 로직 이식.
프론트엔드 SourcePanel 컴포넌트에서 호출.
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/source", tags=["source"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@router.get("/pdf")
async def render_pdf_page(
    file: str = Query(..., description="PDF 파일 경로 (예: data/pdfs/학사안내.pdf)"),
    page: int = Query(1, ge=1, description="페이지 번호 (1-indexed)"),
    chunk_text: str = Query("", description="하이라이트할 청크 텍스트"),
):
    """
    PDF 페이지를 PNG 이미지로 렌더링.
    chunk_text가 있으면 해당 텍스트 위치를 노란색 하이라이트.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return Response(content=b"PyMuPDF not installed", status_code=500)

    # 경로 resolve
    path = Path(file)
    if not path.is_absolute():
        path = (_PROJECT_ROOT / file).resolve()

    # 경로 순회 공격 방지
    try:
        path.resolve().relative_to(_PROJECT_ROOT.resolve())
    except ValueError:
        return Response(content=b"Access denied", status_code=403)

    if not path.exists():
        return Response(content=b"File not found", status_code=404)

    try:
        doc = fitz.open(str(path))
        page_idx = max(0, page - 1)
        if page_idx >= len(doc):
            return Response(content=b"Page out of range", status_code=404)

        pg = doc[page_idx]

        # 하이라이트 (실패해도 렌더링 계속)
        if chunk_text:
            try:
                clean = re.sub(r"\[공지\]\s*", "", chunk_text)
                clean = re.sub(r"[|│┃]", " ", clean)
                clean = re.sub(r"\s+", " ", clean).strip()

                sentences = re.split(r"[.\n]+", clean)
                highlighted: set = set()

                for sent in sentences:
                    sent = sent.strip()
                    if len(sent) < 8:
                        continue
                    if len(sent) <= 30:
                        fragments = [sent]
                    else:
                        fragments = [sent[j:j + 15] for j in range(0, len(sent) - 14, 10)]

                    for frag in fragments:
                        if frag in highlighted:
                            continue
                        rects = pg.search_for(frag)
                        for rect in rects:
                            h = pg.add_highlight_annot(rect)
                            h.set_colors(stroke=(1, 0.9, 0))
                            h.update()
                        if rects:
                            highlighted.add(frag)
            except Exception as e:
                logger.debug("하이라이트 실패 (렌더링 계속): %s", e)

        pix = pg.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        png_bytes = pix.tobytes("png")
        doc.close()

        return Response(content=png_bytes, media_type="image/png")

    except Exception as e:
        logger.warning("PDF 렌더링 실패 (%s p.%d): %s", file, page, e)
        return Response(content=b"Render failed", status_code=500)
