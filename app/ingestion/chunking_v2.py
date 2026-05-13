"""
재설계된 청킹 파이프라인 v2.

원칙:
1. 헤더 경계에서 청크 강제 분할 (섹션 가로지름 금지)
2. 같은 섹션 내에서만 sliding window (overlap 100자, 자연 경계 우선)
3. 모든 청크에 필수 메타 강제 주입 (검증 게이트)
4. 표는 VLM 폴백 가능 시 마크다운 표로 별도 청크
5. chunk_id = {file_sha[:8]}_{page:03d}_{pos:02d}_{text_sha[:8]} → 충돌 0%
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import fitz
import pdfplumber
from PIL import Image

from app.pdf.section_stack import (
    SectionStack, classify_header, derive_header_levels
)
from app.pdf.vlm_extractor import (
    extract_table, needs_table_fallback, needs_page_fallback, is_real_table,
)
from app.pdf.page_router import classify_pdf, PageClassification

logger = logging.getLogger(__name__)

# ── 파라미터 (env로 노출 가능) ──────────────────────────────────────────────
import os
CHUNK_MIN_LEN = int(os.getenv("CHUNK_MIN_LEN", "150"))
CHUNK_MAX_LEN = int(os.getenv("CHUNK_MAX_LEN", "700"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))
CHUNK_HARD_CAP = int(os.getenv("CHUNK_HARD_CAP", "1200"))
TABLE_MAX_LEN = int(os.getenv("TABLE_MAX_LEN", "2500"))

# 깨짐 문자 패턴 — 명시 코드포인트로 작성 (range 오해석 방지)
# PUA: U+E000~F8FF
# CJK Ext-A: U+3400~4DBF
# IPA Extensions: U+0250~02AF
# Combining Diacritical Marks: U+0300~036F
# Spacing Modifier Letters: U+02B0~02FF
# C0 Control (except \n=0x0A, \t=0x09, \r=0x0D): \x00~\x08, \x0b, \x0c, \x0e~\x1f, \x7f
_GARBAGE_RE = re.compile(
    "[㐀-䶿"  # CJK Ext-A
    "-"   # PUA
    "ɐ-ʯ"   # IPA Extensions
    "̀-ͯ"   # Combining Marks
    "ʰ-˿"   # Spacing Modifiers
    "\x00-\x08\x0B\x0C\x0E-\x1F\x7F"  # 제어 (LF/HT/CR 제외)
    "]"
)
# 마크다운 정상 문자 (표 청크 노이즈 계산 시 제외) — `|`, `-`, 공백
_MD_NEUTRAL_RE = re.compile(r"[|\-\s]")

# Surya OCR 0.17 마크업 — 한정된 영문 태그만 매칭해서 한국어 꺾쇠 본문 보존
# (예: <대학일자리플러스센터>, <만오교양대학> 같은 텍스트는 손대지 않음)
_OCR_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_OCR_INLINE_TAG_RE = re.compile(
    r"</?(?:b|i|u|em|strong|sub|sup|small|big|s|mark|del|ins)\s*/?>",
    re.IGNORECASE,
)


def strip_ocr_markup(text: str) -> str:
    """Surya OCR 출력의 인라인 마크업 제거.

    - <br>, <br/> → 줄바꿈
    - <b>·<i>·<em>·<strong>·<u>·<sub>·<sup> 등 → 제거 (내용은 보존)
    - 한국어 꺾쇠(예: <AI융합교육센터>)는 영문 태그명 화이트리스트로 보호
    """
    if not text:
        return text
    text = _OCR_BR_RE.sub("\n", text)
    text = _OCR_INLINE_TAG_RE.sub("", text)
    return text


# ── 데이터 클래스 ───────────────────────────────────────────────────────────
@dataclass
class ChunkV2:
    """신규 청크 객체."""
    chunk_id: str
    text: str
    metadata: dict = field(default_factory=dict)


@dataclass
class PageBlock:
    """페이지 내 단일 블록 (헤더 또는 본문 라인)."""
    text: str
    font_size: float
    bbox: tuple  # (x0, y0, x1, y1)
    is_table: bool = False
    table_md: Optional[str] = None  # 표일 때 마크다운


# ── 헬퍼 ────────────────────────────────────────────────────────────────────
def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def text_sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def garbage_ratio(text: str, is_table: bool = False) -> float:
    """깨짐 문자 비율. 표는 마크다운 구분자(|, -, 공백) 제외하여 계산."""
    if not text:
        return 0.0
    bad = len(_GARBAGE_RE.findall(text))
    if is_table:
        # 분모를 마크다운 중립 문자 제외한 의미 있는 글자 수로
        denom = len(text) - len(_MD_NEUTRAL_RE.findall(text))
        return bad / max(denom, 1)
    return bad / len(text)


def detect_cohort(text: str) -> tuple[int, int]:
    """학번 범위 추출. 폴백 (2016, 2030).

    개선: '이후/이전/부터' 패턴 추가로 폴백률 감소.
    """
    text = text or ""
    # 명시적 학번
    explicit = re.findall(r"(20\d{2})\s*학번", text)
    if explicit:
        years = sorted(set(int(y) for y in explicit))
        return (years[0], years[-1])

    # "2024학년도부터", "2024학번 이후"
    m_after = re.search(r"(20\d{2})\s*(?:학년도부터|학번\s*이후|이후|학년도\s*이후)", text)
    if m_after:
        y = int(m_after.group(1))
        return (y, 2030)

    m_before = re.search(r"(20\d{2})\s*(?:학번\s*이전|학번까지|이전)", text)
    if m_before:
        y = int(m_before.group(1))
        return (2016, y)

    # 범위
    m_range = re.search(r"(20\d{2})\s*[~\-]\s*(20\d{2})", text)
    if m_range:
        return (int(m_range.group(1)), int(m_range.group(2)))

    return (2016, 2030)


def replace_pua(text: str) -> str:
    """PUA·CJK Ext-A·IPA·Combining Marks·제어문자 등 깨진 문자 정리.

    - PUA (\\uf000~\\uf8ff): 폰트 글리프 (불릿·체크박스) → ■
    - CJK Extension A (\\u3400~\\u4dbf): HWP 추출 깨짐 → 공백
    - IPA 확장 (\\u0250~\\u02af): pyhwp 깨짐 → 공백
    - Combining Diacritical Marks (\\u0300~\\u036f) → 제거
    - Spacing Modifier Letters (\\u02b0~\\u02ff) → 제거
    - C0 제어 문자 (\\x00~\\x1f, \\x7f): PDF 형식 코드 → 공백 (\\n, \\t는 보존)
    """
    # PUA → ■
    text = re.sub("[-]", "■", text)
    # CJK Ext-A, IPA Extensions → 공백
    text = re.sub("[㐀-䶿ɐ-ʯ]", " ", text)
    # Combining Marks → 제거
    text = re.sub("[̀-ͯ]", "", text)
    # Spacing Modifiers → 제거
    text = re.sub("[ʰ-˿]", "", text)
    # C0 제어 문자 → 공백 (\n, \t, \r 보존)
    text = re.sub("[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", " ", text)
    # 연속 공백 정리
    text = re.sub(r" {3,}", "  ", text)
    return text


# ── 자연 경계 분할 (sliding window) ─────────────────────────────────────────
def find_natural_boundary(text: str, search_start: int, search_end: int) -> int:
    """단락(\\n\\n) → 문장(다.까./.?!) → 절(,) 순으로 boundary 탐색."""
    if search_end <= search_start:
        return search_end
    region = text[search_start:search_end]
    # 1) 단락
    pos = region.rfind("\n\n")
    if pos >= 0:
        return search_start + pos + 2
    # 2) 한국어 종결("다.", "까.")
    for marker in ("다.", "까.", ".\n", "?\n", "!\n"):
        pos = region.rfind(marker)
        if pos >= 0:
            return search_start + pos + len(marker)
    # 3) 문장 종료
    for marker in (". ", "? ", "! "):
        pos = region.rfind(marker)
        if pos >= 0:
            return search_start + pos + len(marker)
    # 4) 절
    pos = region.rfind(", ")
    if pos >= 0:
        return search_start + pos + 2
    # 5) 마지막 줄바꿈
    pos = region.rfind("\n")
    if pos >= 0:
        return search_start + pos + 1
    return search_end


def sliding_chunks(
    text: str, max_len: int = CHUNK_MAX_LEN, overlap: int = CHUNK_OVERLAP,
    min_len: int = CHUNK_MIN_LEN,
) -> list[str]:
    """자연 경계 우선 슬라이딩 윈도우."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text] if len(text) >= min_len else []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_len, len(text))
        if end < len(text):
            # 80% 지점부터 자연 경계 탐색
            search_start = start + int(max_len * 0.8)
            end = find_natural_boundary(text, search_start, end)
        piece = text[start:end].strip()
        if len(piece) >= min_len:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


# ── 페이지 처리 ─────────────────────────────────────────────────────────────
def collect_page_blocks(
    page: fitz.Page, font_levels: dict[float, int],
) -> list[PageBlock]:
    """페이지를 헤더/본문 블록으로 분해. y 좌표 위→아래 순."""
    page_dict = page.get_text("dict")
    blocks = []

    for block in page_dict.get("blocks", []):
        if "lines" not in block:
            continue
        for line in block["lines"]:
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue
            max_size = max(s["size"] for s in spans)
            bbox = line.get("bbox", (0, 0, 0, 0))
            blocks.append(PageBlock(
                text=text, font_size=max_size, bbox=bbox,
            ))

    # y 좌표 순 (위→아래)
    blocks.sort(key=lambda b: b.bbox[1])
    return blocks


def extract_tables_with_fallback(
    pdf_path: str, page_idx: int, page: fitz.Page,
) -> list[tuple[tuple, str]]:
    """페이지 표 추출 (사전 진짜표 필터 → pdfplumber → VLM 폴백)."""
    results = []
    page_size = (page.rect.width, page.rect.height)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pp_page = pdf.pages[page_idx]
            tables = pp_page.find_tables()
            for table in tables:
                bbox = table.bbox
                extracted = table.extract()

                # 1) 사전 검증: 진짜 표 아니면 스킵 (가짜 표 → VLM 호출 절약)
                is_real, real_reason = is_real_table(extracted, bbox, page_size)
                if not is_real:
                    logger.debug("표 스킵 (page %d): %s", page_idx + 1, real_reason)
                    continue

                # 2) 추출 품질 검증
                needs, reason = needs_table_fallback(extracted)
                if not needs:
                    md = pdfplumber_to_markdown(extracted)
                    results.append((bbox, md))
                    continue

                # 3) VLM 폴백
                logger.info("표 VLM 폴백 (page %d): %s", page_idx + 1, reason)
                mat = fitz.Matrix(2, 2)
                pix = page.get_pixmap(matrix=mat, clip=fitz.Rect(*bbox))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                vlm_result = extract_table(
                    img,
                    expected_text_density=sum(len(str(c) or "") for r in extracted for c in r),
                )
                if vlm_result.valid and vlm_result.text != "NOT_A_TABLE":
                    results.append((bbox, vlm_result.text))
                else:
                    md = pdfplumber_to_markdown(extracted)
                    results.append((bbox, md))
    except Exception as e:
        logger.warning("표 추출 실패 (page %d): %s", page_idx + 1, e)
    return results


def split_table_by_size(md: str, max_len: int = TABLE_MAX_LEN) -> list[str]:
    """긴 마크다운 표를 글자 수 기준으로 분할 (헤더는 모든 청크에 유지)."""
    if len(md) <= max_len:
        return [md]
    rows = md.split("\n")
    if len(rows) < 3:
        return [md]
    header = rows[:2]
    body = rows[2:]
    chunks = []
    cur_rows = []
    cur_len = sum(len(r) + 1 for r in header)
    for r in body:
        if cur_len + len(r) + 1 > max_len and cur_rows:
            chunks.append("\n".join(header + cur_rows))
            cur_rows = [r]
            cur_len = sum(len(x) + 1 for x in header) + len(r) + 1
        else:
            cur_rows.append(r)
            cur_len += len(r) + 1
    if cur_rows:
        chunks.append("\n".join(header + cur_rows))
    return chunks


def pdfplumber_to_markdown(extracted: list[list[str]]) -> str:
    """pdfplumber extract 결과 → 마크다운 표."""
    if not extracted:
        return ""
    rows = []
    max_cols = max(len(r) for r in extracted)
    for row in extracted:
        cells = [str(c).strip().replace("\n", " ") if c else "" for c in row]
        # 부족한 열 채우기
        cells += [""] * (max_cols - len(cells))
        rows.append("| " + " | ".join(cells) + " |")
    if rows:
        sep = "| " + " | ".join(["---"] * max_cols) + " |"
        rows.insert(1, sep)
    return "\n".join(rows)


# ── 청크 생성 메인 ──────────────────────────────────────────────────────────
class ChunkBuilder:
    """헤더 경계 기준 청크 누적기.

    flush 시 MIN_CHUNK_LEN 미만이면 carry-over 모드로 다음 섹션 prepend 가능.
    """

    def __init__(self, section_stack: SectionStack):
        self.stack = section_stack
        self._buffer: list[str] = []
        self._carryover: str = ""  # 직전 flush에서 짧아 거부된 본문

    def append_line(self, text: str) -> None:
        self._buffer.append(text)

    def prepend_carryover(self) -> None:
        """저장된 carryover를 새 버퍼 앞에 추가 (헤더 push 직후 호출)."""
        if self._carryover:
            self._buffer.insert(0, self._carryover)
            self._carryover = ""

    @property
    def length(self) -> int:
        return sum(len(s) + 1 for s in self._buffer)

    @property
    def text(self) -> str:
        return "\n".join(self._buffer).strip()

    def flush(self, force_min: bool = False, allow_carryover: bool = True) -> list[str]:
        """현 버퍼 → 청크. 짧으면 carry-over 또는 거부."""
        if not self._buffer:
            return []
        text = self.text
        if not force_min and len(text) < CHUNK_MIN_LEN:
            if allow_carryover:
                # 다음 섹션에 prepend 위해 보관
                self._carryover = (self._carryover + "\n" + text).strip() if self._carryover else text
            self._buffer.clear()
            return []
        # carryover 합치기
        if self._carryover:
            text = (self._carryover + "\n" + text).strip()
            self._carryover = ""
        chunks = sliding_chunks(text)
        self._buffer.clear()
        return chunks


def chunks_from_pdf(
    pdf_path: str,
    *,
    doc_type: str = "domestic",
    enable_vlm: bool = True,
    enable_ocr: bool = True,
) -> Iterator[ChunkV2]:
    """PDF → ChunkV2 스트림 생성기.

    페이지별 라우팅:
        - 디지털: PyMuPDF + pdfplumber (+ VLM 표 폴백)
        - 스캔  : Surya OCR (enable_ocr=True 시)
    """
    pdf_path = str(pdf_path)
    file_sha = file_sha256(pdf_path)
    file_sha8 = file_sha[:8]

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count

    # 1. 페이지 라우팅 분류
    classifications = classify_pdf(pdf_path)
    n_scan = sum(1 for c in classifications if c.is_scan)
    n_digital = sum(1 for c in classifications if not c.is_scan)
    logger.info(
        "[%s] 페이지 분류: 디지털 %d, 스캔 %d (총 %d)",
        Path(pdf_path).name, n_digital, n_scan, total_pages,
    )

    # 2. 스캔 페이지에 대해서만 Surya OCR 호출 (디지털 페이지는 디지털 추출 유지)
    ocr_text_per_page: dict[int, str] = {}
    if n_scan > 0 and enable_ocr:
        try:
            from app.pdf.ocr_extractor import SuryaOCRExtractor
            scan_page_idx = [c.page_index for c in classifications if c.is_scan]
            logger.info(
                "Surya OCR 호출 — 스캔 페이지 %d개만 (디지털 %d 페이지는 디지털 추출 유지)",
                len(scan_page_idx), n_digital,
            )
            extractor = SuryaOCRExtractor()
            ocr_pages = extractor.extract(pdf_path, page_indices=scan_page_idx)
            for p in ocr_pages:
                pn = getattr(p, "page_number", None)
                txt = getattr(p, "text", "") or ""
                if pn is not None:
                    ocr_text_per_page[pn] = strip_ocr_markup(txt)
            logger.info("Surya OCR 완료: %d페이지 텍스트 수신", len(ocr_text_per_page))
        except Exception as e:
            logger.warning("Surya OCR 실패: %s — 스캔 페이지 건너뜀", e)

    # 3. 디지털 페이지의 폰트 분포만으로 헤더 level 매핑
    all_sizes: list[float] = []
    for cls in classifications:
        if cls.is_scan:
            continue
        page = doc[cls.page_index]
        for block in page.get_text("dict").get("blocks", []):
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line.get("spans", []):
                    all_sizes.append(span["size"])
    font_levels = derive_header_levels(all_sizes)
    logger.info(
        "[%s] 폰트 분석(디지털): 총 %d span, 헤더 매핑 %s",
        Path(pdf_path).name, len(all_sizes), font_levels,
    )

    section_stack = SectionStack()

    for page_idx in range(total_pages):
        page = doc[page_idx]
        page_num = page_idx + 1
        cls = classifications[page_idx]

        # 스캔 페이지 → OCR 텍스트로 처리
        if cls.is_scan:
            ocr_text = ocr_text_per_page.get(page_num, "")
            if not ocr_text.strip():
                logger.debug("스캔 페이지 %d: OCR 결과 없음 — 스킵", page_num)
                continue

            chunk_builder = ChunkBuilder(section_stack)
            position = 0
            # OCR 텍스트를 라인 단위로 처리 (font_size 정보 없음 → 패턴 매칭만)
            for line_text in ocr_text.split("\n"):
                line_text = line_text.strip()
                if not line_text:
                    continue
                # 헤더 패턴만으로 분류 (font_size=10.0 가정 — body로 fallback)
                from app.pdf.section_stack import detect_pattern_level
                pat_level = detect_pattern_level(line_text)
                # OCR엔 폰트 정보 없으므로 명확한 패턴(L1, L2)만 헤더로 인정
                if pat_level is not None and pat_level <= 2 and 2 <= len(line_text) <= 60:
                    for chunk_text in chunk_builder.flush(allow_carryover=True):
                        yield _build_chunk(
                            chunk_text, file_sha8, page_num, position,
                            section_stack, doc_type, total_pages, "ocr_surya",
                        )
                        position += 1
                    section_stack.push(pat_level, line_text)
                    chunk_builder.prepend_carryover()
                else:
                    chunk_builder.append_line(replace_pua(line_text))
                    if chunk_builder.length >= CHUNK_HARD_CAP:
                        for chunk_text in chunk_builder.flush(allow_carryover=False):
                            yield _build_chunk(
                                chunk_text, file_sha8, page_num, position,
                                section_stack, doc_type, total_pages, "ocr_surya",
                            )
                            position += 1
            chunk_builder.prepend_carryover()
            for chunk_text in chunk_builder.flush(force_min=True, allow_carryover=False):
                yield _build_chunk(
                    chunk_text, file_sha8, page_num, position,
                    section_stack, doc_type, total_pages, "ocr_surya",
                )
                position += 1
            continue  # 다음 페이지로

        # 디지털 페이지 — 기존 경로
        # 페이지 블록 수집
        blocks = collect_page_blocks(page, font_levels)
        # 페이지 표 추출 (VLM 폴백 포함)
        tables = extract_tables_with_fallback(pdf_path, page_idx, page) if enable_vlm else []

        # 표 영역과 겹치는 본문 블록은 제거 (중복 방지)
        non_table_blocks = []
        for b in blocks:
            in_table = False
            for tbox, _ in tables:
                if (b.bbox[1] >= tbox[1] - 5 and b.bbox[3] <= tbox[3] + 5):
                    in_table = True
                    break
            if not in_table:
                non_table_blocks.append(b)

        chunk_builder = ChunkBuilder(section_stack)
        position = 0  # 페이지 내 청크 인덱스

        for block in non_table_blocks:
            level = classify_header(block.text, block.font_size, font_levels)
            if level is not None:
                # 1) 현 청크 마감 (짧으면 carryover로 다음 섹션에 prepend)
                for chunk_text in chunk_builder.flush(allow_carryover=True):
                    yield _build_chunk(
                        chunk_text, file_sha8, page_num, position,
                        section_stack, doc_type, total_pages, "digital",
                    )
                    position += 1
                # 2) stack 갱신 (헤더 자체는 본문 미포함)
                section_stack.push(level, block.text)
                # 3) carryover 있으면 새 섹션 본문에 prepend
                chunk_builder.prepend_carryover()
            else:
                cleaned = replace_pua(block.text)
                chunk_builder.append_line(cleaned)
                # 청크가 너무 길어지면 즉시 flush (같은 섹션 내 sliding)
                if chunk_builder.length >= CHUNK_HARD_CAP:
                    for chunk_text in chunk_builder.flush(allow_carryover=False):
                        yield _build_chunk(
                            chunk_text, file_sha8, page_num, position,
                            section_stack, doc_type, total_pages, "digital",
                        )
                        position += 1

        # 페이지 끝 — 본문 잔여 마감 (carryover도 함께 force)
        chunk_builder.prepend_carryover()  # 마지막 carryover도 합쳐서 마감
        for chunk_text in chunk_builder.flush(force_min=True, allow_carryover=False):
            yield _build_chunk(
                chunk_text, file_sha8, page_num, position,
                section_stack, doc_type, total_pages, "digital",
            )
            position += 1

        # 표 청크들 (글자 수 기반 분할)
        for tbox, md in tables:
            for piece in split_table_by_size(md, max_len=TABLE_MAX_LEN):
                yield _build_chunk(
                    piece, file_sha8, page_num, position,
                    section_stack, doc_type, total_pages, "vlm_table",
                    is_table=True,
                )
                position += 1

    doc.close()


def _build_chunk(
    text: str, file_sha8: str, page: int, position: int,
    section_stack: SectionStack, doc_type: str, page_total: int,
    extraction_method: str, is_table: bool = False,
) -> ChunkV2:
    """검증 게이트 통과한 청크만 반환 (이름은 그대로, 거부 케이스는 None X — 호출부에서 valid 체크)."""
    text_sha8 = text_sha256(text)[:8]
    chunk_id = f"{file_sha8}_{page:03d}_{position:02d}_{text_sha8}"

    cohort_from, cohort_to = detect_cohort(text)
    cohort_inferred = (cohort_from, cohort_to) == (2016, 2030)

    metadata = {
        "source_hash": file_sha8,
        "doc_type": doc_type,
        "page_number": page,
        "page_total": page_total,
        "section_path": section_stack.path,
        "section_titles": "|".join(section_stack.titles),  # ChromaDB flat 저장
        "section_depth": section_stack.depth,
        "cohort_from": cohort_from,
        "cohort_to": cohort_to,
        "cohort_inferred": cohort_inferred,
        "extraction_method": extraction_method,
        "chunk_position": position,
        "is_table": is_table,
        "garbage_ratio": round(garbage_ratio(text, is_table=is_table), 3),
        "text_len": len(text),
    }
    return ChunkV2(chunk_id=chunk_id, text=text, metadata=metadata)


# ── 검증 게이트 (인제스트 전 필수 호출) ─────────────────────────────────────
REQUIRED_META = (
    "source_hash", "doc_type", "page_number", "page_total",
    "section_path", "section_depth",
    "cohort_from", "cohort_to", "extraction_method", "chunk_position",
)


def validate_chunk(
    chunk: ChunkV2,
    max_garbage_ratio: float = 0.20,
    max_garbage_ratio_table: float = 0.40,
    min_len_table: int = 80,
) -> tuple[bool, str]:
    """인제스트 직전 검증. 표 청크는 길이·깨짐 모두 완화."""
    # 필수 메타
    for k in REQUIRED_META:
        if k not in chunk.metadata or chunk.metadata[k] is None:
            return False, f"필수 메타 누락: {k}"
    n = len(chunk.text)
    is_table = chunk.metadata.get("is_table", False)
    # 길이 검증 (표 청크는 짧아도 의미 있음)
    min_len = min_len_table if is_table else CHUNK_MIN_LEN
    if n < min_len:
        return False, f"길이 부족 ({n} < {min_len})"
    upper_cap = TABLE_MAX_LEN if is_table else CHUNK_HARD_CAP
    if n > upper_cap:
        return False, f"길이 초과 ({n} > {upper_cap})"
    # 깨짐 비율 (표 청크는 마크다운 구분자 제외 + 임계 완화)
    gr = chunk.metadata["garbage_ratio"]
    threshold = max_garbage_ratio_table if is_table else max_garbage_ratio
    if gr > threshold:
        return False, f"깨짐 {gr*100:.0f}% > {threshold*100:.0f}%"
    return True, "OK"
