"""
tests/test_qr_pipeline.py — Comprehensive QR Pipeline Contract Tests
"""
import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_qr_png_returns_valid_image(client):
    """Verify /api/qr.png returns a valid PNG image with 200 OK and no-cache headers."""
    resp = client.get("/api/qr.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert "no-store" in resp.headers.get("cache-control", "")
    assert len(resp.content) > 500

    # Verify PIL can open and parse it as a valid PNG
    img = Image.open(io.BytesIO(resp.content))
    assert img.format == "PNG"
    assert img.size[0] > 100
    assert img.size[1] > 100


def test_qr_png_supports_types(client):
    """Verify /api/qr.png accepts type=local, type=public, and type=primary."""
    for qr_type in ("primary", "public", "local"):
        resp = client.get(f"/api/qr.png?type={qr_type}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        img = Image.open(io.BytesIO(resp.content))
        assert img.format == "PNG"


def test_qr_png_accessible_without_auth(client, monkeypatch):
    """Verify /api/qr.png does not require operator authentication on local/LAN access."""
    monkeypatch.setenv("SKC_OPERATOR_PASSWORD", "SecretPass123!")
    resp = client.get("/api/qr.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_qr_png_blocked_on_public_host(client):
    """Verify /api/qr.png is blocked (404) when requested through public host."""
    headers = {"Host": "live.starkvillekoreanchurch.org"}
    resp = client.get("/api/qr.png", headers=headers)
    assert resp.status_code == 404

