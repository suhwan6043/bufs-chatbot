"""
PDF 인제스트 스크립트
PDF → 추출 → 청킹 → 임베딩 → ChromaDB 저장까지 한 번에 처리합니다.

사용법:
    # 단일 파일
    python scripts/ingest_pdf.py --pdf data/pdfs/학사안내2023.pdf --student-id 2023

    # 디렉토리 전체
    python scripts/ingest_pdf.py --dir data/pdfs/ --student-id 2023

    # 외국인 안내서
    python scripts/ingest_pdf.py --pdf data/pdfs/외국인안내.pdf --student-id 2023 --doc-type foreign

    # 현재 DB 상태 확인
    python scripts/ingest_pdf.py --status
"""

import sys
import argparse
import logging
import hashlib
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models import PageContent, Chunk, PDFType
from app.pdf.detector import PDFTypeDetector
from app.pdf.digital_extractor import DigitalPDFExtractor
from app.embedding import Embedder
from app.vectordb import ChromaStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ── 청킹 설정 ──────────────────────────────────────────────
CHUNK_SIZE = 500       # 청크당 최대 글자 수 (한국어 기준 ~250 토큰)
CHUNK_OVERLAP = 80     # 청크 간 겹침 글자 수 (문맥 연속성)
MIN_CHUNK_LEN = 50     # 이 이하 청크는 버림


def make_chunks(pages: List[PageContent], student_id: str, doc_type: str) -> List[Chunk]:
    """
    PageContent 리스트를 Chunk 리스트로 변환합니다.

    전략:
    - 텍스트는 문단/줄 단위로 분리 후 CHUNK_SIZE 내에서 합침
    - 테이블은 페이지당 1개 청크로 통째로 유지 (분리 금지)
    - 청크 간 CHUNK_OVERLAP 글자를 겹쳐 문맥 연속성 확보
    """
    chunks: List[Chunk] = []

    for page in pages:
        source_file = str(page.source_file)

        # 1. 테이블 청크 (페이지당, 분리하지 않음)
        for t_idx, table_md in enumerate(page.tables):
            if len(table_md.strip()) < MIN_CHUNK_LEN:
                continue
            chunk_id = _make_id(source_file, page.page_number, f"table_{t_idx}", table_md)
            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=f"[표]\n{table_md}",
                page_number=page.page_number,
                source_file=source_file,
                student_id=student_id,
                doc_type=doc_type,
                metadata={"content_type": "table"},
            ))

        # 2. 텍스트 청크 (슬라이딩 윈도우)
        if page.text:
            text_chunks = _sliding_window(page.text)
            for i, text in enumerate(text_chunks):
                if len(text.strip()) < MIN_CHUNK_LEN:
                    continue
                chunk_id = _make_id(source_file, page.page_number, f"text_{i}", text)
                chunks.append(Chunk(
                    chunk_id=chunk_id,
                    text=text,
                    page_number=page.page_number,
                    source_file=source_file,
                    student_id=student_id,
                    doc_type=doc_type,
                    metadata={"content_type": "text", "chunk_index": i},
                ))

    logger.info(f"청킹 완료: {len(pages)}페이지 → {len(chunks)}개 청크")
    return chunks


def _sliding_window(text: str) -> List[str]:
    """
    텍스트를 CHUNK_SIZE 글자씩, CHUNK_OVERLAP 겹침으로 분할합니다.
    줄바꿈 기준으로 문단 경계를 최대한 보존합니다.
    """
    # 빈 줄 기준으로 문단 분리
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks = []
    current = ""

    for para in paragraphs:
        # 현재 청크에 문단을 추가해도 CHUNK_SIZE 이내이면 합침
        candidate = (current + "\n\n" + para).strip() if current else para
        if len(candidate) <= CHUNK_SIZE:
            current = candidate
        else:
            # 현재 청크 저장
            if current:
                chunks.append(current)
            # 문단 자체가 CHUNK_SIZE보다 크면 강제 분할
            if len(para) > CHUNK_SIZE:
                sub_chunks = _force_split(para)
                chunks.extend(sub_chunks[:-1])
                current = sub_chunks[-1] if sub_chunks else ""
            else:
                # overlap: 이전 청크 끝 CHUNK_OVERLAP 글자를 가져옴
                overlap_text = chunks[-1][-CHUNK_OVERLAP:] if chunks else ""
                current = (overlap_text + "\n\n" + para).strip() if overlap_text else para

    if current:
        chunks.append(current)

    return chunks


def _force_split(text: str) -> List[str]:
    """CHUNK_SIZE보다 큰 텍스트를 강제 분할합니다."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP  # overlap 적용
    return [c for c in chunks if c.strip()]


def _make_id(source_file: str, page_num: int, suffix: str, text: str) -> str:
    """중복 방지용 청크 ID를 생성합니다."""
    content = f"{source_file}:{page_num}:{suffix}:{text[:50]}"
    return hashlib.md5(content.encode()).hexdigest()


def ingest_pdf(pdf_path: str, student_id: str, doc_type: str) -> int:
    """단일 PDF를 처리하여 ChromaDB에 저장합니다. 저장된 청크 수를 반환합니다."""
    path = Path(pdf_path)
    if not path.exists():
        logger.error(f"파일을 찾을 수 없습니다: {pdf_path}")
        return 0

    logger.info(f"처리 시작: {path.name}")

    # 1. PDF 유형 감지
    detector = PDFTypeDetector()
    pdf_type = detector.detect(str(path))
    logger.info(f"PDF 유형: {pdf_type.value}")

    # 2. 텍스트 추출
    if pdf_type == PDFType.DIGITAL:
        extractor = DigitalPDFExtractor()
        pages = extractor.extract(str(path))
    else:
        # 스캔 PDF: Ollama 중지 필요
        logger.warning(
            "스캔 PDF 감지. Surya OCR 사용.\n"
            "⚠️  Ollama가 실행 중이면 'ollama stop' 후 진행하세요."
        )
        input("계속하려면 Enter를 누르세요...")
        from app.pdf.ocr_extractor import SuryaOCRExtractor
        extractor = SuryaOCRExtractor()
        pages = extractor.extract(str(path))

    if not pages:
        logger.warning("추출된 내용이 없습니다.")
        return 0

    logger.info(f"추출 완료: {len(pages)}페이지")

    # 3. 청킹
    chunks = make_chunks(pages, student_id=student_id, doc_type=doc_type)

    if not chunks:
        logger.warning("생성된 청크가 없습니다.")
        return 0

    # 4. 임베딩 + ChromaDB 저장
    embedder = Embedder()
    store = ChromaStore(embedder=embedder)

    # 기존에 같은 파일이 있으면 skip (chunk_id 기반 중복 방지)
    store.add_chunks(chunks)

    logger.info(f"저장 완료: {len(chunks)}개 청크 → ChromaDB")
    return len(chunks)


def show_status():
    """현재 ChromaDB 상태를 출력합니다."""
    from app.embedding import Embedder
    embedder = Embedder()
    store = ChromaStore(embedder=embedder)
    count = store.count()
    print(f"\nChromaDB 현황")
    print(f"  총 청크 수: {count:,}개")
    print(f"  저장 경로: {settings.chroma.persist_dir}")
    print(f"  컬렉션명: {settings.chroma.collection_name}")


def main():
    parser = argparse.ArgumentParser(description="PDF → ChromaDB 인제스트")
    parser.add_argument("--pdf", help="처리할 PDF 파일 경로")
    parser.add_argument("--dir", help="처리할 PDF 디렉토리 경로")
    parser.add_argument("--student-id", default="2023", help="학번 (기본: 2023)")
    parser.add_argument(
        "--doc-type", default="domestic",
        choices=["domestic", "foreign", "transfer", "schedule"],
        help="문서 유형 (domestic=내국인, foreign=외국인, transfer=편입생, schedule=학사일정)",
    )
    parser.add_argument("--status", action="store_true", help="DB 상태 확인")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    pdf_files = []

    if args.pdf:
        pdf_files.append(args.pdf)
    elif args.dir:
        dir_path = Path(args.dir)
        pdf_files = list(dir_path.glob("*.pdf"))
        if not pdf_files:
            logger.warning(f"PDF 파일을 찾을 수 없습니다: {args.dir}")
            return
    else:
        # 기본: data/pdfs/ 디렉토리
        default_dir = Path(settings.pdf.pdf_dir)
        pdf_files = list(default_dir.glob("*.pdf"))
        if not pdf_files:
            print(f"\n처리할 PDF가 없습니다.")
            print(f"PDF 파일을 {settings.pdf.pdf_dir} 에 넣고 다시 실행하세요.")
            return

    total = 0
    for pdf_path in pdf_files:
        count = ingest_pdf(str(pdf_path), args.student_id, args.doc_type)
        total += count

    print(f"\n완료: 총 {total:,}개 청크가 ChromaDB에 저장되었습니다.")
    show_status()


if __name__ == "__main__":
    main()
