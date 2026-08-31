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
