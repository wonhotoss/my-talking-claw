from fastapi.testclient import TestClient

from app import server


client = TestClient(server.app)


def test_get_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
