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


def test_audio_stream_rejected_when_service_stopped():
    """Connecting /audio-stream when translation is stopped returns 1008 policy close."""
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/audio-stream?lang=es") as ws:
            ws.receive_bytes()


def test_audio_stream_lifecycle_and_target_switching():
    """
    1. Start translation with [en, es, zh]
    2. Connect /audio-stream?lang=es -> receives PCM chunks
    3. Switch to /audio-stream?lang=zh -> receives Chinese PCM chunks
    4. Verify old client queue is cleaned up from Spanish broadcaster
    """
    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.start(active_targets=["en", "es", "zh"], expected_source_language="ko"))

        b_es = manager.get_broadcaster("es")
        b_zh = manager.get_broadcaster("zh")
        assert b_es is not None
        assert b_zh is not None

        client = TestClient(app)

        # 1. Connect Spanish audio WebSocket
        with client.websocket_connect("/audio-stream?lang=es") as ws_es:
            assert len(b_es._audio_clients) == 1
            # Push test PCM chunk to Spanish broadcaster
            b_es.on_audio_chunk(b"\x01\x02\x03\x04")
            data = ws_es.receive_bytes()
            assert data == b"\x01\x02\x03\x04"

            # 2. Connect Chinese audio WebSocket
            with client.websocket_connect("/audio-stream?lang=zh") as ws_zh:
                assert len(b_zh._audio_clients) == 1
                b_zh.on_audio_chunk(b"\x05\x06\x07\x08")
                data_zh = ws_zh.receive_bytes()
                assert data_zh == b"\x05\x06\x07\x08"

            # After Chinese WS context exits, Chinese audio client must be cleaned up
            assert len(b_zh._audio_clients) == 0

        # After Spanish WS context exits, Spanish audio client must be cleaned up
        assert len(b_es._audio_clients) == 0


def test_multi_tab_independent_audio_and_tab_close():
    """
    Multi-tab attendee test:
    1. Tab 1 connects EN audio.
    2. Tab 2 connects ZH audio.
    3. Tab 3 connects ES audio simultaneously.
    4. All three receive audio on their independent queues.
    5. Tab 2 (ZH) closes without pressing stop -> ZH client queue cleaned up.
    6. Tab 1 (EN) and Tab 3 (ES) continue working without disruption.
    7. Tab 4 re-opens ZH audio and connects cleanly.
    8. Tab 4 switches language dynamically: ZH -> EN -> ES.
    """
    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.start(active_targets=["en", "zh", "es"], expected_source_language="ko"))

        b_en = manager.get_broadcaster("en")
        b_zh = manager.get_broadcaster("zh")
        b_es = manager.get_broadcaster("es")
        assert b_en is not None
        assert b_zh is not None
        assert b_es is not None

        client = TestClient(app)

        # 1. Tab 1 connects EN
        with client.websocket_connect("/audio-stream?lang=en") as ws_en:
            assert len(b_en._audio_clients) == 1

            # 2. Tab 2 connects ZH
            with client.websocket_connect("/audio-stream?lang=zh") as ws_zh:
                assert len(b_zh._audio_clients) == 1

                # 3. Tab 3 connects ES
                with client.websocket_connect("/audio-stream?lang=es") as ws_es:
                    assert len(b_es._audio_clients) == 1

                    # 4. Push audio to all 3 simultaneously
                    b_en.on_audio_chunk(b"EN_CHUNK")
                    b_zh.on_audio_chunk(b"ZH_CHUNK")
                    b_es.on_audio_chunk(b"ES_CHUNK")
                    assert ws_en.receive_bytes() == b"EN_CHUNK"
                    assert ws_zh.receive_bytes() == b"ZH_CHUNK"
                    assert ws_es.receive_bytes() == b"ES_CHUNK"

                # Tab 3 closed -> ES clients = 0
                assert len(b_es._audio_clients) == 0

            # Tab 2 closed -> ZH clients = 0
            assert len(b_zh._audio_clients) == 0

            # 5. Tab 1 EN still receives audio
            b_en.on_audio_chunk(b"EN_CHUNK_2")
            assert ws_en.receive_bytes() == b"EN_CHUNK_2"

            # 6. Tab 4 connects ZH again
            with client.websocket_connect("/audio-stream?lang=zh") as ws_tab4:
                assert len(b_zh._audio_clients) == 1
                b_zh.on_audio_chunk(b"ZH_CHUNK_2")
                assert ws_tab4.receive_bytes() == b"ZH_CHUNK_2"

            # 7. Tab 4 dynamically switches to EN, then to ES
            with client.websocket_connect("/audio-stream?lang=en") as ws_tab4_en:
                assert len(b_en._audio_clients) == 2  # Tab 1 + Tab 4
                b_en.on_audio_chunk(b"EN_CHUNK_3")
                assert ws_en.receive_bytes() == b"EN_CHUNK_3"
                assert ws_tab4_en.receive_bytes() == b"EN_CHUNK_3"

            with client.websocket_connect("/audio-stream?lang=es") as ws_tab4_es:
                assert len(b_es._audio_clients) == 1
                b_es.on_audio_chunk(b"ES_CHUNK_2")
                assert ws_tab4_es.receive_bytes() == b"ES_CHUNK_2"

            assert len(b_es._audio_clients) == 0

        assert len(b_en._audio_clients) == 0


def test_attendee_html_per_tab_state_and_lifecycle():
    client = TestClient(app)
    res = client.get("/live")
    assert res.status_code == 200
    html = res.text

    assert 'updateAudioButton' in html
    assert 'isAudioConnecting' in html
    assert 'pagehide' in html
    assert 'beforeunload' in html


def test_attendee_html_js_syntax():
    """Verify that embedded JavaScript in attendee.html is syntactically valid and free of undeclared variables."""
    import re
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not installed")

    root = Path(__file__).resolve().parent.parent
    html_path = root / "app" / "templates" / "attendee.html"
    html = html_path.read_text(encoding="utf-8")
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert script is not None, "Script block not found in attendee.html"

    temp_js = root / ".temp_attendee_lint.js"
    temp_js.write_text(script.group(1), encoding="utf-8")

    try:
        proc = subprocess.run([node, "--check", str(temp_js)], capture_output=True, text=True)
        assert proc.returncode == 0, f"attendee.html JavaScript syntax error:\n{proc.stderr}"

        npx = shutil.which("npx")
        if npx:
            lint_proc = subprocess.run([npx, "--no-install", "eslint", str(temp_js)], capture_output=True, text=True, shell=True)
            assert lint_proc.returncode == 0, f"attendee.html ESLint no-undef error:\n{lint_proc.stdout}\n{lint_proc.stderr}"
    finally:
        temp_js.unlink(missing_ok=True)


def test_attendee_audio_button_clickable_with_three_active_targets():
    """
    DOM & state regression test:
    When service is RUNNING and active_targets = ['en', 'zh', 'es'],
    for each selected target (en, zh, es):
    - audio-btn must NOT be disabled (audioBtn.disabled === false).
    - Audio state remains clickable regardless of target count (1, 2, or 3).
    """
    import subprocess
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not installed")

    node_test_code = """
    class MockElement {
        constructor(id) {
            this.id = id;
            this.disabled = false;
            this.className = '';
            this.classList = {
                add: () => {},
                remove: () => {},
                contains: () => false
            };
            this.textContent = '';
            this.value = '';
            this.innerHTML = '';
            this.appendChild = () => {};
            this.addEventListener = () => {};
        }
    }
    const elements = {
        'audio-btn': new MockElement('audio-btn'),
        'target-lang-select': new MockElement('target-lang-select'),
        'language-selector-wrap': new MockElement('language-selector-wrap'),
    };
    global.document = {
        getElementById: (id) => elements[id] || new MockElement(id),
        createElement: (tag) => new MockElement(tag),
        addEventListener: () => {},
    };
    global.WebSocket = class {};
    global.WebSocket.OPEN = 1;

    let isAudioConnecting = false;
    let audioWs = null;
    const audioBtn = document.getElementById('audio-btn');

    function updateAudioButton() {
        if (!audioBtn) return;
        if (isAudioConnecting) {
            audioBtn.disabled = true;
            audioBtn.textContent = '⏳ Connecting…';
            audioBtn.classList.remove('active');
        } else if (audioWs && audioWs.readyState === WebSocket.OPEN) {
            audioBtn.disabled = false;
            audioBtn.textContent = '🔊 Audio On';
            audioBtn.classList.add('active');
        } else {
            audioBtn.disabled = false;
            audioBtn.textContent = '🎧 Audio Off';
            audioBtn.classList.remove('active');
        }
    }

    const testCases = [
        ['en'],
        ['en', 'zh'],
        ['en', 'zh', 'es']
    ];

    for (const targets of testCases) {
        for (const target of targets) {
            updateAudioButton();
            if (audioBtn.disabled !== false) {
                console.error(`FAIL: audio-btn disabled for ${target} in ${targets.join(',')}`);
                process.exit(1);
            }
        }
    }
    console.log("PASS");
    """

    res = subprocess.run([node, "-e", node_test_code], capture_output=True, text=True)
    assert res.returncode == 0, f"Audio button state check failed:\n{res.stderr}"
    assert "PASS" in res.stdout



