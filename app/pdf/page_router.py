"""
페이지별 추출 경로 분류기.

각 PDF 페이지를 디지털/스캔으로 분류하여 적절한 추출 도구로 라우팅한다.
- 디지털: PyMuPDF로 텍스트 + pdfplumber로 표 (필요 시 VLM 폴백)
- 스캔: Surya OCR로 텍스트 (이미지에서 OCR)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import fitz

logger = logging.getLogger(__name__)


@dataclass
class PageClassification:
    page_index: int  # 0-based
    page_number: int  # 1-based
    is_scan: bool
    text_length: int
    has_significant_image: bool
    reason: str


# 페이지 텍스트 길이 임계값 — 이 이하 + 이미지 있으면 스캔으로 분류
DIGITAL_TEXT_MIN = 50


def classify_page(page: fitz.Page) -> PageClassification:
    """단일 페이지를 디지털/스캔으로 분류.

    스캔 판정 기준 (text_len < DIGITAL_TEXT_MIN 시):
      - 임베디드 이미지(get_images) 또는
      - 다수의 vector drawing(>=50개) 또는
      - 페이지에 그릴 콘텐츠가 있음 (rect 내부 픽셀)
    위 중 하나라도 충족하면 스캔으로 분류.
    텍스트가 매우 적은데 시각 콘텐츠도 없는 페이지(여백·표지 등)는 스킵 대상이지만
    여기선 안전하게 스캔으로 보내 OCR이 직접 판정.
    """
    text = page.get_text() or ""
    text_len = len(text.strip())
    images = page.get_images()
    has_img = len(images) > 0

    # vector drawings 카운트 — 일부 PDF는 본문이 vector path로 인코딩됨
    try:
        n_drawings = len(page.get_drawings())
    except Exception:
        n_drawings = 0

    # 페이지 크기 대비 이미지 영역 비율 (선택적 정밀화)
    has_significant = False
    if has_img:
        try:
            page_area = page.rect.width * page.rect.height
            img_area = 0
            for img in images:
                xref = img[0]
                bbox_list = page.get_image_rects(xref)
                for b in bbox_list:
                    img_area += b.width * b.height
            has_significant = (img_area / page_area) > 0.3 if page_area > 0 else False
        except Exception:
            has_significant = has_img

    has_visual_content = has_img or n_drawings >= 50

    if text_len < DIGITAL_TEXT_MIN and has_visual_content:
        return PageClassification(
            page_index=page.number, page_number=page.number + 1,
            is_scan=True, text_length=text_len,
            has_significant_image=has_significant,
            reason=(
                f"text {text_len}자 < {DIGITAL_TEXT_MIN}, "
                f"img={has_img}, drawings={n_drawings}"
            ),
        )

    return PageClassification(
        page_index=page.number, page_number=page.number + 1,
        is_scan=False, text_length=text_len,
        has_significant_image=has_significant,
        reason="디지털 텍스트 추출 가능",
    )


def classify_pdf(pdf_path: str) -> list[PageClassification]:
    """PDF 전체를 페이지 단위 분류."""
    doc = fitz.open(pdf_path)
    results = []
    try:
        for page in doc:
            results.append(classify_page(page))
    finally:
        doc.close()
    return results


def split_pages_by_type(
    classifications: list[PageClassification],
) -> tuple[list[int], list[int]]:
    """디지털 / 스캔 페이지 인덱스 리스트 반환."""
    digital = [c.page_index for c in classifications if not c.is_scan]
    scan = [c.page_index for c in classifications if c.is_scan]
    return digital, scan
