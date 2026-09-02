"""
tests/test_server_multilingual_routes.py — Tests for Multilingual Server Routes & Status
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.audio import AudioCapture
from app.broadcast import CaptionEvent
from app.gemini_session import GeminiSession
from app.server import app, manager, _state, ServiceState


@pytest.fixture(autouse=True)
def reset_server_state():
    with patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.stop())
        yield
        asyncio.run(manager.stop())


def test_api_languages_discovery():
    client = TestClient(app)
    res = client.get("/api/languages")
    assert res.status_code == 200
    data = res.json()

    assert "expected_source" in data
    assert "available" in data
    assert "supported_targets" in data
    assert "selected_targets" in data
    assert "active_targets" in data
    assert len(data["available"]) >= 70
    assert "en" in data["supported_targets"]


def test_stream_routes_validation():
    client = TestClient(app)

    # Explicit invalid language code -> 400
    res_bad = client.get("/stream?lang=invalid_123")
    assert res_bad.status_code == 400
    assert res_bad.json()["error"] == "invalid_language"

    # Explicit inactive target language code -> 404
    res_inactive = client.get("/stream?lang=zh")
    assert res_inactive.status_code == 404
    assert res_inactive.json()["error"] == "target_not_active"


def test_streams_never_cross_outputs():
    # Start manager with targets ['en', 'uk']
    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.start(active_targets=["en", "uk"], expected_source_language="ko"))

        b_en = manager.get_broadcaster("en")
        b_uk = manager.get_broadcaster("uk")

        assert b_en is not None
        assert b_uk is not None

        # Add subscriber queues directly to simulate streams
        q_en = b_en.add_client()
        q_uk = b_uk.add_client()

        # Push Ukrainian caption to Ukrainian broadcaster
        b_uk._push(CaptionEvent(kind="commit", target="Слава Богу", source="하나님께 영광", source_lang="ko", target_lang="uk"))

        # Push English caption to English broadcaster
        b_en._push(CaptionEvent(kind="commit", target="Glory to God", source="하나님께 영광", source_lang="ko", target_lang="en"))

        # UK queue gets Ukrainian event
        ev_uk = q_uk.get_nowait()
        assert ev_uk.target == "Слава Богу"
        assert q_uk.empty()

        # EN queue gets English event
        ev_en = q_en.get_nowait()
        assert ev_en.target == "Glory to God"
        assert q_en.empty()

        b_en.remove_client(q_en)
        b_uk.remove_client(q_uk)


def test_audio_stream_websocket_routing():
    client = TestClient(app)

    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.start(active_targets=["en", "uk"], expected_source_language="ko"))

        # Default connection -> connects to primary ('en')
        with client.websocket_connect("/audio-stream") as ws:
            b_en = manager.get_broadcaster("en")
            assert b_en.audio_client_count == 1
            b_en.on_audio_chunk(b"\x01\x02" * 100)
            data = ws.receive_bytes()
            assert data == b"\x01\x02" * 100
            ws.close()

        # Ukrainian connection -> connects to 'uk'
        with client.websocket_connect("/audio-stream?lang=uk") as ws_uk:
            b_uk = manager.get_broadcaster("uk")
            assert b_uk.audio_client_count == 1
            b_uk.on_audio_chunk(b"\x03\x04" * 100)
            data = ws_uk.receive_bytes()
            assert data == b"\x03\x04" * 100
            ws_uk.close()


def test_target_settings_mutation_and_running_lock():
    client = TestClient(app)

    # When stopped: targets can be updated via PUT /api/translation/targets
    res_get = client.get("/api/translation/targets")
    assert res_get.status_code == 200

    res_put = client.put("/api/translation/targets", json={"targets": ["en", "uk"]})
    assert res_put.status_code == 200
    assert res_put.json()["ok"] is True

    # Start manager
    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.start(active_targets=["en", "uk"]))

        # When running: target change is rejected with 409 Conflict
        res_conflict = client.put("/api/translation/targets", json={"targets": ["en", "zh"]})
        assert res_conflict.status_code == 409
        assert res_conflict.json()["error"] == "translation_running"

        # Pause manager
        asyncio.run(manager.pause_clean())

        # When paused: target change is still rejected with 409 Conflict
        res_conflict_paused = client.put("/api/translation/targets", json={"targets": ["en", "zh"]})
        assert res_conflict_paused.status_code == 409
        assert res_conflict_paused.json()["error"] == "translation_running"

        # Stop manager
        asyncio.run(manager.stop())

        # Now stopped: targets can be updated again
        res_ok_again = client.put("/api/translation/targets", json={"targets": ["en"]})
        assert res_ok_again.status_code == 200


def test_api_status_reports_translation_and_legacy_fields():
    client = TestClient(app)
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()

    # Legacy fields
    assert "service_running" in data
    assert "state" in data
    assert "telemetry" in data
    assert "session" in data
    assert "audio" in data

    # New multilingual translation block
    assert "translation" in data
    t = data["translation"]
    assert "expected_source" in t
    assert "selected_targets" in t
    assert "active_targets" in t
    assert "primary_target" in t
    assert "sessions" in t


def test_public_host_guard_allows_api_languages_and_blocks_targets():
    client = TestClient(app)

    # Simulated public host header
    pub_headers = {"Host": "live.starkvillekoreanchurch.org"}

    # /api/languages should be allowed for attendees
    res_lang = client.get("/api/languages", headers=pub_headers)
    assert res_lang.status_code == 200

    # /api/translation/targets should be blocked (404 / denied on public host)
    res_targets = client.get("/api/translation/targets", headers=pub_headers)
    assert res_targets.status_code == 404


def test_status_default_drift_correction_is_manual_before_start():
    client = TestClient(app)
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert data["auto_drift_correction"] is False


def test_signal_shutdown_terminates_active_streams_cleanly():
    """
    Verify that when signal_shutdown() is called:
    - shutdown_event is set.
    - _sse_generator exits cleanly without hanging or raising CancelledError.
    - Target broadcaster removes the client queue cleanly.
    """
    from app.server import shutdown_event, signal_shutdown, _sse_generator
    from unittest.mock import MagicMock

    shutdown_event.clear()
    assert not shutdown_event.is_set()

    q = asyncio.Queue()
    mock_b = MagicMock()
    mock_req = MagicMock()
    mock_req.is_disconnected = AsyncMock(return_value=False)

    async def run_test():
        gen = _sse_generator(mock_req, q, mock_b)
        await q.put(CaptionEvent(kind="ping"))
        item = await gen.__anext__()
        assert "ping" in item

        # Trigger shutdown
        signal_shutdown()
        assert shutdown_event.is_set()

        # Next iteration should terminate voluntarily (StopAsyncIteration)
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

        mock_b.remove_client.assert_called_once_with(q)

    asyncio.run(run_test())
    shutdown_event.clear()


