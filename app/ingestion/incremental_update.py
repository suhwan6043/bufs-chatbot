"""
증분 업데이터 - ChangeEvent를 받아 ChromaDB를 증분 업데이트합니다.

흐름:
  ChangeEvent(NEW)      → 청킹 → 임베딩 → ChromaDB upsert
                          → 첨부파일 다운로드 → PDF 인제스트 (Phase 6)
  ChangeEvent(MODIFIED) → 기존 청크 삭제 → 청킹 → 임베딩 → ChromaDB upsert
                          → 이전 첨부 청크 삭제 → 첨부파일 재다운로드 → PDF 인제스트
  ChangeEvent(DELETED)  → ChromaDB에서 해당 소스 청크 삭제
                          → 첨부 PDF 청크도 삭제
"""

import logging
from pathlib import Path

from app.config import DATA_DIR
from app.models import Chunk
from app.vectordb import ChromaStore
from app.crawler.change_detector import ChangeEvent, ChangeType
from app.crawler.blacklist import ContentBlacklist
from app.crawler.crawl_logger import UpdateReport
from app.ingestion.chunking import (
    sliding_window,
    detect_cohort,
    make_chunk_id,
    pages_to_chunks,
    MIN_CHUNK_LEN,
)

logger = logging.getLogger(__name__)

# 첨부 PDF 청크를 식별하는 메타데이터 키
_NOTICE_URL_KEY = "source_notice_url"
_ATTACH_TYPE = "notice_attachment"


class IncrementalUpdater:
    """
    [역할] ChangeEvent 목록을 처리하여 ChromaDB를 증분 업데이트
    [핵심] 블랙리스트 체크 → 이벤트 타입별 처리 → UpdateReport 반환
    [의존] ChromaStore, ContentBlacklist (외부 주입)
    """

    def __init__(
        self,
        chroma_store: ChromaStore,
        blacklist: ContentBlacklist,
    ) -> None:
        self.chroma = chroma_store
        self.blacklist = blacklist

    def process_events(self, events: list[ChangeEvent]) -> UpdateReport:
        """
        이벤트 목록을 처리하고 결과 보고서를 반환합니다.
        블랙리스트에 있는 항목은 건너뜁니다.
        """
        report = UpdateReport()

        for event in events:
            if self.blacklist.is_blocked(event.source_id):
                logger.debug("블랙리스트로 건너뜀: %s", event.source_id)
                report.skipped += 1
                continue

            try:
                if event.change_type == ChangeType.NEW:
                    count = self._handle_new(event)
                    report.added += count

                elif event.change_type == ChangeType.MODIFIED:
                    count = self._handle_modified(event)
                    report.updated += count

                elif event.change_type == ChangeType.DELETED:
                    self._handle_deleted(event)
                    report.deleted += 1

            except Exception as e:
                msg = f"{event.source_id}: {e}"
                logger.error("이벤트 처리 실패 [%s] %s", event.change_type.value, msg)
                report.errors.append(msg)
                report.failed_source_ids.add(event.source_id)

        logger.info("증분 업데이트 완료: %s", report.summary())
        return report

    # ── 이벤트 핸들러 ─────────────────────────────────────────────

    def _handle_new(self, event: ChangeEvent) -> int:
        """신규: 공지 텍스트 인제스트 → 첨부파일 다운로드 + PDF 인제스트"""
        chunks = self._event_to_chunks(event)
        if chunks:
            self.chroma.add_chunks(chunks)
        logger.info("신규 인제스트: %s (%d청크)", event.title or event.source_id, len(chunks))

        attach_count = self._ingest_attachments(event)
        return len(chunks) + attach_count

    def _handle_modified(self, event: ChangeEvent) -> int:
        """수정: 기존 공지/첨부 청크 삭제 → 재인제스트"""
        # 공지 텍스트 청크 삭제
        deleted = self.chroma.delete_by_source(event.source_id)
        logger.debug("수정 전 기존 청크 삭제: %d개", deleted)

        # 이전 첨부 PDF 청크 삭제
        self._delete_attachment_chunks(event.source_id)

        # 재인제스트
        chunks = self._event_to_chunks(event)
        if chunks:
            self.chroma.add_chunks(chunks)
        logger.info("수정 재인제스트: %s (%d청크)", event.title or event.source_id, len(chunks))

        attach_count = self._ingest_attachments(event)
        return len(chunks) + attach_count

    def _handle_deleted(self, event: ChangeEvent) -> None:
        """삭제: 공지 텍스트 + 첨부 PDF 청크 제거"""
        deleted = self.chroma.delete_by_source(event.source_id)
        attach_deleted = self._delete_attachment_chunks(event.source_id)
        logger.info(
            "삭제 처리: %s (공지 %d청크, 첨부 %d청크 제거)",
            event.title or event.source_id,
            deleted,
            attach_deleted,
        )

    # ── 첨부파일 (Phase 6) ────────────────────────────────────────

    def _ingest_attachments(self, event: ChangeEvent) -> int:
        """
        이벤트의 첨부파일 URL을 다운로드하고 PDF는 ChromaDB에 인제스트합니다.

        - PDF: 다운로드 → DigitalPDFExtractor → pages_to_chunks → ChromaDB upsert
        - HWP: 다운로드만 (data/attachments/hwp/에 저장, 텍스트 추출 불가)

        Returns:
            인제스트된 청크 수
        """
        if not event.attachments:
            return 0

        from app.crawler.pdf_downloader import PDFDownloader
        from app.pdf.digital_extractor import DigitalPDFExtractor
        from app.pdf.detector import PDFTypeDetector, PDFType

        downloader = PDFDownloader()
        paths = downloader.download_attachments(event.attachments, source_url=event.source_id)

        hwp_count = len(paths.get("hwp", []))
        if hwp_count:
            logger.info("HWP 저장됨 (텍스트 추출 생략): %d건", hwp_count)

        pdf_paths = paths.get("pdf", [])
        if not pdf_paths:
            return 0

        extractor = DigitalPDFExtractor()
        detector = PDFTypeDetector()
        total_chunks = 0

        for pdf_path in pdf_paths:
            try:
                # 스캔 PDF는 건너뜀 (OCR 없이 텍스트 추출 불가)
                pdf_type = detector.detect(str(pdf_path))
                if pdf_type == PDFType.SCANNED:
                    logger.info("스캔 PDF 건너뜀 (OCR 불가): %s", pdf_path.name)
                    continue

                pages = extractor.extract(str(pdf_path))
                if not pages:
                    continue

                chunks = pages_to_chunks(
                    pages=pages,
                    source_file=str(pdf_path),
                    doc_type=_ATTACH_TYPE,
                    semester=event.metadata.get("semester", ""),
                    extra_metadata={
                        _NOTICE_URL_KEY: event.source_id,   # 공지 URL (삭제 연동)
                        "source_url": event.source_id,
                        "title": event.title or "",
                        "crawled_at": event.metadata.get("crawled_at", ""),
                        "filename": pdf_path.name,
                    },
                )

                if chunks:
                    self.chroma.add_chunks(chunks)
                    total_chunks += len(chunks)
                    logger.info(
                        "첨부 PDF 인제스트: %s (%d청크)", pdf_path.name, len(chunks)
                    )

            except Exception as e:
                logger.warning("첨부 PDF 처리 실패 [%s]: %s", pdf_path.name, e)

        return total_chunks

    def _delete_attachment_chunks(self, notice_url: str) -> int:
        """
        특정 공지 URL에 연결된 첨부 PDF 청크를 ChromaDB에서 삭제합니다.
        source_notice_url 메타데이터 키로 조회합니다.
        """
        try:
            result = self.chroma.collection.get(
                where={
                    "$and": [
                        {_NOTICE_URL_KEY: {"$eq": notice_url}},
                        {"doc_type": {"$eq": _ATTACH_TYPE}},
                    ]
                },
                include=[],
            )
            ids = result.get("ids", []) if result else []
            if ids:
                self.chroma.collection.delete(ids=ids)
                logger.debug("첨부 청크 삭제: %d개 (notice=%s)", len(ids), notice_url)
            return len(ids)
        except Exception as e:
            logger.warning("첨부 청크 삭제 실패 [%s]: %s", notice_url, e)
            return 0

    # ── 청킹 ─────────────────────────────────────────────────────

    def _event_to_chunks(self, event: ChangeEvent) -> list[Chunk]:
        """
        ChangeEvent의 content를 Chunk 목록으로 변환합니다.
        source_file 필드에 URL(source_id)을 저장하여 delete_by_source()와 연동합니다.
        """
        if not event.content or not event.content.strip():
            return []

        text_chunks = sliding_window(event.content)
        chunks: list[Chunk] = []

        for idx, text in enumerate(text_chunks):
            if len(text.strip()) < MIN_CHUNK_LEN:
                continue

            cohort_from, cohort_to = detect_cohort(text)
            chunk_id = make_chunk_id(event.source_id, 0, f"text_{idx}", text)

            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=text,
                page_number=0,
                source_file=event.source_id,   # URL을 source_file에 저장
                student_id=None,
                doc_type=event.metadata.get("content_type", "notice"),
                cohort_from=cohort_from,
                cohort_to=cohort_to,
                semester=event.metadata.get("semester", ""),
                metadata={
                    "content_type": "web",
                    "source_url": event.source_id,
                    "title": event.title,
                    "source_name": event.metadata.get("source_name", ""),
                    "crawled_at": event.metadata.get("crawled_at", ""),
                    "chunk_index": idx,
                },
            ))

        return chunks
