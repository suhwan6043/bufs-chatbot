"""query_understanding 모듈 단위 테스트.

LLM 호출은 monkeypatch로 모킹. 실제 네트워크 호출 없음.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.models import Intent, QueryAnalysis, QuestionType
from app.pipeline import query_understanding as qu
from app.pipeline.follow_up_detector import FollowUpSignal


# ── 헬퍼 단위 ──────────────────────────────────────────────────────

def test_parse_json_response_plain():
    assert qu._parse_json_response('{"x": 1}') == {"x": 1}


def test_parse_json_response_with_code_fence():
    assert qu._parse_json_response('```json\n{"x": 1}\n```') == {"x": 1}


def test_parse_json_response_with_surrounding_text():
    text = 'Sure! Here is the answer:\n{"intent": "CERTIFICATE"}\nLet me know.'
    assert qu._parse_json_response(text) == {"intent": "CERTIFICATE"}


def test_parse_json_response_empty():
    assert qu._parse_json_response("") is None
    assert qu._parse_json_response("no json here") is None
    assert qu._parse_json_response("{not valid json}") is None


def test_coerce_intent_valid():
    assert qu._coerce_intent("CERTIFICATE") is Intent.CERTIFICATE
    assert qu._coerce_intent("scholarship_apply") is Intent.SCHOLARSHIP_APPLY
    assert qu._coerce_intent(" GRADE_OPTION ") is Intent.GRADE_OPTION


def test_coerce_intent_invalid():
    assert qu._coerce_intent("") is None
    assert qu._coerce_intent("NOT_A_REAL") is None


def test_coerce_question_type_default_on_invalid():
    assert qu._coerce_question_type("factoid") == QuestionType.FACTOID
    assert qu._coerce_question_type("REASONING") == QuestionType.REASONING
    assert qu._coerce_question_type("garbage") == QuestionType.FACTOID
    assert qu._coerce_question_type("") == QuestionType.FACTOID


def test_system_prompt_contains_all_new_categories():
    prompt = qu._build_system_prompt()
    new_categories = [
        "REGISTRATION_GENERAL", "GRADE_OPTION", "REREGISTRATION",
        "SCHOLARSHIP_APPLY", "SCHOLARSHIP_QUALIFICATION", "TUITION_BENEFIT",
        "CERTIFICATE", "CONTACT", "FACILITY",
    ]
    for cat in new_categories:
        assert cat in prompt, f"새 카테고리 {cat}가 시스템 프롬프트에 누락"


def test_build_messages_includes_few_shots():
    messages = qu._build_messages("테스트 질문", "")
    # system 1 + few-shot 4*(user+assistant)=8 + real user 1 = 10
    assert len(messages) == 10
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert "테스트 질문" in messages[-1]["content"]


# ── _llm_dict_to_result ──────────────────────────────────────────

def _valid_llm_dict() -> dict:
    return {
        "is_follow_up": False,
        "standalone_query": "재학증명서 어디서 떼나요?",
        "intent": "CERTIFICATE",
        "intent_confidence": 0.92,
        "question_type": "procedural",
        "lang": "ko",
        "entities": {"asks_url": True, "question_focus": "method"},
    }


def test_llm_dict_to_result_happy_path():
    import time
    result = qu._llm_dict_to_result(
        _valid_llm_dict(),
        original_query="재학증명서 어디서 떼나요?",
        history=None,
        source="llm",
        started_at=time.monotonic() - 0.1,
    )
    assert result is not None
    assert result.analysis.intent is Intent.CERTIFICATE
    assert result.analysis.question_type == QuestionType.PROCEDURAL
    assert result.analysis.lang == "ko"
    assert result.analysis.entities.get("asks_url") is True
    assert result.intent_confidence == pytest.approx(0.92)
    assert result.source == "llm"
    assert result.follow_up_signal.is_follow_up is False
    assert result.rewritten_query == "재학증명서 어디서 떼나요?"


def test_llm_dict_to_result_missing_intent_returns_none():
    import time
    data = _valid_llm_dict()
    del data["intent"]
    assert qu._llm_dict_to_result(
        data, original_query="x", history=None,
        source="llm", started_at=time.monotonic(),
    ) is None


def test_llm_dict_to_result_invalid_intent_returns_none():
    import time
    data = _valid_llm_dict()
    data["intent"] = "REGISTRATION_NOT_REAL"
    assert qu._llm_dict_to_result(
        data, original_query="x", history=None,
        source="llm", started_at=time.monotonic(),
    ) is None


def test_llm_dict_to_result_history_none_forces_no_follow_up():
    """history=None일 때 LLM이 is_follow_up=True 출력해도 False로 보정."""
    import time
    data = _valid_llm_dict()
    data["is_follow_up"] = True
    data["standalone_query"] = "재작성된 쿼리"
    result = qu._llm_dict_to_result(
        data, original_query="원본 쿼리", history=None,
        source="llm", started_at=time.monotonic(),
    )
    assert result is not None
    assert result.follow_up_signal.is_follow_up is False
    assert result.rewritten_query == "원본 쿼리"  # standalone 무시


def test_llm_dict_to_result_clamps_confidence():
    import time
    data = _valid_llm_dict()
    data["intent_confidence"] = 1.5
    result = qu._llm_dict_to_result(
        data, original_query="x", history=None,
        source="llm", started_at=time.monotonic(),
    )
    assert result is not None
    assert result.intent_confidence == 1.0

    data["intent_confidence"] = "garbage"
    result2 = qu._llm_dict_to_result(
        data, original_query="x", history=None,
        source="llm", started_at=time.monotonic(),
    )
    assert result2 is not None
    assert result2.intent_confidence == 0.0


# ── 3단계 폴백 통합 (LLM 모킹) ────────────────────────────────────

def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f


def test_understand_uses_primary_llm_when_valid(monkeypatch):
    monkeypatch.setattr(qu, "_call_llm", _async_return(_valid_llm_dict()))
    result = asyncio.run(qu.understand("재학증명서 어디서 떼나요?", None))
    assert result.source == "llm"
    assert result.analysis.intent is Intent.CERTIFICATE


def test_understand_falls_back_to_secondary_llm_on_primary_failure(monkeypatch):
    """1차 None → 2차 호출. _call_llm을 2번 호출되도록 모킹."""
    call_count = {"n": 0}

    async def fake_call(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None
        return _valid_llm_dict()

    monkeypatch.setattr(qu, "_call_llm", fake_call)
    result = asyncio.run(qu.understand("증명서 발급?", None))
    assert call_count["n"] == 2
    assert result.source == "llm_fallback"
    assert result.analysis.intent is Intent.CERTIFICATE


def test_understand_falls_back_to_rules_on_both_llm_failures(monkeypatch):
    """1차·2차 모두 None → 룰 폴백 동작."""
    monkeypatch.setattr(qu, "_call_llm", _async_return(None))
    result = asyncio.run(qu.understand("수강신청 언제 시작해?", None))
    assert result.source == "rule_fallback"
    assert isinstance(result.analysis, QueryAnalysis)
    # 룰 폴백은 구 Intent 그대로 반환 (REGISTRATION 등 — 신 분할 없음).
    # 정확한 분류는 룰 동작에 의존하므로 enum 존재만 확인.
    assert isinstance(result.analysis.intent, Intent)
