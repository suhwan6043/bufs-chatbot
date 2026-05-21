"""direct_answer grounding 관측 — **dry-run 전용·동결** (2026-05-21).

## ⚠️ 본 모듈은 enforce 대상이 아님

5/21 dry-run 측정 결과 본 게이트가 **범주 오류**임이 확인됨:
  - direct_answer는 graph FAQ 노드(사람이 미리 작성)에서 나오는 텍스트.
    LLM이 생성하지 않으므로 "환각" 대상이 아님.
  - chunks 본문과 graph 노드의 direct_answer 메타데이터는 **다른 출처**.
    포맷이 다르면 정상 응답도 false reject (실측: 16% 정상 트래픽 false reject).
  - 진짜 위험은 "**틀린 FAQ 노드 매칭**" (예: 2023학번 질문에 2020학번 노드).
    이는 grounding(텍스트 출처 검증)이 아니라 **retrieval 정합성** 문제 →
    **PR #25 rerank gate(logit ≥ 0.20) 강화**로 해결해야 함 (2주차 graph 작업).

## 본 모듈의 현재 역할 — 관측 전용

  - `GROUNDING_MODE=dry_run` (default): grounding 검사 + JSON 로그만.
    direct_answer와 chunks의 포맷 불일치를 **데이터 품질 시그너처**로 모니터링.
    운영 진단에는 가치 있음 (어떤 fact가 자주 누락되는지).
  - `GROUNDING_MODE=enforce`: 미사용. 켜지 않을 것. 코드 경로는 유지하되
    측정 + 정교화 없이는 사용 금지.

## 향후 작업 (2주차 이관)

  - direct_answer 안전성은 **retrieval 게이트 강화**로:
    1. PR #25 rerank_bypass_threshold 0.20 → ?
    2. cohort·intent 일치 검증을 채택 루프에 추가 (AnswerUnit 확장)
    3. graph 노드 매칭 정합성 측정 + 임계 조정

## 본 모듈 한계 (참고)

  ❌ substring 매칭은 cohort 오매칭 못 잡음 — "2023학번 130학점" 환각 시
     청크 어딘가에 다른 학번의 "130학점"이 있으면 grounded 통과
  ❌ graph 메타 ↔ chunks 본문 포맷 차이로 정상 응답도 false reject 가능
  ✅ 명백한 완전 날조(청크에 전혀 없는 숫자·도메인)는 잡을 수 있음

## EN 사각지대

`chat.py`의 direct_answer 채택 조건이 `analysis.lang != "en"`이라 EN은 항상
LLM 경로. 따라서 본 grounding은 **KO direct 전용**. EN direct 경로가 추후
생기면 본 게이트가 안 도는 사각지대가 됨 — `progress.txt`에 메모.

## 동작 모드 (env)

- `GROUNDING_MODE=dry_run` (default): 검사만 수행, 결과 logger.info 기록.
  Reject 없음. **차단율 측정용 — 안전망 1단계**.
- `GROUNDING_MODE=enforce`: 실패 시 chat.py가 `merged.direct_answer=""` 처리
  하여 LLM 경로로 fall-through. dry-run에서 차단율 검증 후 켤 것.

## 배경 (2026-05-21)

운영 트래픽의 16%가 `path=direct`로 LLM·validator 우회 (app.log 기준).
PR #25 rerank gate가 "어떤 direct_answer를 채택할지" 신호이나, 본 게이트는
"채택된 게 실제로 retrieved 청크에 fact가 있는지" 검증.

사용:
  from app.pipeline.grounding import is_grounded
  grounded, missing = is_grounded(direct_answer, chunks)
  if not grounded and grounding_mode == "enforce":
      merged.direct_answer = ""
"""

from __future__ import annotations

import re
from typing import Iterable

from app.models import SearchResult


# ── Fact 정규식 ────────────────────────────────────────────────────────
# 라벨은 디버깅·로깅·테스트용. value는 group(0).strip() 후 corpus substring 매칭.

FACT_PATTERNS: dict[str, re.Pattern] = {
    # 학점: "120학점", "120 학점", "12 학점"
    "credit":  re.compile(r"\d+\s*학점"),
    # 학번: "2023학번", "2023 학번"
    "cohort":  re.compile(r"20\d{2}\s*학번"),
    # 학년: "1학년", "4 학년"
    "grade":   re.compile(r"\d+\s*학년"),
    # 전화번호: "051-509-5182", "509-5182"
    "phone":   re.compile(r"\d{2,3}-\d{3,4}-\d{3,4}"),
    # 날짜 (한글): "2026년 1월 28일"
    "date_ko": re.compile(r"20\d{2}년\s*\d{1,2}월\s*\d{1,2}일"),
    # 날짜 (슬래시): "7/6", "8/30" — "1/2학기" 같은 분수 학기 표현 제외
    "date_slash": re.compile(r"\d{1,2}/\d{1,2}(?!\s*학기)"),
    # URL·도메인: "sugang.bufs.ac.kr", "https://..."
    # \w만 쓰면 "fake-sugang.bufs.ac.kr"에서 "sugang.bufs.ac.kr"만 부분 매치 → 합성 환각이 substring 통과.
    # \b...\b + 하이픈 허용으로 전체 도메인 단위 추출 (5/21 dry-run 측정에서 fake_url 사고 발견).
    "url_bufs":  re.compile(r"\b[\w.-]+\.bufs\.ac\.kr\b"),
    "url_proto": re.compile(r"https?://\S+"),
}


# 공백 정규화 정규식 — 청크·answer 둘 다 같은 정규화 거쳐야 매칭 안정
_WHITESPACE_RE = re.compile(r"\s+")
# 숫자만 추출 정규식 — 전화번호 등 포맷 변종 매칭용 보조 경로
_DIGITS_RE = re.compile(r"\D+")


def _normalize_whitespace(text: str) -> str:
    """다중 공백·개행을 단일 공백으로."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _digits_only(text: str) -> str:
    """문자열에서 숫자만 추출 — 포맷 변종에 강한 보조 매칭용.
    "051-509-5182" → "0515095182"
    "120 학점" → "120"
    """
    return _DIGITS_RE.sub("", text)


def _build_corpus(chunks: Iterable[SearchResult]) -> str:
    """retrieved chunks의 .text 필드를 단일 문자열로 합치고 공백 정규화."""
    parts = []
    for c in chunks:
        if c is None:
            continue
        t = c.text or ""
        if t:
            parts.append(t)
    return _normalize_whitespace(" ".join(parts))


def is_grounded(
    direct_answer: str,
    chunks: Iterable[SearchResult],
) -> tuple[bool, list[str]]:
    """direct_answer의 fact 토큰이 chunks corpus에 실존하는지 검증.

    Returns:
        (grounded, missing): 통과 여부 + 누락된 fact 라벨 리스트 (디버깅용).
                              direct_answer 비었거나 fact 0개면 (True, []).

    매칭 우선순위 (각 fact value당):
      1차: 공백 정규화 corpus에 substring으로 존재 → 통과
      2차: 숫자만 추출한 corpus(_digits_only)에 substring 존재 → 통과 (포맷 변종)
      둘 다 실패 → missing 등록

    한계:
      - substring 매칭은 "완전 날조"만 잡고, cohort 오매칭은 못 잡음.
        모듈 docstring 역할 한정 참조.
    """
    if not direct_answer or not direct_answer.strip():
        return True, []

    answer_norm = _normalize_whitespace(direct_answer)
    corpus_norm = _build_corpus(chunks)
    corpus_digits = _digits_only(corpus_norm)

    missing: list[str] = []
    for label, pattern in FACT_PATTERNS.items():
        for match in pattern.finditer(answer_norm):
            value = match.group(0).strip()
            if not value:
                continue

            # 1차: 정규화 corpus에 직접 substring 매칭
            if value in corpus_norm:
                continue

            # 2차: 숫자만 추출 후 매칭 (전화번호 포맷 변종 등)
            value_digits = _digits_only(value)
            if value_digits and len(value_digits) >= 3 and value_digits in corpus_digits:
                continue

            missing.append(f"{label}:{value}")

    return (len(missing) == 0, missing)
