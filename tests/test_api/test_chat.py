"""채팅 API 테스트 — POST /api/chat, GET /api/chat/stream, 이력.

LLM 의존 테스트는 @pytest.mark.slow로 표시 (기본 실행 시 skip).
실행: pytest tests/test_api/test_chat.py -m "not slow"  (빠른)
실행: pytest tests/test_api/test_chat.py                (전체, LLM 필요)
"""

import pytest


def test_chat_history_empty(client, session_id):
    """새 세션의 이력은 비어있어야 한다."""
    r = client.get("/api/chat/history", params={"session_id": session_id})
    assert r.status_code == 200
    assert r.json()["messages"] == []


def test_chat_clear_history(client, session_id):
    """이력 삭제."""
    r = client.delete("/api/chat/history", params={"session_id": session_id})
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.slow
def test_chat_sync_returns_answer(client, session_id):
    """POST /api/chat → answer 필드가 반환되어야 한다. (LLM 필요)"""
    r = client.post(
        "/api/chat",
        params={"session_id": session_id, "question": "개강일은 언제인가?"},
        timeout=120,
    )
    assert r.status_code == 200
    data = r.json()
    assert "answer" in data
    assert len(data["answer"]) > 0
    assert "intent" in data
    assert "duration_ms" in data


@pytest.mark.slow
def test_chat_contact_shortcircuit(client, session_id):
    """연락처 질문은 LLM 없이 즉시 응답. (LLM fallback 가능)"""
    r = client.post(
        "/api/chat",
        params={"session_id": session_id, "question": "학사지원팀 전화번호"},
        timeout=120,
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["answer"]) > 0


@pytest.mark.slow
def test_chat_history_after_question(client, session_id):
    """질문 후 이력에 user/assistant 메시지가 있어야 한다. (LLM 필요)"""
    client.post(
        "/api/chat",
        params={"session_id": session_id, "question": "수강신청 기간"},
        timeout=120,
    )
    r = client.get("/api/chat/history", params={"session_id": session_id})
    messages = r.json()["messages"]
    user_msgs = [m for m in messages if m["role"] == "user"]
    asst_msgs = [m for m in messages if m["role"] == "assistant"]
    assert len(user_msgs) >= 1
    assert len(asst_msgs) >= 1
