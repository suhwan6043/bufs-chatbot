"""AnswerGenerator.rewrite_query — 저신뢰 재시도 경로 단위 테스트.

LLM 호출은 httpx.AsyncClient.post를 monkeypatch로 가로채어 검증한다.
실제 Ollama 서버는 필요 없다.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.pipeline.answer_generator import AnswerGenerator


class _FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._body


def _make_client_mock(response: _FakeResponse):
    """httpx.AsyncClient를 async context manager로 동작시키는 mock 생성."""
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    # async with httpx.AsyncClient(...) as client: 패턴 지원
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=client)
    async_cm.__aexit__ = AsyncMock(return_value=None)
    return async_cm


@pytest.mark.asyncio
async def test_rewrite_query_success(monkeypatch):
    """LLM이 재작성된 쿼리를 반환하면 그대로 사용한다."""
    gen = AnswerGenerator()

    fake_body = {
        "choices": [
            {"message": {"content": "복수전공 이수학점 2023학번 기준"}}
        ]
    }
    fake_response = _FakeResponse(200, fake_body)

    def _fake_client_factory(*args, **kwargs):
        return _make_client_mock(fake_response)

    import app.pipeline.answer_generator as ag_mod
    monkeypatch.setattr(ag_mod.httpx, "AsyncClient", _fake_client_factory)

    rewritten = await gen.rewrite_query(
        question="복수전공 몇 학점이야?",
        lang="ko",
        intent="MAJOR_CHANGE",
    )
    assert rewritten == "복수전공 이수학점 2023학번 기준"


@pytest.mark.asyncio
async def test_rewrite_query_strips_prefix_and_quotes(monkeypatch):
    """LLM이 '재작성된 쿼리:' 접두사나 따옴표를 붙여도 제거한다."""
    gen = AnswerGenerator()

    fake_body = {
        "choices": [
            {"message": {"content": '재작성된 쿼리: "OCU 초과수강료 금액"'}}
        ]
    }
    fake_response = _FakeResponse(200, fake_body)

    def _fake_client_factory(*args, **kwargs):
        return _make_client_mock(fake_response)

    import app.pipeline.answer_generator as ag_mod
    monkeypatch.setattr(ag_mod.httpx, "AsyncClient", _fake_client_factory)

    rewritten = await gen.rewrite_query(
        question="OCU 수강료 얼마예요?",
        lang="ko",
    )
    assert rewritten == "OCU 초과수강료 금액"


@pytest.mark.asyncio
async def test_rewrite_query_failure_returns_original(monkeypatch):
    """LLM 호출이 예외를 던지면 원본 질문을 반환한다 (안전 폴백)."""
    gen = AnswerGenerator()

    def _raising_factory(*args, **kwargs):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    import app.pipeline.answer_generator as ag_mod
    monkeypatch.setattr(ag_mod.httpx, "AsyncClient", _raising_factory)

    original = "졸업요건이 뭔가요?"
    rewritten = await gen.rewrite_query(question=original, lang="ko")
    assert rewritten == original


@pytest.mark.asyncio
async def test_rewrite_query_empty_response_returns_original(monkeypatch):
    """LLM이 공백·너무 짧은 응답을 반환하면 원본 유지."""
    gen = AnswerGenerator()

    fake_body = {"choices": [{"message": {"content": "  "}}]}
    fake_response = _FakeResponse(200, fake_body)

    def _fake_client_factory(*args, **kwargs):
        return _make_client_mock(fake_response)

    import app.pipeline.answer_generator as ag_mod
    monkeypatch.setattr(ag_mod.httpx, "AsyncClient", _fake_client_factory)

    rewritten = await gen.rewrite_query(question="수강신청 언제?", lang="ko")
    assert rewritten == "수강신청 언제?"


@pytest.mark.asyncio
async def test_rewrite_query_absurdly_long_returns_original(monkeypatch):
    """응답이 원본보다 3배 이상 길면 폴백."""
    gen = AnswerGenerator()

    fake_body = {
        "choices": [
            {"message": {"content": "부산외국어대학교 " * 50}}
        ]
    }
    fake_response = _FakeResponse(200, fake_body)

    def _fake_client_factory(*args, **kwargs):
        return _make_client_mock(fake_response)

    import app.pipeline.answer_generator as ag_mod
    monkeypatch.setattr(ag_mod.httpx, "AsyncClient", _fake_client_factory)

    original = "학번?"
    rewritten = await gen.rewrite_query(question=original, lang="ko")
    assert rewritten == original


@pytest.mark.asyncio
async def test_rewrite_query_empty_question_returns_as_is(monkeypatch):
    """빈 질문은 LLM 호출 없이 즉시 반환."""
    gen = AnswerGenerator()

    # httpx를 호출 불가로 설정해 실제로 호출되지 않음을 확인
    def _forbidden(*args, **kwargs):
        raise AssertionError("should not be called")

    import app.pipeline.answer_generator as ag_mod
    monkeypatch.setattr(ag_mod.httpx, "AsyncClient", _forbidden)

    assert (await gen.rewrite_query(question="", lang="ko")) == ""
    assert (await gen.rewrite_query(question="   ", lang="ko")) == "   "
