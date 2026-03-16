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
import re
import json
import argparse
import logging
import hashlib
from datetime import datetime
from dataclasses import asdict
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models import PageContent, Chunk, PDFType
from app.pdf.detector import PDFTypeDetector
from app.pdf.digital_extractor import DigitalPDFExtractor
from app.pdf import timetable_parser
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

# ── 학번 범위 감지 ──────────────────────────────────────────
_COHORT_MIN = 2016  # 지원하는 학번 최솟값
_COHORT_MAX = 2030  # 공통 콘텐츠 상한 (미래 학번 포함)


def detect_cohort(text: str) -> Tuple[int, int]:
    """
    청크 텍스트에서 적용 학번 범위를 감지합니다.

    매칭 우선순위:
      1. 범위 패턴   "2021~2023학번", "2021·2023학번"
      2. 방향 패턴   "2024학번 이후/부터", "2023학번 이전/까지"
      3. 복수 단일   "2023학번 … 2024학번" → (2023, 2024)
      4. 단일        "2024학번" → (2024, 2024)
      5. 미감지      공통 콘텐츠로 간주 → (2016, 2030)

    Returns:
        (cohort_from, cohort_to) 정수 튜플
    """
    # 1. 범위 패턴
    m = re.search(r'(20\d{2})[~·](20\d{2})학번', text)
    if m:
        return (int(m.group(1)), int(m.group(2)))

    # 2. 방향 패턴 (이후/부터)
    m = re.search(r'(20\d{2})학번\s*(?:이후|부터)', text)
    if m:
        return (int(m.group(1)), _COHORT_MAX)

    # 2. 방향 패턴 (이전/까지)
    m = re.search(r'(20\d{2})학번\s*(?:이전|까지)', text)
    if m:
        return (_COHORT_MIN, int(m.group(1)))

    # 3·4. 단일 학번 열거 (201x ~ 202x 범위만 유효)
    years = sorted({int(y) for y in re.findall(r'(201[6-9]|202[0-9])학번', text)})
    if years:
        return (years[0], years[-1])

    # 5. 공통 콘텐츠
    return (_COHORT_MIN, _COHORT_MAX)


def make_chunks(
    pages: List[PageContent],
    student_id: str,
    doc_type: str,
    semester: str = "",
) -> List[Chunk]:
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
        raw_tables = page.raw_tables or []
        for t_idx, table_md in enumerate(page.tables):
            if len(table_md.strip()) < MIN_CHUNK_LEN:
                continue

            raw = raw_tables[t_idx] if t_idx < len(raw_tables) else []

            # ── 수업시간표 전용 처리 ──────────────────────────
            if doc_type == "timetable" and raw and timetable_parser.is_timetable_table(raw):
                dept = timetable_parser.extract_department_from_context(
                    page.headers, page.text
                )
                chunk_text = timetable_parser.timetable_table_to_text(raw, dept)
                meta = timetable_parser.extract_timetable_meta(raw, dept)
                if not chunk_text.strip():
                    chunk_text = f"[수업시간표]\n{table_md}"  # 파싱 실패 시 fallback
            else:
                chunk_text = f"[표]\n{table_md}"
                meta = {"content_type": "table"}

            chunk_id = _make_id(source_file, page.page_number, f"table_{t_idx}", table_md)
            c_from, c_to = detect_cohort(table_md)
            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=chunk_text,
                page_number=page.page_number,
                source_file=source_file,
                student_id=student_id,
                doc_type=doc_type,
                cohort_from=c_from,
                cohort_to=c_to,
                semester=semester,
                metadata=meta,
            ))

        # 2. 텍스트 청크 (슬라이딩 윈도우)
        # 수업시간표는 모든 정보가 표에 있으므로 텍스트 청크 불필요
        if doc_type == "timetable":
            continue
        if page.text:
            text_chunks = _sliding_window(page.text)
            for i, text in enumerate(text_chunks):
                if len(text.strip()) < MIN_CHUNK_LEN:
                    continue
                chunk_id = _make_id(source_file, page.page_number, f"text_{i}", text)
                c_from, c_to = detect_cohort(text)
                chunks.append(Chunk(
                    chunk_id=chunk_id,
                    text=text,
                    page_number=page.page_number,
                    source_file=source_file,
                    student_id=student_id,
                    doc_type=doc_type,
                    cohort_from=c_from,
                    cohort_to=c_to,
                    semester=semester,
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


def _save_json(
    path: Path,
    pdf_type: PDFType,
    pages: List[PageContent],
    chunks: List[Chunk],
    doc_type: str,
    semester: str,
) -> Path:
    """
    추출 결과와 청크를 JSON 파일로 저장합니다.

    저장 경로: data/extracted/{pdf_stem}.json
    (같은 파일을 재인제스트하면 덮어씁니다)
    """
    out_dir = Path(settings.pdf.pdf_dir).parent / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}.json"

    def _safe(v):
        """JSON 직렬화 불가 타입을 문자열로 변환합니다."""
        if isinstance(v, Path):
            return str(v)
        return v

    payload = {
        "source_file": str(path),
        "pdf_type":    pdf_type.value,
        "doc_type":    doc_type,
        "semester":    semester,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "total_pages": len(pages),
        "pages": [
            {
                "page_number": p.page_number,
                "text":        p.text,
                "headers":     p.headers,
                "tables": [
                    {
                        "markdown": md,
                        "raw":      raw,
                    }
                    for md, raw in zip(p.tables, p.raw_tables or [None] * len(p.tables))
                ],
            }
            for p in pages
        ],
        "chunks": [
            {
                "chunk_id":   c.chunk_id,
                "text":       c.text,
                "page_number": c.page_number,
                "doc_type":   c.doc_type,
                "semester":   c.semester,
                "cohort_from": c.cohort_from,
                "cohort_to":  c.cohort_to,
                "metadata":   c.metadata,
            }
            for c in chunks
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_safe)

    logger.info(f"JSON 저장 완료: {out_path} ({len(pages)}페이지, {len(chunks)}청크)")
    return out_path


def ingest_pdf(
    pdf_path: str,
    student_id: str,
    doc_type: str,
    semester: str = "",
    save_json: bool = False,
) -> int:
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
    chunks = make_chunks(pages, student_id=student_id, doc_type=doc_type, semester=semester)

    if not chunks:
        logger.warning("생성된 청크가 없습니다.")
        return 0

    # 4. (선택) JSON 저장
    if save_json:
        _save_json(path, pdf_type, pages, chunks, doc_type, semester)

    # 5. 임베딩 + ChromaDB 저장
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
        choices=["domestic", "foreign", "transfer", "schedule", "timetable"],
        help=(
            "문서 유형 "
            "(domestic=내국인 학사안내, foreign=외국인 안내, "
            "transfer=편입생 안내, schedule=학사일정, timetable=수업시간표)"
        ),
    )
    parser.add_argument(
        "--semester", default="",
        help="학기 식별자 (예: 2026-1, 2025-2). 학사안내 등 전 학기 공통 문서는 생략",
    )
    parser.add_argument(
        "--save-json", action="store_true",
        help="추출 결과를 data/extracted/{파일명}.json 으로 저장 (디버깅/재사용용)",
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
        count = ingest_pdf(
            str(pdf_path),
            args.student_id,
            args.doc_type,
            args.semester,
            save_json=args.save_json,
        )
        total += count

    print(f"\n완료: 총 {total:,}개 청크가 ChromaDB에 저장되었습니다.")
    show_status()


if __name__ == "__main__":
    main()
