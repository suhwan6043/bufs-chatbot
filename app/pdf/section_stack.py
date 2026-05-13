"""
헤더 Stack 기반 계층 경로 추적기.

폰트 크기 분포 자동 분석 + 헤더 패턴 매칭으로 PDF 페이지 내 헤더를 검출하고,
새 헤더가 push될 때 같거나 더 깊은 level의 기존 헤더는 자동 pop된다.

사용 예:
    stack = SectionStack()
    stack.push(1, "Ⅱ. 수강신청")
    stack.push(2, "1. 일정 안내")
    stack.push(2, "2. 정정 기간")     # L2가 push되면 이전 L2는 pop
    print(stack.path)                  # "Ⅱ. 수강신청 > 2. 정정 기간"
    stack.push(1, "Ⅲ. 졸업요건")      # L1이 push되면 이전 L1·L2 모두 pop
    print(stack.path)                  # "Ⅲ. 졸업요건"
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional


class SectionStack:
    """헤더 출현 순으로 계층 경로를 stack으로 관리."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []

    def push(self, level: int, title: str) -> None:
        """새 헤더 push. 같거나 깊은 level은 모두 pop 후 추가."""
        # 같은 level 또는 더 깊은 level의 기존 항목 모두 제거
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, title))

    def reset(self) -> None:
        self._stack.clear()

    @property
    def path(self) -> str:
        """현재 활성 경로 — ' > ' 조인."""
        return " > ".join(t for _, t in self._stack)

    @property
    def titles(self) -> list[str]:
        """현재 활성 제목 리스트 (level 순)."""
        return [t for _, t in self._stack]

    @property
    def depth(self) -> int:
        return len(self._stack)

    def snapshot(self) -> dict:
        """청크 메타데이터로 주입할 dict 형태."""
        return {
            "section_path": self.path,
            "section_titles": self.titles,
            "section_depth": self.depth,
        }

    def __repr__(self) -> str:
        return f"SectionStack({self.path!r})"


# ── 헤더 분류 ───────────────────────────────────────────────────────────────
# 패턴 기반 보조 — 폰트 크기와 결합하여 정확도 향상
_HEADER_PATTERNS: list[tuple[int, re.Pattern]] = [
    # L1: 로마자 대제목 ("Ⅰ.", "Ⅱ.", ..., "Ⅹ.")
    (1, re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s*\S")),
    # L1: 영문 챕터 ("Chapter 1", "Part I")
    (1, re.compile(r"^(?:Chapter|Part)\s+[\dⅠⅡⅢⅣⅤ]", re.IGNORECASE)),
    # L2: 숫자 소제목 ("1.", "2.", ..., "10.")
    (2, re.compile(r"^\d{1,2}\.\s*\S")),
    # L3: 한글 가나다 ("가.", "나.", "다.", ...)
    (3, re.compile(r"^[가-힣]\.\s*\S")),
    # L4: 숫자 괄호 또는 닫는 괄호 ("1)", "(1)")
    (4, re.compile(r"^(?:\(\d+\)|\d+\))\s*\S")),
    # L5: 불릿 (▶, ▫, •, ●)
    (5, re.compile(r"^[▶▫•●·□■◇◆]\s*\S")),
]


def detect_pattern_level(text: str) -> Optional[int]:
    """패턴 기반 헤더 level 추정. 매칭 없으면 None."""
    text = text.strip()
    if not text:
        return None
    for level, pat in _HEADER_PATTERNS:
        if pat.search(text):
            return level
    return None


# ── 폰트 크기 자동 분석 ─────────────────────────────────────────────────────
def analyze_font_distribution(font_sizes: list[float]) -> dict[float, int]:
    """폰트 크기 빈도 분석. 본문(최빈) 기준으로 헤더 후보 도출."""
    counter = Counter(round(s, 1) for s in font_sizes)
    return dict(counter.most_common())


def derive_header_levels(
    font_sizes: list[float], min_body_count: int = 10,
    min_header_diff: float = 1.5,
) -> dict[float, int]:
    """폰트 크기별 → 헤더 level 매핑 자동 결정.

    규칙:
        1. 최빈 폰트(본문) 식별
        2. 본문보다 `min_header_diff`pt 이상 큰 폰트만 헤더 후보
        3. 가장 큰 → L1, 다음 → L2, ...
        4. 인접 크기는 같은 level로 묶기 (>=0.7pt 차이만 새 level)

    Returns:
        {15.0: 1, 13.5: 2}  같은 매핑.
    """
    counter = Counter(round(s, 1) for s in font_sizes)
    if not counter:
        return {}

    # 본문 폰트 = 최빈 (단, 출현이 min_body_count 이상이어야)
    most_common = counter.most_common()
    body_size = None
    for size, cnt in most_common:
        if cnt >= min_body_count:
            body_size = size
            break
    if body_size is None:
        body_size = most_common[0][0]

    # 본문보다 충분히 큰 폰트만 (기본 +1.5pt) 내림차순
    header_sizes = sorted(
        [s for s in counter if s >= body_size + min_header_diff],
        reverse=True,
    )

    # 인접한 크기는 같은 level로 묶기
    levels: dict[float, int] = {}
    current_level = 1
    prev_size: Optional[float] = None
    for sz in header_sizes:
        if prev_size is not None and prev_size - sz > 0.7:
            current_level += 1
        levels[sz] = current_level
        prev_size = sz

    return levels


# ── 통합 분류기 ─────────────────────────────────────────────────────────────
def classify_header(
    text: str, font_size: float, font_levels: dict[float, int],
    min_len: int = 2, max_len: int = 60,
) -> Optional[int]:
    """텍스트와 폰트 크기를 종합해 헤더 level 추정.

    우선순위:
        1. 폰트가 헤더 사이즈인 경우만 헤더 인정 (본문 폰트는 제외)
           — 패턴이 매칭되더라도 본문 폰트면 단순 본문 enumeration으로 간주
        2. 폰트 + 패턴 모두 있으면 더 상위(작은 숫자) level 채택
        3. URL·이메일 등 본문스러운 텍스트는 항상 본문 처리

    Returns:
        1~5 정수 또는 None (헤더 아님).
    """
    text = text.strip()
    if not (min_len <= len(text) <= max_len):
        return None

    # 한글·영문 글자 최소 1개 (순수 숫자 라인 제외)
    if not re.search(r"[가-힣A-Za-z]", text):
        return None

    # 본문스러운 패턴: URL, 이메일, 매우 긴 라인은 헤더 아님
    if re.search(r"https?://|@\w+\.\w|www\.", text, re.IGNORECASE):
        return None

    # 목차 leader dot 제거된 후 길이 검사
    cleaned = re.sub(r"[·\.]{3,}.*$", "", text).strip()
    cleaned = re.sub(r"\s+\d+\s*$", "", cleaned).strip()
    if len(cleaned) < min_len:
        return None

    pat_level = detect_pattern_level(cleaned)
    font_level = font_levels.get(round(font_size, 1))

    # 폰트가 본문이면(font_level is None) 헤더 아님
    # → 본문 안 enumeration("3. 공인결석...")이 헤더로 잘못 분류 방지
    if font_level is None:
        return None

    # 폰트 헤더 + 패턴 헤더: 둘 중 더 상위 (작은 숫자)
    if pat_level is not None:
        return min(pat_level, font_level)
    return font_level
