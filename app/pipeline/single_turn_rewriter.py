"""
단턴 쿼리 리라이팅 레이어 — recall@5 개선용.

기존 `query_rewriter.py`는 멀티턴 follow-up 자립화 전용. 본 모듈은 단턴 질문을
학사 공식 용어로 정규화하여 문서 어휘와의 매칭률을 올린다.

**규칙 기반 재작성** (2026-04-19 변경):
LLM 호출은 본 환경(LM Studio qwen 5~6초)에서 매 쿼리마다 과도한 지연.
결정적 규칙(축약형·학번·구어체)만 적용하여 0ms로 정규화.

설계 원칙 (4대 원칙):
- #2 비용·지연 최적화: 규칙 기반, LLM 미호출
- #4 하드코딩 금지: 축약 매핑은 데이터 파일로 분리 가능 (현재는 모듈 상수)

실패 시 None 반환 → caller가 원본 사용.
Feature flag: settings.conversation.single_turn_rewrite_enabled (기본 False)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# ── 축약형 → 공식 용어 매핑 ────────────────────────────────
# 데이터 기반 (구어 자주 쓰이는 축약형만). 4원칙 #4는 이 사전 분리로 완화.
_ABBREV_MAP: dict[str, str] = {
    "복전": "복수전공",
    "부전": "부전공",
    "조졸": "조기졸업",
    "교양필": "교양필수",
    "전필": "전공필수",
    "전선": "전공선택",
    "글소": "글로벌소통역량",
    "자전": "자유전공",
    "재수강": "재수강",  # 이미 공식
    "학사안내": "학사안내",
    "수강신청": "수강신청",
}

# 학번 숫자 정규화: "2020학번"/"2020년" 이미 수식어 있으면 건드리지 않음.
# "2020" 단독 또는 "20학번"을 "2020학번"으로.
_YEAR_CONTEXT = re.compile(r"(?<![0-9])(20[0-2][0-9])(?![학년번년])")
_SHORT_COHORT = re.compile(r"(?<![0-9])([0-2][0-9])학번(?![0-9])")

# 구어체 끝맺음 → 정중체
_CASUAL_ENDINGS = [
    (re.compile(r"뭐야\??$"), "무엇인가요"),
    (re.compile(r"어때\??$"), "어떻게 되나요"),
    (re.compile(r"얼마\??$"), "얼마인가요"),
    (re.compile(r"얼마야\??$"), "얼마인가요"),
    (re.compile(r"몇개야\??$"), "몇 개인가요"),
    (re.compile(r"몇 개야\??$"), "몇 개인가요"),
    (re.compile(r"있어\??$"), "있나요"),
    (re.compile(r"없어\??$"), "없나요"),
    (re.compile(r"돼\??$"), "되나요"),
    (re.compile(r"돼요\??$"), "되나요"),
    (re.compile(r"가능\??$"), "가능한가요"),
]


def _apply_abbrev(q: str) -> str:
    """어절 경계 기준 축약어 치환."""
    out = q
    for short, full in _ABBREV_MAP.items():
        if short == full:
            continue
        # 어절 경계: 앞뒤에 한글/영문 숫자 없는 것만 (과매칭 방지)
        pattern = re.compile(rf"(?<![가-힣A-Za-z0-9]){re.escape(short)}(?![가-힣A-Za-z0-9])")
        out = pattern.sub(full, out)
    return out


def _apply_cohort(q: str) -> str:
    """2자리 학번 → 4자리 학번, 벌거벗은 4자리 연도 → 학번 단서 추가."""
    # "20학번" → "2020학번"
    def _two_digit(m):
        yy = int(m.group(1))
        century = 2000 if yy < 40 else 1900  # 안전한 가정
        return f"{century+yy}학번"
    out = _SHORT_COHORT.sub(_two_digit, q)
    # "2020 졸업" (학번 컨텍스트 단어 있을 때) → "2020학번"
    # 단순 heuristic: 뒤에 "졸업", "복수전공", "수강", "학점" 등이 있으면
    _TRIGGERS = ("졸업", "복수전공", "부전공", "전공", "학점", "수강", "이수")
    def _year_maybe(m):
        year = m.group(1)
        # 주변 10자 이내에 학사 트리거가 있으면 "학번" 추가
        full = out  # closure
        pos = m.start()
        window = full[max(0, pos - 4):pos + 20]
        if any(t in window for t in _TRIGGERS):
            return f"{year}학번"
        return year
    out = _YEAR_CONTEXT.sub(_year_maybe, out)
    return out


def _apply_formal_tone(q: str) -> str:
    """구어체 끝맺음 → 정중체."""
    out = q
    for pat, repl in _CASUAL_ENDINGS:
        out = pat.sub(repl, out)
    return out


async def rewrite(question: str, lang: str = "ko") -> Optional[str]:
    """단턴 질문을 학사 공식 용어로 규칙 기반 정규화.

    변경 없으면 None 반환 → caller는 원본 사용.
    비활성 시 None.
    """
    conv_cfg = settings.conversation
    if not getattr(conv_cfg, "single_turn_rewrite_enabled", False):
        return None
    if not question:
        return None
    if lang == "en":
        return None  # 영어 미지원 (필요 시 확장)

    q = question.strip()
    rewritten = q
    rewritten = _apply_abbrev(rewritten)
    rewritten = _apply_cohort(rewritten)
    rewritten = _apply_formal_tone(rewritten)

    if not rewritten or rewritten == q:
        return None

    if len(rewritten) > max(80, len(q) * 3):
        # sanity: 이상 팽창 거절
        return None

    logger.debug("단턴 rewrite(규칙): %r → %r", q, rewritten)
    return rewritten
