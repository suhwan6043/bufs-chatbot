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
        """신규: 공지 텍스트 인제스트 → 첨부파일 다운로드 + PDF 인제스트 → 그래프 업데이트"""
        chunks = self._event_to_chunks(event)
        if chunks:
            self.chroma.add_chunks(chunks)
        logger.info("신규 인제스트: %s (%d청크)", event.title or event.source_id, len(chunks))

        attach_count = self._ingest_attachments(event)
        self._update_graph(event)
        return len(chunks) + attach_count

    def _handle_modified(self, event: ChangeEvent) -> int:
        """수정: 기존 공지/첨부 청크 삭제 → 재인제스트 → 그래프 업데이트"""
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
        self._update_graph(event)
        return len(chunks) + attach_count

    def _handle_deleted(self, event: ChangeEvent) -> None:
        """삭제: 공지 텍스트 + 첨부 PDF 청크 제거 + 그래프 노드 삭제"""
        deleted = self.chroma.delete_by_source(event.source_id)
        attach_deleted = self._delete_attachment_chunks(event.source_id)
        # 그래프에서도 삭제
        self._remove_from_graph(event)
        logger.info(
            "삭제 처리: %s (공지 %d청크, 첨부 %d청크 제거)",
            event.title or event.source_id,
            deleted,
            attach_deleted,
        )

    # ── 그래프 업데이트 ─────────────────────────────────────────────

    def _update_graph(self, event: ChangeEvent) -> None:
        """공지사항 이벤트를 그래프에 반영 (노드 추가/업데이트 + 엣지 연결)."""
        if event.metadata.get("content_type") != "notice":
            return
        try:
            from app.graphdb.notice_graph_builder import NoticeGraphBuilder
            from app.graphdb.academic_graph import AcademicGraph
            graph = AcademicGraph()
            builder = NoticeGraphBuilder()
            builder.build_from_event(graph, event)
            graph.save()
        except Exception as e:
            logger.warning("그래프 업데이트 실패: %s", e)

    def _remove_from_graph(self, event: ChangeEvent) -> None:
        """삭제된 공지사항을 그래프에서 제거."""
        if event.metadata.get("content_type") != "notice":
            return
        try:
            from app.graphdb.notice_graph_builder import NoticeGraphBuilder
            from app.graphdb.academic_graph import AcademicGraph
            graph = AcademicGraph()
            if NoticeGraphBuilder.remove_notice(graph, event.source_id, event.title):
                graph.save()
                logger.debug("그래프에서 공지 삭제: %s", event.title)
        except Exception as e:
            logger.warning("그래프 삭제 실패: %s", e)

    # ── 첨부파일 (Phase 6) ────────────────────────────────────────

    def _ingest_attachments(self, event: ChangeEvent) -> int:
        """
        이벤트의 첨부파일 URL을 다운로드하고 텍스트 추출 가능한 파일을 ChromaDB에 인제스트합니다.

        처리 방식:
          PDF   → DigitalPDFExtractor → pages_to_chunks → ChromaDB upsert
          HWP/HWPX → HwpExtractor     → pages_to_chunks → ChromaDB upsert
          DOCX  → DocxExtractor       → pages_to_chunks → ChromaDB upsert
          XLSX/XLS → XlsxExtractor    → pages_to_chunks → ChromaDB upsert
          ZIP   → 저장만 (내부 파일 재귀 처리 생략)

        메타데이터 공통 필드:
          source_notice_url, source_url, title, post_date,
          source_name, semester, crawled_at, filename, file_type

        Returns:
            인제스트된 청크 수
        """
        if not event.attachments:
            return 0

        from app.crawler.pdf_downloader import PDFDownloader
        from app.pdf.digital_extractor import DigitalPDFExtractor
        from app.pdf.detector import PDFTypeDetector, PDFType
        from app.ingestion.hwp_extractor import HwpExtractor
        from app.ingestion.docx_extractor import DocxExtractor
        from app.ingestion.xlsx_extractor import XlsxExtractor

        downloader = PDFDownloader()
        paths = downloader.download_attachments(event.attachments, source_url=event.source_id)

        # 공통 메타데이터 (모든 파일 형식 공유)
        common_meta = {
            _NOTICE_URL_KEY: event.source_id,
            "source_url": event.source_id,
            "title": event.title or "",
            "post_date": event.metadata.get("post_date", ""),
            "source_name": event.metadata.get("source_name", ""),
            "crawled_at": event.metadata.get("crawled_at", ""),
            "bo_table": event.metadata.get("bo_table", ""),
        }
        semester = event.metadata.get("semester", "")

        total_chunks = 0

        # ── PDF 처리 ─────────────────────────────────────────
        extractor_pdf = DigitalPDFExtractor()
        detector = PDFTypeDetector()

        for pdf_path in paths.get("pdf", []):
            try:
                pdf_type = detector.detect(str(pdf_path))
                if pdf_type == PDFType.SCANNED:
                    logger.info("스캔 PDF 건너뜀 (OCR 불가): %s", pdf_path.name)
                    continue

                pages = extractor_pdf.extract(str(pdf_path))
                if not pages:
                    continue

                chunks = pages_to_chunks(
                    pages=pages,
                    source_file=str(pdf_path),
                    doc_type=_ATTACH_TYPE,
                    semester=semester,
                    extra_metadata={
                        **common_meta,
                        "filename": pdf_path.name,
                        "file_type": "pdf",
                    },
                )
                if chunks:
                    self.chroma.add_chunks(chunks)
                    total_chunks += len(chunks)
                    logger.info("첨부 PDF 인제스트: %s (%d청크)", pdf_path.name, len(chunks))

            except Exception as e:
                logger.warning("첨부 PDF 처리 실패 [%s]: %s", pdf_path.name, e)

        # ── HWP/HWPX 처리 ─────────────────────────────────────
        extractor_hwp = HwpExtractor()

        for hwp_path in paths.get("hwp", []):
            try:
                pages = extractor_hwp.extract(str(hwp_path))
                if not pages:
                    logger.info("HWP 텍스트 없음 (저장만): %s", hwp_path.name)
                    continue

                chunks = pages_to_chunks(
                    pages=pages,
                    source_file=str(hwp_path),
                    doc_type=_ATTACH_TYPE,
                    semester=semester,
                    extra_metadata={
                        **common_meta,
                        "filename": hwp_path.name,
                        "file_type": hwp_path.suffix.lower().lstrip("."),
                    },
                )
                if chunks:
                    self.chroma.add_chunks(chunks)
                    total_chunks += len(chunks)
                    logger.info("첨부 HWP 인제스트: %s (%d청크)", hwp_path.name, len(chunks))

            except Exception as e:
                logger.warning("첨부 HWP 처리 실패 [%s]: %s", hwp_path.name, e)

        # ── DOCX / XLSX 처리 (other 카테고리) ──────────────────
        extractor_docx = DocxExtractor()
        extractor_xlsx = XlsxExtractor()

        for other_path in paths.get("other", []):
            ext = other_path.suffix.lower()
            try:
                if ext == ".docx":
                    pages = extractor_docx.extract(str(other_path))
                    file_type = "docx"
                elif ext in (".xlsx", ".xls"):
                    pages = extractor_xlsx.extract(str(other_path))
                    file_type = "xlsx"
                else:
                    logger.debug("기타 첨부 저장만: %s", other_path.name)
                    continue

                if not pages:
                    logger.info("%s 텍스트 없음 (저장만): %s", ext, other_path.name)
                    continue

                chunks = pages_to_chunks(
                    pages=pages,
                    source_file=str(other_path),
                    doc_type=_ATTACH_TYPE,
                    semester=semester,
                    extra_metadata={
                        **common_meta,
                        "filename": other_path.name,
                        "file_type": file_type,
                    },
                )
                if chunks:
                    self.chroma.add_chunks(chunks)
                    total_chunks += len(chunks)
                    logger.info(
                        "첨부 %s 인제스트: %s (%d청크)",
                        ext.upper(), other_path.name, len(chunks),
                    )

            except Exception as e:
                logger.warning("첨부 %s 처리 실패 [%s]: %s", ext, other_path.name, e)

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
                    "post_date": event.metadata.get("post_date", ""),
                    "semester": event.metadata.get("semester", ""),
                    "bo_table": event.metadata.get("bo_table", ""),
                    "is_table": False,
                },
            ))

        return chunks
