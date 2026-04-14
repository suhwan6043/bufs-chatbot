"""
PDF 섹션 경로 추적기 — 폰트 크기 기반 계층 감지.

각 페이지가 속한 섹션 경로(Level 1 > Level 2)를 추출해
인제스트 시 청크 메타데이터로 주입합니다.

감지 규칙:
    - Level 1 (15pt+): 로마자/대제목 (예: "Ⅱ. 수강신청")
    - Level 2 (13.5~14.5pt): 숫자 소제목 (예: "8. 성적평가 선택제도")
    - 본문 (~11pt): 섹션 아님

페이지별 섹션 = 해당 페이지까지 감지된 가장 최근 L1/L2 헤더.
"""

from __future__ import annotations

import logging
from typing import Optional
from collections import defaultdict

import pdfplumber

logger = logging.getLogger(__name__)

# 폰트 크기 임계값 (학사안내·가이드북 기준 튜닝)
_LEVEL1_MIN = 14.5
_LEVEL2_MIN = 13.5
_MIN_HEADER_LEN = 3
_MAX_HEADER_LEN = 80


def build_page_to_section_map(pdf_path: str) -> dict[int, str]:
    """PDF를 스캔해 `page_number(1-based) → "L1 > L2"` 맵을 반환합니다.

    Returns:
        예: {
            6: "Ⅱ. 수강신청 > 1. 수강신청 일정 및 유의사항 안내",
            47: "Ⅲ. 교육과정 및 졸업 > 8. 성적평가 선택제도 및 부분적 성적 포기 제도",
            ...
        }
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = pdf.pages
            headers_per_page = []
            for pg_idx, page in enumerate(pages):
                l1, l2 = _extract_headers_on_page(page)
                headers_per_page.append((pg_idx + 1, l1, l2))
    except Exception as e:
        logger.warning("섹션 추출 실패 (%s): %s", pdf_path, e)
        return {}

    # 순차 누적: 페이지에 L1/L2 있으면 업데이트, 없으면 이전 값 유지
    current_l1: Optional[str] = None
    current_l2: Optional[str] = None
    page_to_section: dict[int, str] = {}

    for pg, new_l1, new_l2 in headers_per_page:
        if new_l1:
            current_l1 = new_l1
            current_l2 = None  # L1 바뀌면 L2 리셋
        if new_l2:
            current_l2 = new_l2

        parts = [p for p in (current_l1, current_l2) if p]
        page_to_section[pg] = " > ".join(parts) if parts else ""

    return page_to_section


def _extract_headers_on_page(page) -> tuple[Optional[str], Optional[str]]:
    """페이지에서 최상위 L1과 L2 헤더를 각각 1개씩 반환합니다.

    같은 페이지에 여러 L1/L2가 있으면 가장 위(y 큰값)에 있는 것 선택.
    """
    chars = page.chars
    if not chars:
        return None, None

    # y 좌표별 라인 그룹핑 (텍스트 + 최대 폰트)
    lines = defaultdict(lambda: {'text': '', 'max_size': 0, 'y': 0})
    for c in sorted(chars, key=lambda x: (-x['y0'], x['x0'])):
        y = round(c['y0'], 0)
        lines[y]['text'] += c['text']
        lines[y]['max_size'] = max(lines[y]['max_size'], c['size'])
        lines[y]['y'] = y

    # 폰트 크기 내림차순으로 라인 정렬 (큰 것부터 검토)
    sorted_lines = sorted(
        lines.values(),
        key=lambda x: (-x['max_size'], -x['y'])
    )

    l1 = None
    l2 = None
    for info in sorted_lines:
        text = _clean_header(info['text'])
        if not text:
            continue
        sz = info['max_size']
        if sz >= _LEVEL1_MIN and not l1:
            l1 = text
        elif sz >= _LEVEL2_MIN and sz < _LEVEL1_MIN and not l2:
            l2 = text
        if l1 and l2:
            break

    return l1, l2


def _clean_header(text: str) -> Optional[str]:
    """헤더 텍스트를 정리하고 유효성 검증합니다."""
    text = text.strip()
    # 목차 줄임표 제거: "Ⅱ. 수강신청···············2"
    import re
    text = re.sub(r'[\u00b7\.]{3,}.*$', '', text).strip()
    # 페이지 번호만 남은 경우 제거
    text = re.sub(r'\s+\d+\s*$', '', text).strip()

    if len(text) < _MIN_HEADER_LEN or len(text) > _MAX_HEADER_LEN:
        return None
    # 순수 숫자나 특수문자만 있는 라인 제외
    if not re.search(r'[가-힣A-Za-z]', text):
        return None
    return text
