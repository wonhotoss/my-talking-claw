from fastapi.testclient import TestClient

from app.server import app


client = TestClient(app)


def test_get_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_post_message_returns_mock_agent_response() -> None:
    response = client.post("/api/message", json={"text": "안녕"})

    assert response.status_code == 200
    assert response.json() == {
        "text": "임시 에이전트 응답입니다. 입력한 내용은 '안녕'입니다."
    }


def test_post_message_rejects_empty_text() -> None:
    response = client.post("/api/message", json={"text": "   "})

    assert response.status_code == 400
    assert response.json() == {"detail": "text must not be empty"}
