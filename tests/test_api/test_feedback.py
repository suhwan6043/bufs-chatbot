"""피드백 + 별점 API 테스트."""


def test_submit_feedback(client, session_id):
    """피드백 제출."""
    r = client.post("/api/feedback", json={"session_id": session_id, "text": "테스트 피드백입니다."})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_submit_feedback_empty(client, session_id):
    """빈 피드백은 422 에러."""
    r = client.post("/api/feedback", json={"session_id": session_id, "text": ""})
    assert r.status_code == 422


def test_rating_invalid_index(client, session_id):
    """존재하지 않는 메시지 인덱스에 별점."""
    r = client.post("/api/rating", json={
        "session_id": session_id, "message_index": 999, "rating": 5,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_rating_invalid_value(client, session_id):
    """잘못된 별점 값 (0 또는 6)."""
    r = client.post("/api/rating", json={
        "session_id": session_id, "message_index": 0, "rating": 0,
    })
    assert r.status_code == 422

    r2 = client.post("/api/rating", json={
        "session_id": session_id, "message_index": 0, "rating": 6,
    })
    assert r2.status_code == 422
