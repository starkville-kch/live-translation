"""
tests/test_operator_ui.py — Tests for Operator Console UI & Status Lifecycle
"""
import asyncio
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.server import app, _state, ServiceState
import app.server as server_mod


def test_operator_html_structure_and_accessibility():
    client = TestClient(app)
    resp = client.get("/admin")
    assert resp.status_code == 200
    html = resp.text

    # 1. Check fixed 3-element control bar IDs
    assert 'id="service-status-pill"' in html
    assert 'id="btn-primary-action"' in html
    assert 'id="btn-stop"' in html

    # 2. Check accessibility: aria-live, role=status, and prefers-reduced-motion CSS
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert ".service-status-pill.status-paused" in html
    assert "animation: none" in html

    # 3. Check neutral disabled stop button styling
    assert ".btn-action.btn-stop:disabled" in html
    assert "var(--color-warm-100)" in html

    # 4. Check pause subtitle string
    assert "Resume 필요" in html

    # 5. Check calm static running state (no pulse animation on green dot)
    assert ".service-status-pill.status-running .status-dot" in html
    assert "pulse-dot" not in html

    # 6. Check Top Navigation Bar: ss-internet is removed, other status pills present
    assert 'id="ss-internet"' not in html
    assert 'id="ss-audio"' in html
    assert 'id="ss-gemini"' in html
    assert 'id="ss-translation"' in html

    # 7. Check Status Monitor Card: Gemini session row is removed, audio input is single-line with ellipsis
    assert 'id="stat-session"' not in html
    assert '<span class="sg-label sg-wide tooltip">Gemini 세션' not in html
    assert 'id="stat-audio"' in html
    assert "#stat-audio" in html
    assert "text-overflow: ellipsis" in html

    # 8. Check Inline Model Controls, Segmented Pill Drift Control, Playback button, and Dashboard Metrics
    assert 'id="btn-refresh-devices"' in html
    assert 'id="model-select"' in html
    assert 'id="stat-model-status"' in html
    assert 'id="btn-test-selected-model"' in html
    assert 'id="drift-manual"' in html
    assert 'id="drift-auto"' in html
    assert 'id="btn-audio"' in html
    assert 'id="stat-latency"' in html
    assert 'id="stat-attendees"' in html
    assert 'id="stat-cost"' in html


def test_api_devices_and_rescan():
    client = TestClient(app)
    # Default listing
    resp = client.get("/api/devices")
    assert resp.status_code == 200
    devices = resp.json()
    assert isinstance(devices, list)

    # Rescan listing
    resp_rescan = client.get("/api/devices?rescan=true")
    assert resp_rescan.status_code == 200
    devices_rescan = resp_rescan.json()
    assert isinstance(devices_rescan, list)


def test_api_status_includes_pause_duration():
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()

    assert "service_running" in data
    assert "state" in data
    assert "paused" in data
    assert "pause_duration_s" in data
    assert isinstance(data["pause_duration_s"], (int, float))


def test_pause_resume_status_lifecycle():
    client = TestClient(app)

    # Simulate running service state
    server_mod._state = ServiceState.RUNNING
    server_mod._paused = False
    server_mod._pause_start = None

    # Status while active
    st_run = client.get("/api/status").json()
    assert st_run["service_running"] is True
    assert st_run["paused"] is False
    assert st_run["pause_duration_s"] == 0.0

    # Pause
    resp_pause = client.post("/api/pause")
    assert resp_pause.status_code == 200
    assert resp_pause.json()["paused"] is True

    # Status while paused
    st_paused = client.get("/api/status").json()
    assert st_paused["paused"] is True
    assert st_paused["pause_duration_s"] >= 0.0

    # Resume
    resp_resume = client.post("/api/resume")
    assert resp_resume.status_code == 200
    assert resp_resume.json()["paused"] is False

    # Status after resume
    st_resumed = client.get("/api/status").json()
    assert st_resumed["paused"] is False
    assert st_resumed["pause_duration_s"] == 0.0

    # Reset state
    server_mod._state = ServiceState.STOPPED
    server_mod._paused = False
    server_mod._pause_start = None
