"""
PDF 출처 페이지 렌더링 엔드포인트.

chat_app.py:_render_pdf_page() (1353~1414줄) 로직 이식 + 고도화.
3단계 매칭: 전체 문장 → 긴 구절(30자) → 짧은 fragment(15자) sliding window
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/source", tags=["source"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _highlight_text(page, chunk_text: str) -> int:
    """
    청크 텍스트를 PDF 페이지에서 찾아 하이라이트.
    3단계 우선순위 매칭으로 기존 15자 sliding window보다 정확도 향상.

    Returns: 하이라이트된 영역 수
    """
    # 전처리
    clean = re.sub(r"\[공지\]\s*", "", chunk_text)
    clean = re.sub(r"\[검색 결과 \d+\].*?\n", "", clean)
    clean = re.sub(r"\[.*?\]\s*", "", clean)
    clean = re.sub(r"[|│┃]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    if not clean or len(clean) < 5:
        return 0

    highlighted: set = set()
    total_rects = 0

    # 문장 분할
    sentences = re.split(r"(?<=[.。!\n])\s*", clean)
    sentences = [s.strip() for s in sentences if len(s.strip()) >= 5]

    # ── Stage 1: 전체 문장 매칭 (가장 정확) ──
    for sent in sentences:
        if len(sent) < 8 or sent in highlighted:
            continue
        rects = page.search_for(sent)
        if rects:
            for rect in rects:
                h = page.add_highlight_annot(rect)
                h.set_colors(stroke=(1, 0.9, 0))
                h.update()
                total_rects += 1
            highlighted.add(sent)

    # ── Stage 2: 긴 구절 매칭 (30자) ──
    for sent in sentences:
        if sent in highlighted or len(sent) < 30:
            continue
        # 30자 구절로 분할, 20자 stride
        for j in range(0, len(sent) - 29, 20):
            frag = sent[j:j + 30]
            if frag in highlighted:
                continue
            rects = page.search_for(frag)
            if rects:
                for rect in rects:
                    h = page.add_highlight_annot(rect)
                    h.set_colors(stroke=(1, 0.9, 0))
                    h.update()
                    total_rects += 1
                highlighted.add(frag)

    # ── Stage 3: 짧은 fragment fallback (15자, 10자 stride) ──
    for sent in sentences:
        if sent in highlighted:
            continue
        if len(sent) <= 30:
            fragments = [sent] if len(sent) >= 8 else []
        else:
            fragments = [sent[j:j + 15] for j in range(0, len(sent) - 14, 10)]

        for frag in fragments:
            if frag in highlighted:
                continue
            rects = page.search_for(frag)
            if rects:
                for rect in rects:
                    h = page.add_highlight_annot(rect)
                    h.set_colors(stroke=(1, 0.9, 0))
                    h.update()
                    total_rects += 1
                highlighted.add(frag)

    return total_rects


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
                n = _highlight_text(pg, chunk_text)
                logger.debug("PDF 하이라이트: %s p.%d — %d 영역", file, page, n)
            except Exception as e:
                logger.debug("하이라이트 실패 (렌더링 계속): %s", e)

        pix = pg.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        png_bytes = pix.tobytes("png")
        doc.close()

        return Response(content=png_bytes, media_type="image/png")

    except Exception as e:
        logger.warning("PDF 렌더링 실패 (%s p.%d): %s", file, page, e)
        return Response(content=b"Render failed", status_code=500)
