"""
tests/test_operator_multilingual.py — Tests for Operator Multi-Target Language UI & API
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.audio import AudioCapture
from app.broadcast import CaptionEvent
from app.gemini_session import GeminiSession, SessionStatus
from app.server import app, manager


@pytest.fixture(autouse=True)
def reset_manager_state():
    with patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.stop())
        yield
        asyncio.run(manager.stop())


def test_operator_html_language_panel_structure():
    client = TestClient(app)
    res = client.get("/admin")
    assert res.status_code == 200
    html = res.text

    # 1. Right-rail language targets card
    assert 'id="card-languages"' in html
    assert 'id="lang-panel-badge"' in html
    assert 'id="lang-source-name"' in html
    assert 'id="lang-targets-config-list"' in html
    assert 'id="lang-targets-active-list"' in html
    assert 'id="btn-open-manage-langs"' in html

    # 2. Manage Languages modal
    assert 'id="manage-langs-modal"' in html
    assert 'id="manage-langs-search"' in html
    assert 'id="manage-langs-catalog-list"' in html
    assert 'id="btn-save-manage-langs"' in html

    # 3. Bilingual labels exist
    assert 'data-lang="ko"' in html
    assert 'data-lang="en"' in html
    assert '통역 언어 (Languages)' in html
    assert 'Translation Languages' in html


def test_target_configuration_lifecycle_and_lock():
    client = TestClient(app)

    # 1. While stopped: query targets
    res_get = client.get("/api/translation/targets")
    assert res_get.status_code == 200
    data_get = res_get.json()
    assert "supported_targets" in data_get
    assert "selected_targets" in data_get
    assert data_get["is_running"] is False

    # 2. While stopped: update targets
    res_put = client.put("/api/translation/targets", json={
        "supported_targets": ["en", "uk", "zh", "es"],
        "targets": ["en", "uk"],
    })
    assert res_put.status_code == 200
    data_put = res_put.json()
    assert data_put["ok"] is True
    assert "es" in data_put["translation"]["supported_targets"]
    assert data_put["translation"]["default_active_targets"] == ["en", "uk"]

    # 3. Start service with active targets
    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.start(active_targets=["en", "uk"], expected_source_language="ko"))

        # While running: mutation returns 409 Conflict
        res_conflict = client.put("/api/translation/targets", json={"targets": ["en", "zh"]})
        assert res_conflict.status_code == 409
        assert res_conflict.json()["error"] == "translation_running"

        # Pause service
        asyncio.run(manager.pause_clean())

        # While paused: mutation still returns 409 Conflict
        res_conflict_paused = client.put("/api/translation/targets", json={"targets": ["en", "zh"]})
        assert res_conflict_paused.status_code == 409
        assert res_conflict_paused.json()["error"] == "translation_running"

        # Stop service
        asyncio.run(manager.stop())

        # While stopped: mutation succeeds again
        res_ok = client.put("/api/translation/targets", json={"targets": ["en"]})
        assert res_ok.status_code == 200


def test_per_target_status_and_failure_isolation():
    client = TestClient(app)

    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        res_start = client.post("/api/start", json={"targets": ["en", "uk"]})
        assert res_start.status_code == 200

        sess_en = manager.sessions.get("en")
        sess_uk = manager.sessions.get("uk")
        assert sess_en is not None
        assert sess_uk is not None

        sess_en._emit(status=SessionStatus.CONNECTED, last_latency_ms=742.0)
        sess_uk._emit(status=SessionStatus.RECONNECTING, last_latency_ms=0.0)

        # Fetch status
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()

        assert "translation" in data
        t = data["translation"]
        assert t["active_targets"] == ["en", "uk"]
        assert t["sessions"]["en"]["status"] == "connected"
        assert t["sessions"]["en"]["latency_ms"] == 742.0
        assert t["sessions"]["uk"]["status"] == "reconnecting"

        # Overall service is still running (not globally failed)
        assert data["service_running"] is True

        client.post("/api/stop")




def test_public_host_denies_target_configuration():
    client = TestClient(app)
    pub_headers = {"Host": "live.starkvillekoreanchurch.org"}

    # Public host cannot modify target configuration
    res = client.put("/api/translation/targets", json={"targets": ["en", "uk"]}, headers=pub_headers)
    assert res.status_code == 404
