"""
tests/test_attendee_multilingual.py — Tests for Attendee Multilingual UI & Stream Binding
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.audio import AudioCapture
from app.broadcast import CaptionEvent
from app.gemini_session import GeminiSession
from app.server import app, manager


@pytest.fixture(autouse=True)
def reset_manager_state():
    with patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.stop())
        yield
        asyncio.run(manager.stop())


def test_attendee_html_structure():
    client = TestClient(app)
    res = client.get("/live")
    assert res.status_code == 200
    html = res.text

    # 1. Header language selector wrapper
    assert 'id="language-selector-wrap"' in html
    assert 'id="target-lang-select"' in html

    # 2. Language notice container
    assert 'id="lang-notice"' in html
    assert 'id="lang-notice-text"' in html

    # 3. Multilingual JS initialization
    assert 'initLanguageSelection' in html
    assert 'switchLanguage' in html
    assert 'clearCurrentTranslationView' in html
    assert 'target_lang' in html


def test_attendee_sse_stream_receives_target_events():
    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.start(active_targets=["en", "uk"], expected_source_language="ko"))

        b_uk = manager.get_broadcaster("uk")
        assert b_uk is not None

        q = b_uk.add_client()
        b_uk._push(CaptionEvent(
            kind="commit",
            target="Благодать Господа",
            source="주님의 은혜",
            source_lang="ko",
            target_lang="uk",
        ))

        ev = q.get_nowait()
        assert ev.target == "Благодать Господа"
        assert ev.source == "주님의 은혜"
        assert ev.source_lang == "ko"
        assert ev.target_lang == "uk"
        # Legacy aliases
        assert ev.text == "Благодать Господа"
        assert ev.ko == "주님의 은혜"

        b_uk.remove_client(q)


def test_telemetry_with_target_lang():
    client = TestClient(app)

    with client.websocket_connect("/ws/telemetry") as ws:
        # Ping/Pong
        ws.send_json({"type": "latency_ping", "client_sent_ms": 1000.0})
        resp = ws.receive_json()
        assert resp["type"] == "latency_pong"

        # Report with target_lang
        ws.send_json({
            "type": "latency_report",
            "client_id": "test_client_uk",
            "hostname": "localhost",
            "rtt_ms": 45,
            "target_lang": "uk",
        })

        ws.close()

    # Verify primary broadcaster recorded the telemetry
    stats = manager.primary_broadcaster.get_telemetry_stats()
    assert stats["local_listeners"] >= 1
    assert "listeners_by_target" in stats
    assert stats["listeners_by_target"].get("uk", 0) >= 1
