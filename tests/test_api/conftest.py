"""
FastAPI 백엔드 API 테스트 Fixtures.

TestClient는 lifespan을 실행하여 파이프라인 싱글톤을 초기화.
실제 ChromaDB + Embedder를 사용 (파이프라인 무수정 검증).
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient — 모듈 단위 공유 (무거운 초기화 1회)."""
    from backend.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def session_id(client):
    """테스트용 세션 생성 → session_id 반환."""
    r = client.post("/api/session", json={"lang": "ko"})
    assert r.status_code == 200
    return r.json()["session_id"]


@pytest.fixture(scope="module")
def admin_token(client):
    """JWT 관리자 토큰 획득."""
    from app.config import settings
    r = client.post("/api/admin/login", json={"password": settings.admin.password})
    if r.status_code == 200:
        return r.json()["token"]
    # 기본 비밀번호 차단 시 None
    return None


def admin_headers(token: str) -> dict:
    """Authorization 헤더 생성 헬퍼."""
    return {"Authorization": f"Bearer {token}"}
