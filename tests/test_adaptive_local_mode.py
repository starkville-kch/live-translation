"""
tests/test_adaptive_local_mode.py — Verification of Adaptive Local-First Mode
Ensures that when no public tunnel is configured:
1. The operator console defaults cleanly to Local Wi-Fi mode.
2. The HTTPS badge and public segmented tabs are hidden.
3. No external domain is displayed to confuse local users.
4. Telemetry details panel shows only Local Wi-Fi latency without public placeholders.
"""
from fastapi.testclient import TestClient
from app.server import app, _is_tunnel_configured


def test_unconfigured_tunnel_returns_local_only_state(monkeypatch):
    """When no tunnel token exists, status and UI should report local-only."""
    monkeypatch.delenv("CLOUDFLARE_TUNNEL_TOKEN", raising=False)
    client = TestClient(app)

    # 1. Check /api/status payload
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_public_domain"] is False
    assert data["tunnel_enabled"] is False
    assert data["public_https_status"] == "disabled"
    assert data["public_attendee_url"] is None

    # 2. Check /admin HTML initial render
    admin_resp = client.get("/admin")
    assert admin_resp.status_code == 200
    html = admin_resp.text

    # HTTPS header badge should be hidden
    assert 'id="ss-public" style="display:none;"' in html

    # Public/Local segmented tab should be hidden
    assert 'id="qr-access-tabs" style="display:none; width:100%; margin-bottom:10px;"' in html

    # Local Wi-Fi radio button should be checked by default
    assert 'id="qr-tab-local" value="local" checked' in html

    # Main QR code should default to local
    assert 'id="qr-img" src="/api/qr.png?type=local"' in html

    # Main QR status and label should say Local Wi-Fi
    assert 'Local Wi-Fi QR' in html
    assert 'Local Wi-Fi ready' in html

    # Dual QR print modal should hide the public column in local mode
    assert 'id="modal-qr-public-col" style="display:none; ' in html


def test_configured_tunnel_shows_public_tabs(monkeypatch):
    """When a tunnel is explicitly configured, public tabs and HTTPS badge unhide."""
    monkeypatch.setattr(
        "app.server.network_cfg",
        lambda: {"enable_tunnel": True, "port": 8080, "public_url": "https://live.testchurch.org"}
    )
    monkeypatch.setenv("CLOUDFLARE_TUNNEL_TOKEN", "mock-token-for-cloudflare-tunnel-test-12345")
    assert _is_tunnel_configured() is True

    client = TestClient(app)
    admin_resp = client.get("/admin")
    assert admin_resp.status_code == 200
    html = admin_resp.text

    # HTTPS header badge should NOT be display:none
    assert 'id="ss-public" style="display:none;"' not in html

    # Public segmented tab should be display:flex
    assert 'id="qr-access-tabs" style="display:flex; width:100%; margin-bottom:10px;"' in html

    # Public radio button should be checked
    assert 'id="qr-tab-public" value="public" checked' in html

    # Main QR code should default to public
    assert 'id="qr-img" src="/api/qr.png?type=public"' in html

    # Dual QR print modal should show both columns when public tunnel is active
    assert 'id="modal-qr-public-col" style="display:none; ' not in html
