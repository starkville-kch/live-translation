"""Exercise real setup widgets without starting services, downloads, or API calls."""
from pathlib import Path
import threading
import time
import tkinter as tk

import pytest

from app.cloudflared_service import CloudflaredService
from app.config import DEFAULT_CONFIG, _load
from setup_gui import COLOR_GOLD, COLOR_SUCCESS, SetupApp


class LocalService(CloudflaredService):
    def __init__(self):
        super().__init__()
        self.token = None
        self.queries = 0

    def get_tunnel_token(self):
        return self.token

    def _query(self):
        self.queries += 1
        return "stopped"


class LocalSetup(SetupApp):
    binary: Path | None = None

    def _find_cloudflared(self):
        return self.binary


@pytest.fixture
def setup():
    root = tk.Tk()
    root.withdraw()
    service = LocalService()
    app = LocalSetup(root, cloudflared_service=service)
    app.entry_public_url.delete(0, tk.END)
    app.existing_key = "fixture-api-key"
    app._update_readiness_pills()
    try:
        yield app, service
    finally:
        root.destroy()


def test_public_settings_start_folded_and_toggling_preserves_values(setup):
    app, service = setup
    assert app.public_https_body.winfo_manager() == ""
    assert "(Advanced)" in app.btn_public_https.cget("text")
    assert service.queries == 0
    app.entry_public_url.insert(0, "https://captions.example.invalid")
    app.btn_public_https.invoke()
    assert app.public_https_body.winfo_manager() == "pack"
    deadline = time.monotonic() + 2
    while app._service_check_pending and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.01)
    assert not app._service_check_pending
    assert service.queries == 1
    app.btn_public_https.invoke()
    assert app.public_https_body.winfo_manager() == ""
    assert app.entry_public_url.get() == "https://captions.example.invalid"
    assert service.queries == 1


def test_expand_stays_responsive_during_slow_service_check():
    class SlowService(LocalService):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def _query(self):
            self.queries += 1
            self.started.set()
            if not self.release.wait(3):
                raise TimeoutError("Test did not release service query")
            return "running"

    root = tk.Tk()
    root.withdraw()
    service = SlowService()
    app = LocalSetup(root, cloudflared_service=service)
    try:
        before = time.monotonic()
        app.btn_public_https.invoke()
        assert time.monotonic() - before < 0.5
        assert service.started.wait(1)
        root.update()
        assert app._service_check_pending
        assert app.lbl_svc_status.cget("text") == "Checking..."
        assert app.btn_refresh_svc.instate(["disabled"])
        app._refresh_service_status()
        app.btn_public_https.invoke()
        app.btn_public_https.invoke()
        assert service.queries == 1
        service.release.set()
        deadline = time.monotonic() + 2
        while app._service_check_pending and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert not app._service_check_pending
        assert "RUNNING" in app.lbl_svc_status.cget("text")
        assert app.btn_refresh_svc.instate(["!disabled"])
        assert app._cloudflared_state == "running"
    finally:
        service.release.set()
        root.destroy()


def test_expanded_controls_are_reachable_at_minimum_window_size(setup):
    app, _ = setup
    app.root.geometry("980x650")
    app.root.deiconify()
    app.btn_public_https.invoke()
    app.root.update()
    app.root.update_idletasks()
    canvas = app.content_canvas
    content_bounds = canvas.bbox("all")
    assert content_bounds is not None
    assert content_bounds[3] > canvas.winfo_height()
    canvas.yview_moveto(1)
    app.root.update_idletasks()
    token = app.entry_tunnel_token
    assert token.winfo_rooty() >= canvas.winfo_rooty()
    assert token.winfo_rooty() + token.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()
    assert app.btn_save.winfo_rooty() + app.btn_save.winfo_height() <= app.root.winfo_rooty() + app.root.winfo_height()
    help_label = app.lbl_password_help
    assert help_label.winfo_height() >= help_label.winfo_reqheight()
    assert help_label.winfo_width() >= int(help_label.cget("wraplength"))
    assert help_label.master == app.entry_operator_password.master.master
    assert "local operator console" in help_label.cget("text")


def test_local_readiness_hides_optional_tunnel_and_invalidates_edited_key(setup):
    app, service = setup
    assert app.lbl_pill_cloudflare.winfo_manager() == ""
    assert "Local URL set" in app.lbl_pill_local.cget("text")
    assert "API key set" in app.lbl_pill_gemini.cget("text")
    assert app.lbl_pill_gemini.cget("fg") == COLOR_GOLD
    app.validation_passed = True
    app._update_readiness_pills()
    assert "Gemini verified" in app.lbl_pill_gemini.cget("text")
    app._on_key_changed()
    assert not app.validation_passed
    assert app.lbl_pill_gemini.cget("fg") == COLOR_GOLD
    assert service.queries == 0


def test_configured_tunnel_does_not_claim_running_until_checked(setup, tmp_path):
    app, service = setup
    app.entry_public_url.insert(0, "https://captions.example.invalid")
    app._update_readiness_pills()
    assert app.lbl_pill_cloudflare.winfo_manager() == ""
    service.token = "fixture-tunnel-token"
    app._update_readiness_pills()
    assert "download needed" in app.lbl_pill_cloudflare.cget("text")
    app.binary = tmp_path / "cloudflared.exe"
    app.binary.write_bytes(b"fixture")
    app._update_readiness_pills()
    assert "HTTPS configured" in app.lbl_pill_cloudflare.cget("text")
    assert app.lbl_pill_cloudflare.cget("fg") == COLOR_GOLD
    app._cloudflared_state = "running"
    app._update_readiness_pills()
    assert "Tunnel running" in app.lbl_pill_cloudflare.cget("text")
    assert app.lbl_pill_cloudflare.cget("fg") == COLOR_SUCCESS
    app.entry_public_url.delete(0, tk.END)
    app._update_readiness_pills()
    assert app.lbl_pill_cloudflare.winfo_manager() == ""
    assert service.queries == 0


def test_new_configuration_is_local_only(tmp_path):
    assert DEFAULT_CONFIG["internet"] == {"enable_tunnel": False, "public_url": ""}
    settings = _load(tmp_path / "new-config.yaml")
    assert settings["internet"] == DEFAULT_CONFIG["internet"]


def test_existing_public_configuration_is_preserved(tmp_path):
    path = tmp_path / "existing.yaml"
    original = b'internet:\n  enable_tunnel: true\n  public_url: https://captions.example.invalid\n'
    path.write_bytes(original)
    assert _load(path)["internet"] == {"enable_tunnel": True, "public_url": "https://captions.example.invalid"}
    assert path.read_bytes() == original
