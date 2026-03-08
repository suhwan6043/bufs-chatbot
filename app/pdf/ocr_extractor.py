"""
스캔 PDF OCR 추출기 (Surya OCR)
텍스트 레이어가 없는 스캔 PDF를 처리합니다.
오프라인 배치 처리로 GPU를 독점 사용합니다.

주의: 실행 전 반드시 Ollama를 중지해야 합니다 (ollama stop).
"""

import os
import logging
from typing import List

from app.config import settings
from app.models import PageContent

logger = logging.getLogger(__name__)


class SuryaOCRExtractor:
    """
    [역할] 스캔 PDF -> 텍스트 추출 (Surya OCR)
    [VRAM] ~2.5GB (LLM과 동시 실행 불가!)
    [실행 시점] 오프라인, 학기당 1회
    [주의] 실행 전 반드시 Ollama 중지: ollama stop
    """

    def __init__(self):
        self.batch_size = settings.pdf.ocr_batch_size
        self.dpi = settings.pdf.ocr_dpi
        self.languages = settings.pdf.ocr_languages

    def extract(self, pdf_path: str) -> List[PageContent]:
        """스캔 PDF에서 OCR로 텍스트를 추출합니다."""
        os.environ["RECOGNITION_BATCH_SIZE"] = str(self.batch_size)

        try:
            from surya.ocr import run_ocr
            from surya.model.detection.segformer import (
                load_model as load_det_model,
                load_processor as load_det_processor,
            )
            from surya.model.recognition.model import (
                load_model as load_rec_model,
                load_processor as load_rec_processor,
            )
        except ImportError:
            logger.error(
                "Surya OCR이 설치되지 않았습니다. "
                "'pip install surya-ocr'로 설치하세요."
            )
            raise

        logger.info(f"Surya OCR 모델 로드 중... (VRAM ~2.5GB 사용)")
        det_model = load_det_model()
        det_processor = load_det_processor()
        rec_model = load_rec_model()
        rec_processor = load_rec_processor()

        images = self._pdf_to_images(pdf_path)
        logger.info(f"OCR 처리 중: {len(images)}페이지")

        langs = [self.languages] * len(images)
        results = run_ocr(
            images, langs,
            det_model, det_processor,
            rec_model, rec_processor,
        )

        return self._format_results(results, pdf_path)

    def _pdf_to_images(self, pdf_path: str) -> list:
        """PDF 페이지를 이미지로 변환합니다."""
        import fitz
        from PIL import Image
        import io

        doc = fitz.open(pdf_path)
        images = []

        for page in doc:
            mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_data))
            images.append(image)

        return images

    def _format_results(self, ocr_results, pdf_path: str) -> List[PageContent]:
        """Surya OCR 결과를 PageContent 리스트로 변환합니다."""
        pages = []
        for page_num, page_result in enumerate(ocr_results):
            lines = []
            for line in page_result.text_lines:
                lines.append(line.text)

            pages.append(PageContent(
                page_number=page_num + 1,
                text="\n".join(lines),
                source_file=pdf_path,
            ))

        logger.info(f"OCR 완료: {pdf_path} ({len(pages)}페이지)")
        return pages
