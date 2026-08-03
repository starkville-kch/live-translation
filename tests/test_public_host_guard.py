from fastapi.testclient import TestClient

from app.server import app


PUBLIC = {"host": "live.starkvillekoreanchurch.org"}


def test_public_root_is_attendee_only():
    with TestClient(app) as client:
        response = client.get("/", headers=PUBLIC)
    assert response.status_code == 200
    assert "Operator Console" not in response.text
    assert "Live Translation" in response.text


def test_public_operator_and_mutations_are_denied():
    with TestClient(app) as client:
        assert client.get("/operator", headers=PUBLIC).status_code == 404
        for path in ("/api/start", "/api/pause", "/api/stop", "/api/reconnect", "/api/devices", "/api/events", "/api/qr.png"):
            assert client.post(path, headers=PUBLIC).status_code == 404


def test_local_operator_remains_available():
    with TestClient(app) as client:
        response = client.get("/operator", headers={"host": "localhost:8080"})
    assert response.status_code == 200
    assert "Operator Console" in response.text
