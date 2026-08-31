"""Tests for PublicHostGuardMiddleware default-deny boundary."""
from fastapi.testclient import TestClient
from app.server import app

PUBLIC = {"host": "live.starkvillekoreanchurch.org"}


def test_public_root_is_attendee_only():
    with TestClient(app) as client:
        response = client.get("/", headers=PUBLIC, follow_redirects=True)
    assert response.status_code == 200
    assert "Live Translation" in response.text
    # Operator console elements must NOT be returned on public host
    assert "관리자 콘솔" not in response.text


def test_public_attendee_routes_allowed():
    with TestClient(app) as client:
        # /live attendee page
        assert client.get("/live", headers=PUBLIC).status_code == 200
        # /logo.webp
        assert client.get("/logo.webp", headers=PUBLIC).status_code in (200, 404)  # 200 if asset present


def test_public_operator_and_mutations_are_denied_404():
    with TestClient(app) as client:
        # Privileged HTML pages
        assert client.get("/admin", headers=PUBLIC).status_code == 404
        assert client.get("/operator", headers=PUBLIC).status_code == 404
        assert client.get("/help", headers=PUBLIC).status_code == 404

        # Privileged APIs
        for path in (
            "/api/start",
            "/api/stop",
            "/api/pause",
            "/api/resume",
            "/api/shutdown",
            "/api/reconnect-public",
            "/api/devices",
            "/api/devices/select",
            "/api/models",
            "/api/models/select",
            "/api/models/test",
            "/api/models/dismiss-alert",
            "/api/config/auto-stop",
            "/api/config/auto-drift-correction",
            "/api/events",
            "/api/qr.png",
            "/api/auth/login",
        ):
            # GET or POST
            assert client.get(path, headers=PUBLIC).status_code == 404
            assert client.post(path, headers=PUBLIC).status_code == 404


def test_local_operator_remains_available():
    with TestClient(app) as client:
        response = client.get("/admin", headers={"host": "localhost:8080"})
    assert response.status_code == 200
    assert "관리자 콘솔" in response.text or "Operator" in response.text
