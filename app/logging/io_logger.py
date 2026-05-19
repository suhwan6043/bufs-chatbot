"""
컴포넌트별 인풋/아웃풋 로그 헬퍼 — docker logs 실시간 가시화 + 단위테스트 capture.

5/19 도입 (plan: multi-task-1-gleaming-stonebraker.md).

설계:
- 모든 파이프라인 컴포넌트(LanguageDetector / QueryAnalyzer / FollowUpDetector /
  QueryRewriter / query_understanding / QueryRouter / CommunitySelector /
  Reranker / ContextMerger / AnswerGenerator / ResponseValidator) + chat.py 9 stage
  진입·종료에서 통일된 `[<comp>-IN]` / `[<comp>-OUT]` 형식 print 출력.
- `flush=True` print 사용 → LOG_LEVEL과 무관하게 stdout. capsys로 단위테스트도 가능.
- DEBUG 레벨 환경(`LOG_LEVEL=DEBUG`)에서만 후보 list 등 상세 detail 출력.
- 한글은 그대로 — 외부 라이브러리의 raw bytes 출력은 backend/main.py에서 이미 차단.

PII 마스킹:
- `PIIRedactor.redact_for_log` (학번 → `[REDACTED_ID]`)만 적용. 이름은 운영 분석용 보존.
- None 입력은 `or ""` 가드.
- 줄바꿈은 ' ⏎ ' 1글자로 치환 (한 줄화).

Truncate 정책 (호출 측이 호출 전에 적용 — 본 헬퍼는 dump만):
- question 120자
- answer 200자 (DEBUG 시 600자)
- candidate text 60자 prefix

레벨 정책:
- INFO (default): summary만 (count / chars / elapsed_ms / 핵심 분기)
- DEBUG: + 후보 list / 결과 list / prompt size 등 상세
"""
from __future__ import annotations

import os
from typing import Any, Optional


# 모듈 로드 시 1회 — LOG_LEVEL 환경변수 캐싱
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_IS_DEBUG = _LOG_LEVEL == "DEBUG"


def is_debug() -> bool:
    """LOG_LEVEL=DEBUG 여부 — 컴포넌트가 추가 detail 출력 결정용."""
    return _IS_DEBUG


def _redact(text: Optional[str]) -> str:
    """학번 마스킹 + 줄바꿈 한 줄화. None 안전."""
    if not text:
        return ""
    try:
        from app.transcript.security import PIIRedactor
        text = PIIRedactor.redact_for_log(text)
    except Exception:
        pass
    return text.replace("\n", " ⏎ ").replace("\r", " ")


def _truncate(text: Optional[str], limit: int) -> str:
    """PII 마스킹 + truncate. 잘리면 끝에 '…' 추가."""
    if not text:
        return ""
    redacted = _redact(text)
    if len(redacted) <= limit:
        return redacted
    return redacted[:limit] + "…"


def _fmt_value(v: Any) -> str:
    """field value 포맷팅 — quote 처리, list 길이만, None은 'null'."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        # list는 길이만 (DEBUG 시 호출 측에서 별도 field로 풀어서 전달)
        return f"[{len(v)}]"
    if isinstance(v, dict):
        return f"{{{len(v)}}}"
    s = str(v)
    # 따옴표 안에 들어가야 의미 명확한 경우 (호출 측에서 결정)
    return s


def log_io(
    component: str,
    phase: str,
    sid: Optional[str] = None,
    **fields: Any,
) -> None:
    """컴포넌트별 인풋/아웃풋 한 줄 print.

    Args:
        component: 컴포넌트 약식 (analyzer, router, reranker, merger, generator,
                   validator, understand, follow_up, rewriter, langdet, community,
                   CHAT 등)
        phase: 'IN' 또는 'OUT'
        sid: 세션 ID (있으면 sid[:8] 사용)
        **fields: 측정값. 값이 str이면 따옴표로 감싸 출력.

    출력 형식:
        [<component>-<phase>] sid=xxxxxxxx field1=value1 field2='quoted str' ...
    """
    parts = [f"[{component}-{phase}]"]
    if sid:
        parts.append(f"sid={str(sid)[:8]}")
    for key, value in fields.items():
        if isinstance(value, str) and not value.startswith("'") and " " in value:
            parts.append(f"{key}='{value}'")
        else:
            parts.append(f"{key}={_fmt_value(value)}")
    print(" ".join(parts), flush=True)


def log_question(component: str, phase: str, sid: Optional[str], question: Optional[str],
                 **fields: Any) -> None:
    """question 포함 IN log. question은 120자 truncate."""
    log_io(component, phase, sid=sid,
           q=f"'{_truncate(question, 120)}'", qlen=len(question or ""),
           **fields)


def log_answer(component: str, phase: str, sid: Optional[str], answer: Optional[str],
               **fields: Any) -> None:
    """answer 포함 OUT log. answer는 INFO 200자 / DEBUG 600자 truncate."""
    limit = 600 if _IS_DEBUG else 200
    log_io(component, phase, sid=sid,
           a=f"'{_truncate(answer, limit)}'", a_chars=len(answer or ""),
           **fields)


def log_candidates(component: str, sid: Optional[str], candidates: list,
                   *, label: str = "cand", text_attr: str = "text",
                   score_attr: str = "score", source_attr: str = "source",
                   limit_text: int = 60, max_show: int = 30) -> None:
    """후보 list 상세 출력 (DEBUG 시에만 호출). reranker/router/merger 등에서 사용.

    각 후보를 한 줄씩 출력:
        [<component>-{label}] sid=xx i=0 score=0.875 src='학사안내.pdf:p38' text='...'
    """
    if not _IS_DEBUG:
        return
    for i, c in enumerate(candidates[:max_show]):
        score = getattr(c, score_attr, 0.0) if not isinstance(c, dict) else c.get(score_attr, 0.0)
        source = getattr(c, source_attr, "") if not isinstance(c, dict) else c.get(source_attr, "")
        text = getattr(c, text_attr, "") if not isinstance(c, dict) else c.get(text_attr, "")
        print(
            f"[{component}-{label}] sid={str(sid or '')[:8]} i={i} "
            f"score={float(score):.4f} src='{_truncate(source, 60)}' "
            f"text='{_truncate(text, limit_text)}'",
            flush=True,
        )
    if len(candidates) > max_show:
        print(
            f"[{component}-{label}] sid={str(sid or '')[:8]} ... +{len(candidates) - max_show} more",
            flush=True,
        )
