"""
스캔 PDF OCR 추출기 (Surya OCR ≥0.17)

텍스트 레이어가 없는 스캔 PDF를 처리. 오프라인 배치 처리로 GPU 독점 사용.

주의: 실행 전 LM Studio 등 GPU 점유 프로세스를 중지할 것.
"""

import os
import logging
from typing import List, Optional, Sequence

from app.config import settings
from app.models import PageContent

logger = logging.getLogger(__name__)


class SuryaOCRExtractor:
    """
    [역할] 스캔 PDF -> 텍스트 추출 (Surya OCR ≥0.17)
    [VRAM] ~2.5GB (LLM과 동시 실행 불가!)
    [실행 시점] 오프라인, 학기당 1회
    [주의] 실행 전 반드시 LM Studio 중지
    """

    def __init__(self):
        self.batch_size = settings.pdf.ocr_batch_size
        self.dpi = settings.pdf.ocr_dpi
        # langs는 Surya 0.17 API에서 더 이상 사용되지 않음 (자동 감지) — 호환 위해 보관
        self.languages = settings.pdf.ocr_languages

    def extract(
        self,
        pdf_path: str,
        page_indices: Optional[Sequence[int]] = None,
    ) -> List[PageContent]:
        """스캔 PDF에서 OCR로 텍스트를 추출.

        Args:
            pdf_path: 대상 PDF 경로
            page_indices: 0-based 페이지 인덱스 리스트. None이면 전체.
                          페이지별 라우팅용 (디지털 페이지 OCR 스킵).

        Returns:
            지정된 페이지의 PageContent 리스트.
            page_number는 PDF 내 1-based 원본 번호.
        """
        # 배치 사이즈 환경변수 (Surya 0.17 settings는 lazy 환경변수 읽음)
        if self.batch_size:
            os.environ.setdefault("RECOGNITION_BATCH_SIZE", str(self.batch_size))

        try:
            from surya.detection import DetectionPredictor
            from surya.recognition import (
                RecognitionPredictor, FoundationPredictor, TaskNames,
            )
        except ImportError:
            logger.error(
                "Surya OCR이 설치되지 않았습니다. "
                "'pip install surya-ocr>=0.17.0'로 설치하세요."
            )
            raise

        logger.info("Surya OCR 모델 로드 중... (VRAM ~2.5GB 사용)")
        det_predictor = DetectionPredictor()
        foundation = FoundationPredictor()
        rec_predictor = RecognitionPredictor(foundation)

        images, original_indices = self._pdf_to_images(
            pdf_path, page_indices=page_indices,
        )
        if not images:
            logger.info("OCR 대상 페이지 없음 — 스킵")
            return []
        logger.info(
            "OCR 처리 중: %d페이지 (원본 페이지 인덱스 %s%s)",
            len(images),
            original_indices[:5],
            "..." if len(original_indices) > 5 else "",
        )

        task_names = [TaskNames.ocr_with_boxes] * len(images)
        results = rec_predictor(
            images,
            task_names=task_names,
            det_predictor=det_predictor,
            recognition_batch_size=self.batch_size or None,
        )

        return self._format_results(results, pdf_path, original_indices)

    def _pdf_to_images(
        self,
        pdf_path: str,
        page_indices: Optional[Sequence[int]] = None,
    ) -> tuple[list, list[int]]:
        """PDF 페이지를 이미지로 변환.

        Returns:
            (images, original_page_indices_0based)
        """
        import fitz
        from PIL import Image
        import io

        doc = fitz.open(pdf_path)
        images = []
        used_indices: list[int] = []

        target = (
            sorted(set(page_indices))
            if page_indices is not None
            else list(range(doc.page_count))
        )

        for idx in target:
            if not (0 <= idx < doc.page_count):
                continue
            page = doc[idx]
            mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_data)).convert("RGB")
            images.append(image)
            used_indices.append(idx)

        doc.close()
        return images, used_indices

    def _format_results(
        self,
        ocr_results,
        pdf_path: str,
        original_indices: list[int],
    ) -> List[PageContent]:
        """Surya OCR 결과를 PageContent 리스트로 변환.

        Args:
            original_indices: ocr_results와 같은 길이의 0-based 페이지 인덱스 리스트.
                              page_number는 +1해서 1-based 원본 페이지 번호로 저장.
        """
        pages = []
        for i, page_result in enumerate(ocr_results):
            orig_page_idx = (
                original_indices[i] if i < len(original_indices) else i
            )
            lines = [line.text for line in page_result.text_lines]

            pages.append(PageContent(
                page_number=orig_page_idx + 1,
                text="\n".join(lines),
                source_file=pdf_path,
            ))

        logger.info(f"OCR 완료: {pdf_path} ({len(pages)}페이지)")
        return pages
