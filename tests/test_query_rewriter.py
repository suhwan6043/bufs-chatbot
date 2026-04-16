"""query_rewriter Stage 2(규칙) + Stage 3(LLM 타임아웃 폴백) 테스트."""

import asyncio
from unittest.mock import patch, AsyncMock

import httpx
import pytest

from app.pipeline.query_rewriter import (
    _extract_last_assistant_entity,
    _format_history_for_prompt,
    llm_rewrite,
    rewrite,
    rule_based_rewrite,
)


def _hist(pairs: list[tuple[str, str]]) -> list[dict]:
    out: list[dict] = []
    for u, a in pairs:
        out.append({"role": "user", "content": u})
        out.append({"role": "assistant", "content": a})
    return out


# ── _extract_last_assistant_entity ──

def test_extract_entity_from_bold():
    hist = _hist([("?", "주요 장학금은 **국가장학금**과 교내장학금입니다.")])
    assert _extract_last_assistant_entity(hist) == "국가장학금"


def test_extract_entity_from_bullet():
    hist = _hist([("?", "- 국가장학금 I유형\n- 국가장학금 II유형")])
    assert _extract_last_assistant_entity(hist) is not None


def test_extract_entity_empty_history():
    assert _extract_last_assistant_entity([]) is None


def test_extract_entity_only_user_messages():
    assert _extract_last_assistant_entity([{"role": "user", "content": "x"}]) is None


# ── rule_based_rewrite (Stage 2) ──

def test_rule_based_ko_pronoun_replacement():
    hist = _hist([("장학금 유형?", "**국가장학금**과 교내장학금이 있습니다.")])
    result = rule_based_rewrite("그거 자세히 알려줘", hist)
    assert result is not None
    assert "국가장학금" in result
    assert "그거" not in result


def test_rule_based_en_pronoun_replacement():
    hist = _hist([("scholarship types?", "Main types include **Type I Scholarship**.")])
    result = rule_based_rewrite("Tell me more about it", hist)
    assert result is not None
    assert "Type I Scholarship" in result


def test_rule_based_no_pronoun_returns_none():
    hist = _hist([("x", "y")])
    assert rule_based_rewrite("완전히 다른 질문?", hist) is None


def test_rule_based_no_history_returns_none():
    assert rule_based_rewrite("그거 뭐야?", []) is None


# ── _format_history_for_prompt ──

def test_format_history_trims_assistant_length():
    long_text = "A" * 500
    hist = _hist([("q", long_text)])
    formatted = _format_history_for_prompt(hist, max_turns=1)
    assert "…" in formatted  # 200자 절단 마커
    assert len(formatted) < 400


def test_format_history_limits_turns():
    hist = _hist([
        ("q1", "a1"), ("q2", "a2"), ("q3", "a3"),
    ])
    formatted = _format_history_for_prompt(hist, max_turns=2)
    assert "q1" not in formatted  # 가장 오래된 턴 제외
    assert "q3" in formatted


# ── llm_rewrite: 타임아웃·실패 폴백 ──

@pytest.mark.asyncio
async def test_llm_rewrite_timeout_returns_none():
    hist = _hist([("장학금?", "**국가장학금**...")])

    async def _raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timeout")

    with patch("httpx.AsyncClient") as mock_client:
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = False
        instance.post = _raise_timeout
        mock_client.return_value = instance

        result = await llm_rewrite("각각 차이?", hist, lang="ko")
    assert result is None


@pytest.mark.asyncio
async def test_llm_rewrite_empty_history_returns_none():
    result = await llm_rewrite("뭐?", [], lang="ko")
    assert result is None


# ── rewrite: 통합 엔트리 ──

@pytest.mark.asyncio
async def test_rewrite_falls_back_to_original_on_all_failure():
    hist = _hist([("장학금?", "**국가장학금**...")])

    async def _raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timeout")

    with patch("httpx.AsyncClient") as mock_client:
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = False
        instance.post = _raise_timeout
        mock_client.return_value = instance

        # skip_rule_stage=True + LLM 타임아웃 → 원본 반환
        result = await rewrite("각각 차이?", hist, skip_rule_stage=True, lang="ko")
    assert result == "각각 차이?"


@pytest.mark.asyncio
async def test_rewrite_uses_rule_stage_when_available():
    # Stage 2 성공 → LLM 안 타야 함
    hist = _hist([("장학금?", "**국가장학금**이 있습니다.")])
    result = await rewrite("그거 자세히", hist, skip_rule_stage=False, lang="ko")
    assert "국가장학금" in result
