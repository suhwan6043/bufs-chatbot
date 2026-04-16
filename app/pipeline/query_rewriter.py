"""
멀티턴 쿼리 재작성기 — follow-up 질문을 자립적 검색 쿼리로 변환.

2단계 폴백 구조:
- Stage 2 (규칙 치환): 단일 지시 대명사("그거", "it")를 직전 entity로 치환. <5ms.
- Stage 3 (경량 LLM): Stage 2 실패·스킵 시 gemma3:4b 등으로 재작성. 타임아웃 0.8s.

원칙 2(비용·지연 최적화):
- 타임아웃 초과·파싱 실패 시 원본 쿼리 반환 (graceful fallback).
- 경량 모델을 별도 `model` 설정으로 분리 → 메인 파이프라인 모델(gemma4:26b)과 독립.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

from app.config import settings
from app.pipeline import ko_tokenizer

logger = logging.getLogger(__name__)


# ── Stage 2: 규칙 기반 대명사 치환 ──

_PRONOUN_TOKENS_KO = ("그거", "이거", "저거", "그것", "이것", "저것", "그게", "이게")

# 치환 시 제외할 불용어 (entity 후보에서 제거)
_STOPWORDS = {
    "이", "그", "저", "것", "수", "때", "점", "말", "분", "곳", "들",
    "the", "a", "an", "is", "are", "to", "of", "in", "for",
}


def _extract_last_assistant_entity(history: list[dict]) -> Optional[str]:
    """
    직전 assistant 응답에서 주요 entity 후보 추출.

    간단 휴리스틱:
    - 굵은 표시(**..**) 안 토큰 우선
    - 그 다음 제목/헤더 (줄 시작 #, -)
    - 마지막으로 빈도 기반 명사 토큰
    """
    if not history:
        return None

    last_assistant = None
    for msg in reversed(history):
        if msg.get("role") == "assistant" and msg.get("content"):
            last_assistant = msg["content"]
            break
    if not last_assistant:
        return None

    # 1) **..** 강조 토큰
    bold = re.findall(r"\*\*([^*]+)\*\*", last_assistant)
    for b in bold:
        cand = b.strip().split(":")[0].strip()
        if 2 <= len(cand) <= 30 and cand.lower() not in _STOPWORDS:
            return cand

    # 2) 불릿 첫 단어 ("- XXX" / "* XXX")
    bullets = re.findall(r"^\s*[-*]\s*([^:\n]+)", last_assistant, flags=re.M)
    for b in bullets:
        cand = b.strip().split(" ")[0]
        if 2 <= len(cand) <= 30 and cand.lower() not in _STOPWORDS:
            return cand

    # 3) 빈도 기반 (한국어 토큰)
    try:
        tokens = ko_tokenizer.tokenize(last_assistant)
    except Exception:
        tokens = last_assistant.split()
    counts: dict[str, int] = {}
    for t in tokens:
        if 2 <= len(t) <= 20 and t.lower() not in _STOPWORDS:
            counts[t] = counts.get(t, 0) + 1
    if counts:
        return max(counts.items(), key=lambda x: x[1])[0]

    return None


def rule_based_rewrite(query: str, history: list[dict]) -> Optional[str]:
    """
    단일 지시 대명사를 직전 assistant entity로 치환.

    성공 시 치환된 쿼리, 실패 시 None 반환.
    """
    if not query:
        return None

    entity = _extract_last_assistant_entity(history)
    if not entity:
        return None

    # 한국어 대명사 치환
    replaced = query
    hit = False
    for p in _PRONOUN_TOKENS_KO:
        if p in replaced:
            replaced = replaced.replace(p, entity, 1)
            hit = True
            break

    # 영어 대명사 치환 (단어 경계 고려)
    if not hit:
        en_patterns = [
            (r"\bit\b", entity),
            (r"\bthat\b", entity),
            (r"\bthis\b", entity),
        ]
        for pat, rep in en_patterns:
            new = re.sub(pat, rep, replaced, count=1, flags=re.IGNORECASE)
            if new != replaced:
                replaced = new
                hit = True
                break

    if not hit or replaced == query:
        return None

    return replaced


# ── Stage 3: 경량 LLM 재작성 ──

_SYSTEM_PROMPT_KO = (
    "당신은 대화 맥락 기반 쿼리 재작성기입니다. "
    "사용자의 마지막 질문을 이전 대화 없이도 이해 가능한 자립적 문장으로 재작성하세요. "
    "재작성된 질문만 한 줄로 출력하고, 설명·따옴표·접두사는 붙이지 마세요. "
    "원래 의도를 유지하고, 맥락에서 명시적으로 언급된 개체(대상·주제)만 보충하세요."
)
_SYSTEM_PROMPT_EN = (
    "You are a conversational query rewriter. "
    "Rewrite the user's last question as a self-contained sentence that is understandable without prior turns. "
    "Output only the rewritten question on a single line, no explanation, quotes, or prefixes. "
    "Preserve the original intent and only inject entities explicitly mentioned in the context."
)


def _format_history_for_prompt(history: list[dict], max_turns: int) -> str:
    """최근 max_turns개 user/assistant pair만 프롬프트용 텍스트로."""
    if not history:
        return ""
    pairs: list[tuple[str, str]] = []
    current_user: Optional[str] = None
    for msg in history:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            current_user = content
        elif role == "assistant" and current_user is not None:
            pairs.append((current_user, content))
            current_user = None

    pairs = pairs[-max_turns:]
    lines: list[str] = []
    for u, a in pairs:
        lines.append(f"User: {u}")
        # assistant는 200자까지만 — 토큰 절약
        a_trim = a if len(a) <= 200 else a[:200] + "…"
        lines.append(f"Assistant: {a_trim}")
    return "\n".join(lines)


async def llm_rewrite(
    query: str,
    history: list[dict],
    lang: str = "ko",
) -> Optional[str]:
    """
    경량 LLM으로 follow-up 쿼리 재작성.

    실패·타임아웃 시 None 반환 (caller가 원본 쿼리로 폴백).
    """
    conv_cfg = settings.conversation
    if not query or not history:
        return None

    history_text = _format_history_for_prompt(
        history, conv_cfg.rewrite_max_input_turns
    )
    if not history_text:
        return None

    if lang == "en":
        system = _SYSTEM_PROMPT_EN
        user_body = f"[Prior conversation]\n{history_text}\n\n[User's last question]\n{query}\n\n[Rewritten self-contained question]:"
    else:
        system = _SYSTEM_PROMPT_KO
        user_body = f"[이전 대화]\n{history_text}\n\n[사용자의 마지막 질문]\n{query}\n\n[자립적으로 재작성된 질문]:"

    url = f"{settings.llm.base_url}/v1/chat/completions"
    payload = {
        "model": conv_cfg.rewrite_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_body},
        ],
        "stream": False,
        "max_tokens": conv_cfg.rewrite_max_tokens,
        "temperature": 0.1,
        "top_p": 0.9,
        "think": False,
    }

    try:
        async with httpx.AsyncClient(timeout=conv_cfg.rewrite_timeout_sec) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
    except (httpx.TimeoutException, asyncio.TimeoutError):
        logger.debug("쿼리 재작성 타임아웃 (%.2fs), 원본 사용", conv_cfg.rewrite_timeout_sec)
        return None
    except Exception as e:
        logger.debug("쿼리 재작성 실패, 원본 사용: %s", e)
        return None

    rewritten = content.split("\n")[0].strip()
    for prefix in (
        "재작성된 쿼리:", "재작성된 질문:", "자립적으로 재작성된 질문:",
        "Rewritten:", "Query:", "Question:",
    ):
        if rewritten.startswith(prefix):
            rewritten = rewritten[len(prefix):].strip()
    rewritten = rewritten.strip('"').strip("'").strip("`").strip()

    if not rewritten or len(rewritten) < 3:
        return None
    # 지나친 팽창 방지 — 원본의 5배 이내만 채택
    if len(rewritten) > max(40, len(query) * 5):
        return None
    if rewritten == query:
        return None

    return rewritten


# ── 통합 엔트리 ──

async def rewrite(
    query: str,
    history: list[dict],
    *,
    skip_rule_stage: bool = False,
    lang: str = "ko",
) -> str:
    """
    Follow-up 쿼리 재작성 통합 엔트리.

    skip_rule_stage=True: 분배/순서 대명사 등 규칙으로 처리 불가 → 바로 LLM.
    모든 경로 실패 시 원본 쿼리 반환 (호출측은 안전하게 사용 가능).
    """
    if not settings.conversation.rewrite_enabled:
        return query

    # Stage 2: 규칙 치환
    if not skip_rule_stage:
        try:
            ruled = rule_based_rewrite(query, history)
            if ruled:
                logger.debug("rewrite[rule]: '%s' → '%s'", query[:40], ruled[:40])
                return ruled
        except Exception as e:
            logger.debug("규칙 치환 오류, LLM 폴백: %s", e)

    # Stage 3: LLM 재작성
    llm_result = await llm_rewrite(query, history, lang=lang)
    if llm_result:
        logger.info("rewrite[llm]: '%s' → '%s'", query[:40], llm_result[:40])
        return llm_result

    return query
