"""
단턴 쿼리 리라이팅 레이어 — recall@5 개선용.

기존 `query_rewriter.py`는 멀티턴 follow-up 자립화 전용. 본 모듈은 단턴 질문을
학사 공식 용어로 정규화·구체화하여 문서 어휘와의 매칭률을 올린다.

설계 원칙 (4대 원칙):
- #2 비용·지연 최적화: 모듈 LRU 캐시 + 짧은 타임아웃 (기본 0.8s)
- #4 하드코딩 금지: 모델·타임아웃은 settings.conversation 기반

실패·타임아웃 시 None 반환 → caller가 원본 사용.
Feature flag: settings.conversation.single_turn_rewrite_enabled (기본 False)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── 캐시 (모듈-레벨 LRU with TTL) ──────────────────────────────
_CACHE_MAX = 256
_CACHE_TTL = 3600.0  # 초
_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()


def _cache_get(key: str) -> Optional[str]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, val = entry
    if time.time() - ts > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    # LRU bump
    _cache.move_to_end(key)
    return val


def _cache_put(key: str, val: str) -> None:
    _cache[key] = (time.time(), val)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


# ── 프롬프트 ────────────────────────────────────────────────
_SYSTEM_PROMPT_KO = (
    "당신은 학사 규정 검색용 질의 정규화기입니다. "
    "사용자의 단턴 질문을 다음 원칙에 따라 재작성하세요:\n"
    "- 학번 숫자(예: '2020') 뒤에 '학번'이 없으면 붙이고, 가능하면 '내국인'을 명시.\n"
    "- '학점' 단독 사용 시 문맥에 따라 '졸업학점'/'이수학점'/'교양학점' 등 구체화.\n"
    "- 축약형(예: '복전')을 공식 용어('복수전공')로 치환.\n"
    "- 구어체(예: '몇 개야?')를 공식체('몇 개인가요')로.\n"
    "- 의도를 바꾸지 말고, 모호한 요구는 더 구체화하지 말 것.\n"
    "- 재작성된 질문만 한 줄로 출력. 따옴표·설명·접두사 없이."
)
_SYSTEM_PROMPT_EN = (
    "You are a query normalizer for academic regulation search. "
    "Rewrite the single-turn user question using these rules:\n"
    "- Attach Korean-style cohort markers where natural; keep intent intact.\n"
    "- Resolve abbreviations to official terminology.\n"
    "- Normalize colloquial phrasing to formal tone.\n"
    "- Do NOT change the intent or add unrelated specification.\n"
    "- Output only the rewritten question on a single line, no quotes or prefixes."
)


def _normalize_key(q: str) -> str:
    return (q or "").strip().lower()


def _looks_sane(original: str, rewritten: str) -> bool:
    """너무 길어진·빈·동일한 출력은 거절."""
    if not rewritten or len(rewritten) < 3:
        return False
    if rewritten == original:
        return False
    if len(rewritten) > max(40, len(original) * 3):
        return False
    return True


async def rewrite(question: str, lang: str = "ko") -> Optional[str]:
    """단턴 질문을 학사 공식 용어로 정규화. 실패/타임아웃 시 None."""
    conv_cfg = settings.conversation

    if not getattr(conv_cfg, "single_turn_rewrite_enabled", False):
        return None
    if not question or len(question.strip()) < 3:
        return None

    key = _normalize_key(question)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    system = _SYSTEM_PROMPT_KO if lang != "en" else _SYSTEM_PROMPT_EN
    user_body = (
        f"질문:\n{question}\n\n[재작성된 질문]:" if lang != "en"
        else f"Question:\n{question}\n\n[Rewritten]:"
    )

    model = getattr(conv_cfg, "single_turn_rewrite_model", None) or conv_cfg.rewrite_model
    timeout = getattr(
        conv_cfg, "single_turn_rewrite_timeout_sec", conv_cfg.rewrite_timeout_sec
    )

    url = f"{settings.llm.base_url}/v1/chat/completions"
    payload = {
        "model": model,
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
        async with httpx.AsyncClient(timeout=timeout) as client:
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
        logger.debug("단턴 rewrite 타임아웃(%.2fs), 원본 사용", timeout)
        return None
    except Exception as e:
        logger.debug("단턴 rewrite 실패, 원본 사용: %s", e)
        return None

    rewritten = content.split("\n")[0].strip()
    for prefix in (
        "재작성된 쿼리:", "재작성된 질문:", "정규화된 질문:",
        "Rewritten:", "Query:", "Question:",
    ):
        if rewritten.startswith(prefix):
            rewritten = rewritten[len(prefix):].strip()
    rewritten = rewritten.strip('"').strip("'").strip("`").strip()

    if not _looks_sane(question, rewritten):
        logger.debug("단턴 rewrite sanity 탈락: %r → %r", question, rewritten)
        return None

    _cache_put(key, rewritten)
    logger.debug("단턴 rewrite: %r → %r", question, rewritten)
    return rewritten
