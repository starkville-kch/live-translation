"""Tests for Operator Authentication Boundary."""
import os
import pytest
from fastapi.testclient import TestClient
from app.server import app
from app.operator_auth import (
    create_session_token,
    is_auth_enabled,
    verify_password,
    verify_session_token,
)


def test_auth_disabled_when_env_empty(monkeypatch):
    monkeypatch.delenv("SKC_OPERATOR_PASSWORD", raising=False)
    assert not is_auth_enabled()

    with TestClient(app) as client:
        res = client.get("/api/auth/status")
        assert res.status_code == 200
        data = res.json()
        assert data["auth_enabled"] is False
        assert data["authenticated"] is True


def test_auth_enabled_flow(monkeypatch):
    monkeypatch.setenv("SKC_OPERATOR_PASSWORD", "secret123")
    monkeypatch.setenv("SKC_SESSION_SECRET", "testsecret")
    assert is_auth_enabled()

    with TestClient(app) as client:
        # 1. Unauthenticated status
        res = client.get("/api/auth/status")
        assert res.status_code == 200
        data = res.json()
        assert data["auth_enabled"] is True
        assert data["authenticated"] is False

        # 2. Privileged API rejected with 401
        res = client.post("/api/pause")
        assert res.status_code == 401

        res = client.get("/api/devices")
        assert res.status_code == 401

        # 3. Wrong password rejected
        res = client.post("/api/auth/login", json={"password": "wrongpassword"})
        assert res.status_code == 401

        # 4. Correct password succeeds and sets session cookie
        res = client.post("/api/auth/login", json={"password": "secret123"})
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert "skc_session" in client.cookies

        # 5. Privileged API allowed with session cookie
        res = client.post("/api/pause")
        assert res.status_code == 200

        # 6. Logout clears session cookie
        res = client.post("/api/auth/logout")
        assert res.status_code == 200

        # 7. Privileged API rejected again
        res = client.post("/api/pause")
        assert res.status_code == 401


def test_token_verification(monkeypatch):
    monkeypatch.setenv("SKC_SESSION_SECRET", "my_secret_key")
    token = create_session_token()
    assert verify_session_token(token)

    # Invalid token
    assert not verify_session_token("invalid.token")
    assert not verify_session_token("")


def test_admin_unauthenticated_html_renders_auth_modal(monkeypatch):
    monkeypatch.setenv("SKC_OPERATOR_PASSWORD", "testpass123")
    with TestClient(app) as client:
        res = client.get("/admin")
        assert res.status_code == 200
        html = res.text
        # Auth modal is visible (not hidden) in initial HTML payload
        assert 'id="auth-modal"' in html
        assert 'id="auth-modal" class="" style="display: flex;"' in html
        # Logout lock button is hidden when unauthenticated
        assert 'id="header-auth-controls" style="display: none;' in html


def test_admin_authenticated_html_does_not_block_console(monkeypatch):
    monkeypatch.setenv("SKC_OPERATOR_PASSWORD", "testpass123")
    monkeypatch.setenv("SKC_SESSION_SECRET", "testsecret")
    with TestClient(app) as client:
        # Login
        login_res = client.post("/api/auth/login", json={"password": "testpass123"})
        assert login_res.status_code == 200

        # View /admin as authenticated
        res = client.get("/admin")
        assert res.status_code == 200
        html = res.text
        # Auth modal is hidden
        assert 'id="auth-modal" class="hidden" style="display: none;"' in html
        # Logout lock button is visible
        assert 'id="header-auth-controls" style="display: inline-flex;' in html


def test_privileged_api_rejects_unauthenticated_request(monkeypatch):
    monkeypatch.setenv("SKC_OPERATOR_PASSWORD", "testpass123")
    with TestClient(app) as client:
        # Privileged endpoints must return 401
        assert client.post("/api/start").status_code == 401
        assert client.post("/api/stop").status_code == 401
        assert client.post("/api/pause").status_code == 401
        assert client.get("/api/devices").status_code == 401
        assert client.get("/api/translation/targets").status_code == 401
        assert client.put("/api/translation/targets", json={"targets": ["en"]}).status_code == 401


def test_lock_returns_console_to_unauthenticated_state(monkeypatch):
    monkeypatch.setenv("SKC_OPERATOR_PASSWORD", "testpass123")
    monkeypatch.setenv("SKC_SESSION_SECRET", "testsecret")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": "testpass123"})
        assert client.post("/api/pause").status_code == 200

        # Lock console via logout
        logout_res = client.post("/api/auth/logout")
        assert logout_res.status_code == 200

        # Privileged calls rejected again
        assert client.post("/api/pause").status_code == 401
        # /admin HTML renders visible modal again
        admin_res = client.get("/admin")
        assert 'id="auth-modal" class="" style="display: flex;"' in admin_res.text


def test_public_root_remains_attendee():
    with TestClient(app) as client:
        # Accessing root from public domain serves attendee view (/live)
        res = client.get("/", headers={"host": "live.starkvillekoreanchurch.org"}, follow_redirects=True)
        assert res.status_code == 200
        assert "Live Translation" in res.text
        assert "관리자 콘솔" not in res.text


def test_public_admin_route_remains_blocked():
    with TestClient(app) as client:
        # Accessing /admin through public tunnel domain is blocked by PublicHostGuard (404 Not Found)
        res = client.get("/admin", headers={"host": "live.starkvillekoreanchurch.org"})
        assert res.status_code == 404

