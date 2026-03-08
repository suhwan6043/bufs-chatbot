"""
PDF 유형 감지기
디지털 PDF(텍스트 레이어 내장)와 스캔 PDF를 자동 판별합니다.
"""

import logging

import fitz  # PyMuPDF

from app.config import settings
from app.models import PDFType

logger = logging.getLogger(__name__)


class PDFTypeDetector:
    """
    [역할] PDF가 디지털인지 스캔본인지 자동 판별
    [방법] 텍스트 레이어 존재 여부 + 글자 수 기준
    [기준] 페이지당 평균 100자 이상이면 디지털
    """

    def __init__(self, threshold: int = None):
        self.threshold = threshold or settings.pdf.digital_threshold

    def detect(self, pdf_path: str) -> PDFType:
        """PDF 유형을 감지하여 PDFType을 반환합니다."""
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            logger.warning(f"빈 PDF 파일: {pdf_path}")
            return PDFType.SCANNED

        total_text = ""
        for page in doc:
            total_text += page.get_text()

        chars_per_page = len(total_text) / len(doc)

        if chars_per_page > self.threshold:
            logger.info(
                f"디지털 PDF 감지: {pdf_path} "
                f"(페이지당 {chars_per_page:.0f}자)"
            )
            return PDFType.DIGITAL
        else:
            logger.info(
                f"스캔 PDF 감지: {pdf_path} "
                f"(페이지당 {chars_per_page:.0f}자)"
            )
            return PDFType.SCANNED
