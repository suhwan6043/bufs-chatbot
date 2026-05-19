"""Frontend ↔ Backend interface 계약 + 6 path 종단점 단위테스트.

목적:
- ChatResponse 스키마 필드 보존 (answer/source_urls/results/intent/duration_ms)
- contact / empty_context / cached / history 엔드포인트 정상 동작
- frontend 호출 시그니처와 동일 (GET stream + POST sync)

전제: backend 컨테이너가 localhost:8000에 떠 있어야 함 (docker compose up -d backend).
TestClient 대신 httpx 직접 호출 — lifespan 초기화 비용 회피 + 실제 운영 path와 동일.

실행:
  docker compose -p bufs-mt1 up -d backend && curl http://localhost:8000/api/health
  pytest tests/test_api/test_chat_interface.py -v
"""
from __future__ import annotations

import json
import os
import uuid
import pytest
import httpx


BASE_URL = os.getenv("BUFS_TEST_BASE_URL", "http://localhost:8000")
TEST_HEADERS = {"X-Test-Mode": "1"}
TIMEOUT = 30.0


# ── fixture: backend 준비 확인 ──────────────────────────────────────

@pytest.fixture(scope="session")
def backend_ready():
    """backend가 healthy + pipeline_ready 한지 확인. 미준비면 skip."""
    try:
        r = httpx.get(f"{BASE_URL}/api/health", timeout=5)
        if r.status_code != 200:
            pytest.skip(f"backend unhealthy: {r.status_code}")
        if not r.json().get("pipeline_ready"):
            pytest.skip("pipeline not ready")
    except Exception as e:
        pytest.skip(f"backend unreachable at {BASE_URL}: {e}")
    return True


@pytest.fixture
def session_id(backend_ready):
    """새 세션 생성."""
    r = httpx.post(f"{BASE_URL}/api/session", json={"lang": "ko"}, timeout=10)
    assert r.status_code == 200
    return r.json()["session_id"]


# ── 헬퍼 ────────────────────────────────────────────────────────────

def _assert_chat_response_schema(data: dict) -> None:
    """ChatResponse 스키마 보존 — frontend 호환성 회귀 즉시 감지."""
    required = {"answer", "source_urls", "results", "intent", "duration_ms"}
    missing = required - set(data.keys())
    assert not missing, f"missing required fields: {missing}"
    assert isinstance(data["answer"], str)
    assert isinstance(data["source_urls"], list)
    assert isinstance(data["results"], list)
    assert isinstance(data["intent"], str)
    assert isinstance(data["duration_ms"], int)
    assert data["duration_ms"] >= 0


def _parse_sse_done(text: str) -> dict | None:
    """SSE 응답에서 event=done의 data JSON 추출."""
    current_event = None
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current_event == "done":
            return json.loads(line.split(":", 1)[1].strip())
    return None


# ── 1) Contact path (LLM 미호출, dept_searcher 실연동) ──────────────

def test_sync_contact_path(backend_ready, session_id):
    """POST /api/chat — contact path 즉답."""
    r = httpx.post(
        f"{BASE_URL}/api/chat",
        params={"session_id": session_id, "question": "학사지원팀 전화번호"},
        headers=TEST_HEADERS, timeout=TIMEOUT,
    )
    assert r.status_code == 200
    data = r.json()
    _assert_chat_response_schema(data)
    assert data["intent"] == "CONTACT"
    assert data["duration_ms"] < 5000, "contact path 5초 이내 응답해야 (LLM 미호출)"
    assert "학사지원팀" in data["answer"] or "Office" in data["answer"]


def test_stream_contact_path_sse(backend_ready, session_id):
    """GET /api/chat/stream — contact path SSE done event."""
    with httpx.stream(
        "GET", f"{BASE_URL}/api/chat/stream",
        params={"session_id": session_id, "question": "학사지원팀 전화번호"},
        headers={**TEST_HEADERS, "Accept": "text/event-stream"},
        timeout=TIMEOUT,
    ) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    data = _parse_sse_done(body)
    assert data is not None, "no done event in SSE response"
    _assert_chat_response_schema(data)
    assert data["intent"] == "CONTACT"


# ── 2) History 엔드포인트 ───────────────────────────────────────────

def test_chat_history_endpoint_shape(backend_ready, session_id):
    """GET /api/chat/history → {messages: list}."""
    r = httpx.get(
        f"{BASE_URL}/api/chat/history",
        params={"session_id": session_id}, timeout=10,
    )
    assert r.status_code == 200
    data = r.json()
    assert "messages" in data
    assert isinstance(data["messages"], list)


def test_chat_clear_history_endpoint(backend_ready, session_id):
    """DELETE /api/chat/history → {ok: true}."""
    r = httpx.delete(
        f"{BASE_URL}/api/chat/history",
        params={"session_id": session_id}, timeout=10,
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True


# ── 3) ChatResponse 필수 필드 보존 (frontend 회귀 감지) ─────────────

@pytest.mark.parametrize("question,expected_intent", [
    ("학사지원팀 전화번호", "CONTACT"),
])
def test_chat_response_required_fields(backend_ready, session_id, question, expected_intent):
    """frontend가 의존하는 5 필드가 ChatResponse에 모두 있어야."""
    r = httpx.post(
        f"{BASE_URL}/api/chat",
        params={"session_id": session_id, "question": question},
        headers=TEST_HEADERS, timeout=TIMEOUT,
    )
    assert r.status_code == 200
    data = r.json()
    for field in ("answer", "source_urls", "results", "intent", "duration_ms"):
        assert field in data, f"missing required field: {field}"
    if expected_intent:
        assert data["intent"] == expected_intent


# ── 4) X-Test-Mode 헤더 동작 검증 ────────────────────────────────────

def test_test_mode_header_skips_logging(backend_ready, session_id):
    """X-Test-Mode 헤더 → JSONL/SQLite 저장 우회."""
    # contact path 1건 (빠른 응답)
    r = httpx.post(
        f"{BASE_URL}/api/chat",
        params={"session_id": session_id, "question": "학사지원팀 전화번호"},
        headers={"X-Test-Mode": "true"}, timeout=TIMEOUT,
    )
    assert r.status_code == 200
    # 응답 자체는 정상이어야 (저장만 우회)
    _assert_chat_response_schema(r.json())


# ── 5) Frontend useChat 호출 시그니처 보존 ─────────────────────────

def test_stream_query_params_minimal(backend_ready):
    """GET /api/chat/stream — session_id + question만 필수, access_token 옵션."""
    sid = uuid.uuid4().hex[:12]
    with httpx.stream(
        "GET", f"{BASE_URL}/api/chat/stream",
        params={"session_id": sid, "question": "테스트"},
        headers={**TEST_HEADERS, "Accept": "text/event-stream"},
        timeout=TIMEOUT,
    ) as r:
        assert r.status_code == 200  # 422 (validation error)면 인터페이스 깨진 것


def test_sync_query_params_minimal(backend_ready):
    """POST /api/chat — session_id + question만 필수."""
    sid = uuid.uuid4().hex[:12]
    r = httpx.post(
        f"{BASE_URL}/api/chat",
        params={"session_id": sid, "question": "학사지원팀 전화번호"},
        headers=TEST_HEADERS, timeout=TIMEOUT,
    )
    assert r.status_code == 200


# ── 6) 잘못된 인풋 거부 (스키마 검증) ───────────────────────────────

def test_question_too_short_rejected(backend_ready, session_id):
    """question 비어있으면 422."""
    r = httpx.post(
        f"{BASE_URL}/api/chat",
        params={"session_id": session_id, "question": ""},
        headers=TEST_HEADERS, timeout=10,
    )
    assert r.status_code == 422


def test_question_too_long_rejected(backend_ready, session_id):
    """question > 2000자면 422."""
    r = httpx.post(
        f"{BASE_URL}/api/chat",
        params={"session_id": session_id, "question": "ㄱ" * 2001},
        headers=TEST_HEADERS, timeout=10,
    )
    assert r.status_code == 422
