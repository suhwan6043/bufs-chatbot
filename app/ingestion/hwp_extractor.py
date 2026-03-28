"""
HWP/HWPX 추출기 - 한글 문서에서 텍스트를 추출합니다.

지원 형식:
  .hwpx  ZIP 기반 XML 형식 (HWP Open Format 2.0)
         → zipfile + ElementTree으로 파싱, 추가 라이브러리 불필요
  .hwp   OLE2 복합 문서 형식 (HWP 5.x)
         → olefile 필요 (pip install olefile)
         → BodyText/Section* 스트림을 zlib 압축 해제 후 파싱

HWP5 바이너리 본문 파싱 원리:
  1. BodyText/Section0 스트림 = zlib 압축된 레코드 스트림
  2. 각 레코드는 (tagId, level, size) 헤더 + 데이터
  3. HWPTAG_PARA_TEXT (tagId=67) 레코드에 UTF-16LE 텍스트가 담김
  4. 특수 문자 0x0002, 0x0003, 0x0004 등은 인라인 오브젝트(스킵)
"""

import logging
import re
import struct
import zlib
from pathlib import Path
from typing import List, Optional

from app.models import PageContent

logger = logging.getLogger(__name__)

# HWP5 레코드 태그 ID
_TAG_PARA_TEXT = 67          # HWPTAG_PARA_TEXT
_TAG_PARA_HEADER = 66        # HWPTAG_PARA_HEADER (단락 시작 구분자)

# 짧은 청크는 무시
_MIN_TEXT_LEN = 5


# ══════════════════════════════════════════════════════════════
#  HWPX 추출 (ZIP + XML)
# ══════════════════════════════════════════════════════════════

def _extract_hwpx(path: str) -> List[PageContent]:
    """
    HWPX(ZIP) 파일에서 본문 텍스트를 추출합니다.

    HWPX 내부 구조 (HWP Open Format 2.0):
      Contents/section0.xml  ... section{n}.xml  (본문 섹션)
      또는
      BodyText/Section0.xml  ... Section{n}.xml
    """
    import zipfile
    import xml.etree.ElementTree as ET

    pages: List[PageContent] = []
    filename = Path(path).name

    try:
        with zipfile.ZipFile(path, "r") as zf:
            # 섹션 파일 목록 수집
            section_files = sorted(
                [
                    n for n in zf.namelist()
                    if re.match(
                        r'(?i)(contents|bodytext)/section\d+\.xml', n
                    )
                ],
                key=lambda s: int(re.search(r'\d+', s.split("/")[-1]).group()),
            )

            if not section_files:
                logger.warning("HWPX 섹션 파일 없음: %s", filename)
                return []

            for sec_idx, sec_name in enumerate(section_files):
                try:
                    xml_data = zf.read(sec_name)
                    text = _parse_hwpx_section(xml_data)
                    if text.strip():
                        pages.append(PageContent(
                            page_number=sec_idx + 1,
                            text=text,
                            tables=[],
                            source_file=str(path),
                        ))
                except Exception as e:
                    logger.debug("HWPX 섹션 파싱 오류 [%s]: %s", sec_name, e)

    except Exception as e:
        logger.error("HWPX 열기 실패 [%s]: %s", filename, e)

    logger.info("HWPX 추출 완료: %s → %d섹션", filename, len(pages))
    return pages


def _parse_hwpx_section(xml_data: bytes) -> str:
    """
    HWPX 섹션 XML에서 텍스트를 추출합니다.

    네임스페이스:
      urn:schemas-microsoft-com:office:hwpml:2005:hwp  (구버전)
      http://www.hancom.co.kr/hwpml/2012/paragraph      (신버전)

    텍스트 태그: <hp:t>, <t>, <hh:t> 등 로컬명 't'
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        # BOM 제거 후 재시도
        try:
            root = ET.fromstring(xml_data.lstrip(b'\xef\xbb\xbf'))
        except Exception:
            logger.debug("XML 파싱 실패: %s", e)
            return ""

    text_parts: List[str] = []
    for elem in root.iter():
        # 로컬 태그명이 't' 인 요소의 텍스트
        local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if local == "t" and elem.text:
            text_parts.append(elem.text)
        # 단락 구분 (<p> or <hp:p> 등)
        elif local == "p" and text_parts:
            text_parts.append("\n")

    text = "".join(text_parts)
    text = "".join(ch for ch in text if ch >= " " or ch in "\n\t")
    # HWP 폼 필드 마커가 CJK 코드포인트로 오파싱된 잔여물 제거
    # 패턴: 빈 줄로 둘러싸인 1~6자 CJK 문자열 (한국어 문서에서 단독 출현)
    text = re.sub(r'\n{1,2}[\u4E00-\u9FFF]{1,6}\n{1,2}', '\n', text)
    return text


# ══════════════════════════════════════════════════════════════
#  HWP5 추출 (OLE2 + zlib + 이진 파싱)
# ══════════════════════════════════════════════════════════════

def _extract_hwp5(path: str) -> List[PageContent]:
    """
    HWP5(OLE2) 파일에서 본문 텍스트를 추출합니다.

    요구사항: olefile (pip install olefile)
    """
    try:
        import olefile
    except ImportError:
        logger.warning(
            "olefile 미설치: HWP5 파싱 불가 [%s]. pip install olefile",
            Path(path).name,
        )
        return []

    filename = Path(path).name
    pages: List[PageContent] = []

    try:
        with olefile.OleFileIO(path) as ole:
            # BodyText/Section0, Section1, ... 스트림 탐색
            section_entries = sorted(
                [
                    e for e in ole.listdir()
                    if len(e) == 2
                    and e[0].lower() == "bodytext"
                    and re.match(r'section\d+', e[1].lower())
                ],
                key=lambda e: int(re.search(r'\d+', e[1]).group()),
            )

            if not section_entries:
                logger.warning("HWP5 BodyText 섹션 없음: %s", filename)
                return []

            # FileHeader에서 압축 여부 확인
            is_compressed = _hwp5_is_compressed(ole)

            for sec_idx, entry in enumerate(section_entries):
                try:
                    raw = ole.openstream(entry).read()
                    if is_compressed:
                        # zlib 압축 해제 (wbits=-15: raw deflate)
                        try:
                            raw = zlib.decompress(raw, -15)
                        except zlib.error:
                            raw = zlib.decompress(raw)

                    text = _parse_hwp5_section(raw)
                    if text.strip():
                        pages.append(PageContent(
                            page_number=sec_idx + 1,
                            text=text,
                            tables=[],
                            source_file=str(path),
                        ))
                except Exception as e:
                    logger.debug("HWP5 섹션 파싱 오류 [%s/%s]: %s", filename, entry[1], e)

    except Exception as e:
        logger.error("HWP5 열기 실패 [%s]: %s", filename, e)

    logger.info("HWP5 추출 완료: %s → %d섹션", filename, len(pages))
    return pages


def _hwp5_is_compressed(ole) -> bool:
    """
    FileHeader 스트림에서 압축 플래그(bit1)를 확인합니다.
    FileHeader 구조: 32바이트 시그니처 + 4바이트 버전 + 4바이트 속성
    """
    try:
        fh = ole.openstream("FileHeader").read()
        # offset 36: 속성 플래그 (4바이트 little-endian)
        flags = struct.unpack_from("<I", fh, 36)[0]
        return bool(flags & 0x01)   # bit0 = 압축 여부
    except Exception:
        return True   # 기본값: 압축됨


def _parse_hwp5_section(data: bytes) -> str:
    """
    HWP5 BodyText 섹션 바이너리에서 텍스트를 파싱합니다.

    레코드 형식:
      bits[0:9]   tagId
      bits[10:12] level
      bits[12:31] size (0x0FFF이면 다음 4바이트가 실제 size)
    """
    text_parts: List[str] = []
    offset = 0
    length = len(data)

    while offset + 4 <= length:
        header = struct.unpack_from("<I", data, offset)[0]
        offset += 4

        tag_id = header & 0x3FF
        size = (header >> 20) & 0xFFF

        if size == 0xFFF:
            if offset + 4 > length:
                break
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4

        if offset + size > length:
            break

        payload = data[offset: offset + size]
        offset += size

        if tag_id == _TAG_PARA_TEXT and payload:
            text = _decode_para_text(payload)
            if text.strip():
                text_parts.append(text)
            text_parts.append("\n")   # 단락 구분

    return "".join(text_parts)


def _decode_para_text(payload: bytes) -> str:
    """
    PARA_TEXT 페이로드에서 UTF-16LE 텍스트를 추출합니다.

    특수 코드 처리:
      0x0000          : null (스킵)
      0x0002          : 인라인 오브젝트 (4워드 헤더 + 12워드 확장 스킵)
      0x0003          : 탭
      0x000D          : 단락 끝
      0x0004~0x001F   : 기타 제어 문자 (스킵)
      0xD800~0xDBFF   : UTF-16 상위 서로게이트
                         → 다음 하위 서로게이트(0xDC00~0xDFFF)와 합쳐 판단
                         → BMP 외 사설 영역(보조 문자) → 스킵
      0xDC00~0xDFFF   : 짝 없는 하위 서로게이트 → 스킵
    """
    chars: List[str] = []
    i = 0
    words = len(payload) // 2

    while i < words:
        code = struct.unpack_from("<H", payload, i * 2)[0]
        i += 1

        if code == 0x0000:
            continue
        elif code == 0x0002:
            # 인라인 오브젝트: 헤더 4워드 스킵 후 12워드 확장 데이터
            i += 3 + 12
        elif code == 0x0003:
            chars.append("\t")
        elif code == 0x000D:
            chars.append("\n")
        elif code < 0x0020:
            continue   # 기타 제어 문자 스킵
        elif 0xD800 <= code <= 0xDBFF:
            # 상위 서로게이트: 하위 서로게이트를 읽어 쌍 처리
            if i < words:
                low = struct.unpack_from("<H", payload, i * 2)[0]
                i += 1
                if 0xDC00 <= low <= 0xDFFF:
                    # 보조 BMP 코드포인트로 합성
                    full_code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    # 사설 사용 영역(U+E0000 이상)은 HWP 마크업 → 스킵
                    if full_code < 0xE0000:
                        try:
                            chars.append(chr(full_code))
                        except (ValueError, OverflowError):
                            pass
                # 짝 없는 서로게이트면 스킵
        elif 0xDC00 <= code <= 0xDFFF:
            # 짝 없는 하위 서로게이트 → 스킵
            continue
        else:
            try:
                chars.append(chr(code))
            except (ValueError, OverflowError):
                pass

    text = "".join(chars)
    # 임베더가 처리 못하는 잔여 제어 문자 제거
    text = "".join(ch for ch in text if ch >= " " or ch in "\n\t")
    return text


# ══════════════════════════════════════════════════════════════
#  공개 인터페이스
# ══════════════════════════════════════════════════════════════

class HwpExtractor:
    """
    HWP/HWPX 파일에서 텍스트를 추출하는 클래스.

    .hwpx → ZIP + XML 파싱 (추가 의존성 없음)
    .hwp  → OLE2 + zlib 파싱 (olefile 필요)

    사용법:
        extractor = HwpExtractor()
        pages = extractor.extract("path/to/file.hwp")
    """

    def extract(self, path: str) -> List[PageContent]:
        """
        HWP/HWPX 파일을 읽어 PageContent 목록을 반환합니다.

        Args:
            path: HWP 또는 HWPX 파일 경로

        Returns:
            List[PageContent] (빈 파일이면 [])
        """
        ext = Path(path).suffix.lower()
        if ext == ".hwpx":
            return _extract_hwpx(path)
        elif ext == ".hwp":
            return _extract_hwp5(path)
        else:
            logger.warning("HwpExtractor: 지원하지 않는 확장자 [%s]", ext)
            return []
