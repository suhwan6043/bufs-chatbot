"""헬스체크 엔드포인트 테스트."""


def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_health_llm(client):
    r = client.get("/api/health/llm")
    assert r.status_code == 200
    data = r.json()
    assert "available" in data
    assert "model" in data


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "CAMCHAT" in r.json()["message"]
