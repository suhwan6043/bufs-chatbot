"""
청킹 공통 유틸리티 - PDF/웹 콘텐츠 공통 텍스트 분할 로직

원래 scripts/ingest_pdf.py에 있던 함수들을 여기로 이동하여
웹 크롤링 인제스트에서도 재사용할 수 있게 합니다.
"""

import hashlib
import re
from pathlib import Path
from typing import List, Optional, Tuple

# ── 청킹 설정 ──────────────────────────────────────────────────
CHUNK_SIZE = 500       # 청크당 최대 글자 수 (한국어 기준 ~250 토큰)
CHUNK_OVERLAP = 80     # 청크 간 겹침 글자 수 (문맥 연속성)
MIN_CHUNK_LEN = 50     # 이 이하 청크는 버림

# ── 학번 범위 감지 ─────────────────────────────────────────────
_COHORT_MIN = 2016
_COHORT_MAX = 2030


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


def make_chunk_id(source_file: str, page_num: int, suffix: str, text: str) -> str:
    """중복 방지용 청크 ID를 생성합니다."""
    content = f"{source_file}:{page_num}:{suffix}:{text[:50]}"
    return hashlib.md5(content.encode()).hexdigest()


def sliding_window(text: str) -> List[str]:
    """
    텍스트를 CHUNK_SIZE 글자씩, CHUNK_OVERLAP 겹침으로 분할합니다.
    줄바꿈 기준으로 문단 경계를 최대한 보존합니다.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        candidate = (current + "\n\n" + para).strip() if current else para
        if len(candidate) <= CHUNK_SIZE:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(para) > CHUNK_SIZE:
                sub_chunks = force_split(para)
                chunks.extend(sub_chunks[:-1])
                current = sub_chunks[-1] if sub_chunks else ""
            else:
                overlap_text = chunks[-1][-CHUNK_OVERLAP:] if chunks else ""
                current = (overlap_text + "\n\n" + para).strip() if overlap_text else para

    if current:
        chunks.append(current)

    return chunks


def force_split(text: str) -> List[str]:
    """CHUNK_SIZE보다 큰 텍스트를 강제 분할합니다."""
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


def pages_to_chunks(
    pages,                          # List[PageContent]
    source_file: str,
    doc_type: str = "notice_attachment",
    semester: str = "",
    student_id: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> list:
    """
    List[PageContent] → List[Chunk] 변환 헬퍼.

    PDF 첨부파일 인제스트용으로 IncrementalUpdater에서 사용합니다.
    ingest_pdf.py 의 make_chunks()와 동일한 로직을 공통화한 버전입니다.

    Args:
        pages:       DigitalPDFExtractor.extract() 결과
        source_file: PDF 경로 문자열 (ChromaDB delete_by_source 키)
        doc_type:    문서 유형 (기본 "notice_attachment")
        semester:    학기 레이블 (기본 "")
        student_id:  학번 (None = 모든 학번 공통)
        extra_metadata: ChromaDB 메타데이터에 추가할 키-값
    """
    # 지연 import (순환 참조 방지)
    from app.models import Chunk

    extra_metadata = extra_metadata or {}
    chunks: list[Chunk] = []

    for page in pages:
        page_num = page.page_number

        # ── 텍스트 청크 ──────────────────────────────────
        if page.text and page.text.strip():
            for idx, text in enumerate(sliding_window(page.text)):
                if len(text.strip()) < MIN_CHUNK_LEN:
                    continue
                cohort_from, cohort_to = detect_cohort(text)
                chunks.append(Chunk(
                    chunk_id=make_chunk_id(source_file, page_num, f"text_{idx}", text),
                    text=text,
                    page_number=page_num,
                    source_file=source_file,
                    student_id=student_id,
                    doc_type=doc_type,
                    cohort_from=cohort_from,
                    cohort_to=cohort_to,
                    semester=semester,
                    metadata={
                        "source_file": source_file,
                        "page_number": page_num,
                        "doc_type": doc_type,
                        **extra_metadata,
                    },
                ))

        # ── 테이블 청크 ──────────────────────────────────
        for tidx, table_md in enumerate(page.tables or []):
            if not table_md or len(table_md.strip()) < MIN_CHUNK_LEN:
                continue
            cohort_from, cohort_to = detect_cohort(table_md)
            chunks.append(Chunk(
                chunk_id=make_chunk_id(source_file, page_num, f"table_{tidx}", table_md),
                text=table_md,
                page_number=page_num,
                source_file=source_file,
                student_id=student_id,
                doc_type=doc_type,
                cohort_from=cohort_from,
                cohort_to=cohort_to,
                semester=semester,
                metadata={
                    "source_file": source_file,
                    "page_number": page_num,
                    "doc_type": doc_type,
                    "is_table": True,
                    **extra_metadata,
                },
            ))

    return chunks
