"""세션 관리 API 테스트."""


def test_create_session(client):
    r = client.post("/api/session", json={"lang": "ko"})
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert data["lang"] == "ko"
    assert data["messages_count"] == 0


def test_create_session_en(client):
    r = client.post("/api/session", json={"lang": "en"})
    assert r.status_code == 200
    assert r.json()["lang"] == "en"


def test_get_session(client, session_id):
    r = client.get(f"/api/session/{session_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == session_id
    assert data["user_profile"] is None


def test_get_session_not_found(client):
    r = client.get("/api/session/nonexistent123")
    assert r.status_code == 404


def test_update_profile(client, session_id):
    r = client.put(
        f"/api/session/{session_id}/profile",
        json={"student_id": "2023", "department": "영어학부", "student_type": "내국인"},
    )
    assert r.status_code == 200

    # 프로필 저장 확인
    r2 = client.get(f"/api/session/{session_id}")
    profile = r2.json()["user_profile"]
    assert profile["student_id"] == "2023"
    assert profile["department"] == "영어학부"


def test_update_lang(client, session_id):
    r = client.put(f"/api/session/{session_id}/lang", params={"lang": "en"})
    assert r.status_code == 200

    r2 = client.get(f"/api/session/{session_id}")
    assert r2.json()["lang"] == "en"


def test_delete_session(client):
    # 생성 후 삭제
    r = client.post("/api/session", json={"lang": "ko"})
    sid = r.json()["session_id"]

    r2 = client.delete(f"/api/session/{sid}")
    assert r2.status_code == 200

    # 삭제 후 조회 → 404
    r3 = client.get(f"/api/session/{sid}")
    assert r3.status_code == 404
