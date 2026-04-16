"""
Follow-up 감지기 — 현재 질문이 직전 턴에 의존하는지 휴리스틱 판정.

원칙 2(비용·지연 최적화):
- <1ms 규칙 기반 판정. LLM 미사용.
- 비-follow-up(~80% 추정)은 여기서 즉시 종료되어 rewriting 파이프라인 완전 스킵.

반환:
- is_follow_up: follow-up 여부
- skip_rule_stage: 규칙 기반 치환 스킵 플래그 (분배/순서 대명사 등 복잡 케이스)
- reason: 판정 근거 (로깅·디버깅용)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from app.config import settings


# ── 단일 지시 대명사 (Stage 2 규칙 치환 가능) ──
_SINGULAR_PRONOUNS_KO = ("그거", "이거", "저거", "그것", "이것", "저것", "그게", "이게")
_SINGULAR_PRONOUNS_EN = (" it", " that", " this", "it ", "that ", "this ")

# ── 분배/순서 대명사 (Stage 2 스킵 → Stage 3 LLM 폴백) ──
_DISTRIBUTIVE_KO = ("각각", "둘 다", "둘다", "셋 다", "셋다", "모두", "다들", "전부")
_ORDINAL_KO = ("첫번째", "첫 번째", "두번째", "두 번째", "세번째", "세 번째", "마지막")
_DISTRIBUTIVE_EN = (" each ", "each ", " both ", "both ", " all ", "all of ")
_ORDINAL_EN = (" first", " second", " third", " last", " former", " latter")

# ── 비교·차이 표현 (follow-up 강한 시그널) ──
_COMPARISON_KO = ("차이", "비교", "다른 점", "다른점", "vs")
_COMPARISON_EN = (" difference", " compare", " vs ", " versus ")

# ── 생략/축약 시그널 ──
_ELLIPTIC_KO = ("그럼", "그럼요", "그러면", "그리고", "또", "그런데", "근데")
_ELLIPTIC_EN = ("then ", "and ", "but ", "also ")


@dataclass(frozen=True)
class FollowUpSignal:
    is_follow_up: bool
    skip_rule_stage: bool  # True → query_rewriter가 Stage 2(규칙) 스킵하고 Stage 3(LLM) 직행
    reason: str


def _contains_any(text: str, needles: Iterable[str]) -> Optional[str]:
    for n in needles:
        if n in text:
            return n.strip()
    return None


def _has_subject_ko(text: str) -> bool:
    """
    한국어 주어 존재 휴리스틱.

    조사 "은/는/이/가/께서" + 선행 2자 이상 체언이 있으면 주어로 본다.
    완벽한 파싱은 아니지만 follow-up 감지용으로 충분.
    """
    if re.search(r"[가-힣A-Za-z0-9]{2,}(은|는|이|가|께서)\s", text):
        return True
    return False


def detect(
    current_query: str,
    history: list[dict] | None,
) -> FollowUpSignal:
    """
    현재 질문이 직전 대화에 의존하는지 판정.

    history: [{"role": "user"|"assistant", "content": str, ...}, ...]
             세션 store의 messages 형식과 호환.

    판정 순위 (첫 매칭으로 확정):
    1. 비교/차이 + 분배/순서 표현 → follow-up, 규칙 스킵
    2. 단순 지시대명사 → follow-up, 규칙 가능
    3. 분배/순서/비교 단독 → follow-up, 규칙 스킵
    4. 생략 시그널 + 짧은 질문 → follow-up, 규칙 스킵
    5. 주어 생략 + 매우 짧은 질문 → follow-up, 규칙 스킵
    """
    if not current_query or not current_query.strip():
        return FollowUpSignal(False, False, "empty_query")

    # history 없으면 무조건 비-follow-up
    if not history:
        return FollowUpSignal(False, False, "no_history")

    q = current_query.strip()
    q_lower = " " + q.lower() + " "
    word_count = len(q.split())
    max_words = settings.conversation.follow_up_max_words

    # 분배/순서 검출 (Stage 2 스킵 플래그 세팅용)
    has_distributive = bool(
        _contains_any(q, _DISTRIBUTIVE_KO + _ORDINAL_KO)
        or _contains_any(q_lower, _DISTRIBUTIVE_EN + _ORDINAL_EN)
    )
    has_comparison = bool(
        _contains_any(q, _COMPARISON_KO)
        or _contains_any(q_lower, _COMPARISON_EN)
    )

    # 1) 비교·차이 동시 발생 → 강한 follow-up, 규칙 스킵
    if has_distributive and has_comparison:
        return FollowUpSignal(True, True, "distributive+comparison")

    # 2) 단일 지시 대명사 → 규칙 치환 시도 가능
    singular = (
        _contains_any(q, _SINGULAR_PRONOUNS_KO)
        or _contains_any(q_lower, _SINGULAR_PRONOUNS_EN)
    )
    if singular:
        return FollowUpSignal(True, False, f"singular_pronoun:{singular}")

    # 3) 분배/순서 또는 비교 단독 → 규칙 스킵 (문맥 필요)
    if has_distributive:
        return FollowUpSignal(True, True, "distributive_only")
    if has_comparison and word_count <= max_words * 2:
        return FollowUpSignal(True, True, "comparison_only")

    # 4) 생략 접속사 + 짧은 질문
    elliptic = (
        _contains_any(q, _ELLIPTIC_KO)
        or _contains_any(q_lower, _ELLIPTIC_EN)
    )
    if elliptic and word_count <= max_words:
        return FollowUpSignal(True, True, f"elliptic_short:{elliptic}")

    # 5) 주어 생략 + 매우 짧은 한국어 질문
    if word_count <= max_words and not _has_subject_ko(q) and re.search(r"[가-힣]", q):
        return FollowUpSignal(True, True, "no_subject_short_ko")

    return FollowUpSignal(False, False, "not_follow_up")
