"""관리자 보호 엔드포인트 테스트 (인증 필요)."""

import pytest
from tests.test_api.conftest import admin_headers


@pytest.fixture(scope="module")
def auth(client, admin_token):
    """인증 헤더. admin_token이 없으면 모듈 전체 skip."""
    if admin_token is None:
        pytest.skip("ADMIN_PASSWORD 기본값 → 관리자 API 테스트 불가")
    return admin_headers(admin_token)


def test_dashboard(client, auth):
    r = client.get("/api/admin/dashboard", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert "kpi" in data
    assert "daily_chart" in data
    assert "intent_distribution" in data
    assert "recent_chats" in data
    assert data["kpi"]["total_questions"] >= 0


def test_graduation_list(client, auth):
    r = client.get("/api/admin/graduation", headers=auth)
    assert r.status_code == 200
    assert "rows" in r.json()
    assert isinstance(r.json()["rows"], list)


def test_early_graduation(client, auth):
    r = client.get("/api/admin/early-graduation", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert "schedules" in data
    assert "criteria" in data
    assert "eligibility" in data


def test_schedule_list(client, auth):
    r = client.get("/api/admin/schedule", headers=auth)
    assert r.status_code == 200
    events = r.json()["events"]
    assert isinstance(events, list)


def test_graph_status(client, auth):
    r = client.get("/api/admin/graph", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["total_nodes"] > 0
    assert data["total_edges"] > 0
    assert "type_counts" in data


def test_crawler_status(client, auth):
    r = client.get("/api/admin/crawler", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert "enabled" in data
    assert "is_running" in data
    assert "notice_count" in data


def test_contacts_list(client, auth):
    r = client.get("/api/admin/contacts", headers=auth)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_contacts_search(client, auth):
    r = client.get("/api/admin/contacts/search", headers=auth, params={"q": "학사지원팀"})
    assert r.status_code == 200
    assert "results" in r.json()


def test_logs_list(client, auth):
    r = client.get("/api/admin/logs", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert "total" in data
    assert "entries" in data


def test_logs_dates(client, auth):
    r = client.get("/api/admin/logs/dates", headers=auth)
    assert r.status_code == 200
    assert "dates" in r.json()
