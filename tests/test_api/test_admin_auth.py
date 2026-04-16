"""관리자 JWT 인증 테스트."""

from tests.test_api.conftest import admin_headers


def test_login_wrong_password(client):
    """잘못된 비밀번호 → 401."""
    r = client.post("/api/admin/login", json={"password": "wrong_password_123"})
    assert r.status_code == 401
    assert "남은 시도" in r.json()["detail"] or "오류" in r.json()["detail"]


def test_login_correct(client, admin_token):
    """올바른 비밀번호 → 200 + 토큰."""
    if admin_token is None:
        import pytest
        pytest.skip("ADMIN_PASSWORD가 기본값이라 로그인 차단됨")
    assert len(admin_token) > 10


def test_unauthenticated_request(client):
    """인증 없이 admin 엔드포인트 → 401."""
    r = client.get("/api/admin/dashboard")
    assert r.status_code == 401


def test_invalid_token(client):
    """잘못된 토큰 → 401."""
    r = client.get("/api/admin/dashboard", headers=admin_headers("invalid.token"))
    assert r.status_code == 401


def test_logout_invalidates_token(client):
    """로그아웃 후 토큰 무효화."""
    from app.config import settings

    # 새 토큰 발급
    r = client.post("/api/admin/login", json={"password": settings.admin.password})
    if r.status_code != 200:
        import pytest
        pytest.skip("로그인 불가")
    token = r.json()["token"]

    # 인증 확인
    r2 = client.get("/api/admin/dashboard", headers=admin_headers(token))
    assert r2.status_code == 200

    # 로그아웃
    r3 = client.post("/api/admin/logout", headers=admin_headers(token))
    assert r3.status_code == 200

    # 로그아웃 후 → 401
    r4 = client.get("/api/admin/dashboard", headers=admin_headers(token))
    assert r4.status_code == 401
