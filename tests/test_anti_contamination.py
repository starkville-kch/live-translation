"""
tests/test_anti_contamination.py — Unit Tests for Anti-Contamination & Clean Session Resets
"""
import asyncio
import collections
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.audio import AudioCapture
from app.broadcast import CaptionBroadcaster, CaptionEvent
from app.gemini_session import (
    GeminiSession,
    SessionStatus,
    evaluate_drift_score,
)
from app.model_resolver import model_resolver


def test_audio_frames_are_dropped_while_paused():
    async def _run():
        cap = AudioCapture()
        cap._loop = asyncio.get_running_loop()

        # Normal active: frames enqueued
        await cap._enqueue(b"\x01\x00" * 160)
        assert not cap._queue.empty()
        assert await cap._queue.get() == b"\x01\x00" * 160

        # Paused: frames discarded immediately
        cap.pause()
        assert cap.is_paused is True
        await cap._enqueue(b"\x02\x00" * 160)
        assert cap._queue.empty()

        # Drain helper
        cap.resume()
        await cap._enqueue(b"\x03\x00" * 160)
        assert not cap._queue.empty()
        cap.drain()
        assert cap._queue.empty()

    asyncio.run(_run())


def test_language_code_ko_allowed():
    # Korean input, English output -> score 0
    score = evaluate_drift_score(
        input_lang="ko",
        input_text="하나님의 은혜로",
        output_lang="en",
        output_text="By the grace of God",
    )
    assert score == 0

    score_variant = evaluate_drift_score(
        input_lang="ko-KR",
        input_text="말씀",
        output_lang="en-US",
        output_text="The Word",
    )
    assert score_variant == 0


def test_language_code_en_allowed():
    # Bilingual English input, English output -> score 0
    score = evaluate_drift_score(
        input_lang="en",
        input_text="And welcome to our church today",
        output_lang="en",
        output_text="And welcome to our church today",
    )
    assert score == 0


def test_language_code_ja_increments_drift():
    # Japanese input code flagged -> score +1
    score = evaluate_drift_score(
        input_lang="ja",
        input_text="それでは皆さん",
        output_lang="en",
        output_text="Well then everyone",
    )
    assert score == 1


def test_language_code_vi_increments_drift():
    # Vietnamese input code flagged -> score +1
    score = evaluate_drift_score(
        input_lang="vi",
        input_text="Xin chào",
        output_lang="en",
        output_text="Hello",
    )
    assert score == 1


def test_output_language_code_takes_priority():
    # Output language code not English -> score +2
    score = evaluate_drift_score(
        input_lang="ko",
        input_text="말씀",
        output_lang="ja",
        output_text="みことば",
    )
    assert score == 2


def test_missing_language_code_uses_script_fallback():
    # Japanese Hiragana script detected when language_code is missing -> score +1
    score = evaluate_drift_score(
        input_lang=None,
        input_text="こんにちは",
        output_lang="en",
        output_text="Hello",
    )
    assert score == 1

    # Normal Korean text without language_code -> score 0
    score_ko = evaluate_drift_score(
        input_lang=None,
        input_text="오늘 말씀은",
        output_lang="en",
        output_text="Today's sermon is",
    )
    assert score_ko == 0


def test_unexpected_output_language_increments_drift():
    # Output language not English -> score +2
    score = evaluate_drift_score(
        input_lang="ko",
        input_text="사랑합니다",
        output_lang="ja",
        output_text="愛しています",
    )
    assert score == 2


def test_pause_does_not_unlock_model():
    async def _run():
        model_resolver.lock_session("gemini-3.5-live-translate-preview")
        assert model_resolver.locked_model == "gemini-3.5-live-translate-preview"

        session = GeminiSession(on_caption=lambda c: None)
        await session.pause_clean()

        # Model remains locked across pause
        assert model_resolver.locked_model == "gemini-3.5-live-translate-preview"
        assert session._resumption_handle is None

        model_resolver.unlock_session()

    asyncio.run(_run())


def test_resume_reuses_locked_model():
    async def _run():
        model_resolver.lock_session("gemini-3.5-live-translate-preview")

        session = GeminiSession(on_caption=lambda c: None)
        session._run_with_retry = AsyncMock()

        await session.resume_clean()

        # Session started with locked model intact
        assert model_resolver.locked_model == "gemini-3.5-live-translate-preview"
        assert session.session_epoch == 1
        session._run_with_retry.assert_called_once_with(is_clean_resume=True)

        model_resolver.unlock_session()

    asyncio.run(_run())


def test_clean_resume_increments_session_epoch():
    async def _run():
        session = GeminiSession(on_caption=lambda c: None)
        session._run_with_retry = AsyncMock()

        initial_epoch = session.session_epoch
        await session.start()
        assert session.session_epoch == initial_epoch + 1

        await session.pause_clean()
        await session.resume_clean()
        assert session.session_epoch == initial_epoch + 2

    asyncio.run(_run())


def test_stale_old_session_transcript_is_discarded():
    async def _run():
        captions = []
        sources = []
        session = GeminiSession(
            on_caption=lambda c: captions.append(c),
            on_source_transcript=lambda s: sources.append(s),
        )

        # Set epoch to 5
        session._session_epoch = 5

        class MockPart:
            inline_data = None

        class MockModelTurn:
            parts = []

        class MockInputTrans:
            text = "이전 세션 자막"
            language_code = "ko"

        class MockOutputTrans:
            text = "Previous session text"
            language_code = "en"

        class MockServerContent:
            model_turn = MockModelTurn()
            input_transcription = MockInputTrans()
            output_transcription = MockOutputTrans()
            turn_complete = False

        class MockResponse:
            session_resumption_update = None
            go_away = None
            server_content = MockServerContent()
            text = "Previous session text"

        class MockLiveSession:
            async def receive(self):
                yield MockResponse()

        # Call _recv_loop with stale epoch 4
        await session._recv_loop(MockLiveSession(), "gemini-3.5-live-translate-preview", epoch=4)

        # All stale outputs were rejected and not dispatched
        assert captions == []
        assert sources == []
        assert session._current_ko == ""
        assert session._current_en == ""

    asyncio.run(_run())


def test_stale_old_session_audio_is_discarded():
    async def _run():
        audio_chunks = []
        session = GeminiSession(
            on_caption=lambda c: None,
            on_audio_chunk=lambda a: audio_chunks.append(a),
        )

        session._session_epoch = 10

        class MockInlineData:
            data = b"\x00\xFF" * 100

        class MockPart:
            inline_data = MockInlineData()

        class MockModelTurn:
            parts = [MockPart()]

        class MockServerContent:
            model_turn = MockModelTurn()
            input_transcription = None
            output_transcription = None
            turn_complete = False

        class MockResponse:
            session_resumption_update = None
            go_away = None
            server_content = MockServerContent()
            text = ""

        class MockLiveSession:
            async def receive(self):
                yield MockResponse()

        # Call _recv_loop with stale epoch 9
        await session._recv_loop(MockLiveSession(), "gemini-3.5-live-translate-preview", epoch=9)

        # Stale audio chunk dropped
        assert audio_chunks == []

    asyncio.run(_run())


def test_drift_scored_on_completed_turns_only_with_rolling_window():
    session = GeminiSession(on_caption=lambda c: None)
    session._auto_drift_correction = False

    # Turn 1: ko (0) -> clean
    session._current_ko = "말씀을 나누겠습니다"
    session._current_en = "Let us share the Word"
    session._turn_in_lang = "ko"
    session._turn_out_lang = "en"
    session._commit_current_turn()
    assert list(session._drift_history) == [0]

    # Turn 2: ja (+1)
    session._current_ko = "はい、皆さん"
    session._current_en = "Yes, everyone"
    session._turn_in_lang = "ja"
    session._turn_out_lang = "en"
    session._commit_current_turn()
    assert list(session._drift_history) == [0, 1]

    # Turn 3: ko (0)
    session._current_ko = "다시 한국어 말씀"
    session._current_en = "Korean sermon again"
    session._turn_in_lang = "ko"
    session._turn_out_lang = "en"
    session._commit_current_turn()
    assert list(session._drift_history) == [0, 1, 0]

    # Turn 4: ko (0) -> 2 consecutive clean turns clear the drift deque!
    session._current_ko = "두번째 한국어 말씀"
    session._current_en = "Second Korean sermon"
    session._turn_in_lang = "ko"
    session._turn_out_lang = "en"
    session._commit_current_turn()
    assert len(session._drift_history) == 0


def test_auto_drift_correction_off_does_not_reset():
    session = GeminiSession(on_caption=lambda c: None)
    session._auto_drift_correction = False
    session.reset_clean = AsyncMock()

    # 3 consecutive ja turns (+1, +1, +1 = 3)
    for i in range(3):
        session._current_ko = f"日本語テキスト {i}"
        session._current_en = f"Japanese text {i}"
        session._turn_in_lang = "ja"
        session._turn_out_lang = "en"
        session._commit_current_turn()

    # Reset was NOT triggered automatically because option is OFF
    session.reset_clean.assert_not_called()
    assert "비정상 언어 감지" in session.state.last_event


def test_auto_drift_correction_on_triggers_clean_reset():
    async def _run():
        session = GeminiSession(on_caption=lambda c: None)
        session._auto_drift_correction = True
        session._run_with_retry = AsyncMock()

        initial_epoch = session.session_epoch

        # 3 consecutive ja turns (+1, +1, +1 = 3)
        for i in range(3):
            session._current_ko = f"日本語テキスト {i}"
            session._current_en = f"Japanese text {i}"
            session._turn_in_lang = "ja"
            session._turn_out_lang = "en"
            session._commit_current_turn()

        # Allow spawned reset_clean task to run
        await asyncio.sleep(0.1)

        # Epoch was incremented via automatic clean reset
        assert session.session_epoch == initial_epoch + 1

    asyncio.run(_run())


def test_drain_audio_clients():
    async def _run():
        broadcaster = CaptionBroadcaster()
        q1 = broadcaster.add_audio_client()
        q2 = broadcaster.add_audio_client()

        broadcaster.on_audio_chunk(b"\x01\x02" * 50)
        assert not q1.empty()
        assert not q2.empty()

        broadcaster.drain_audio_clients()
        assert q1.empty()
        assert q2.empty()

    asyncio.run(_run())


def test_developer_api_live_config_does_not_include_language_codes():
    session = GeminiSession(on_caption=lambda c: None)
    config = session._build_config("gemini-3.5-live-translate-preview")

    # Developer API Live mode rejects language_codes; verify input_audio_transcription is empty
    assert config.input_audio_transcription is not None
    assert config.input_audio_transcription.language_codes is None or config.input_audio_transcription.language_codes == []
    assert config.translation_config.target_language_code == "en"


def test_auto_drift_toggle_endpoint_runtime_only():
    from fastapi.testclient import TestClient
    from app.server import app, session as server_session

    client = TestClient(app)

    # Initial state
    assert server_session.auto_drift_correction is False

    # Toggle ON
    resp = client.post("/api/config/auto-drift-correction", json={"enabled": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["auto_drift_correction"] is True
    assert server_session.auto_drift_correction is True

    # Toggle OFF
    resp = client.post("/api/config/auto-drift-correction", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["auto_drift_correction"] is False
    assert server_session.auto_drift_correction is False


def test_auto_drift_toggle_clears_drift_state():
    session = GeminiSession(on_caption=lambda c: None)
    session._drift_history.append(2)
    session._consecutive_clean_turns = 1

    session.clear_drift_state()
    assert len(session._drift_history) == 0
    assert session._consecutive_clean_turns == 0


def test_configuration_value_error_is_not_retried():
    async def _run():
        session = GeminiSession(on_caption=lambda c: None)
        model_resolver.lock_session("gemini-3.5-live-translate-preview")

        # Mock _run_session to raise a ValueError (non-retryable config error)
        session._run_session = AsyncMock(side_effect=ValueError("Simulated invalid configuration parameter"))

        with patch("app.gemini_session.session_log"), patch("app.gemini_session.server_log"):
            await session._run_with_retry()

        # Should fail immediately without retrying
        assert session.state.status == SessionStatus.FAILED
        assert "Configuration error" in session.state.last_event
        assert session._attempt == 0
        session._run_session.assert_called_once()


        model_resolver.unlock_session()

    asyncio.run(_run())


def test_configuration_value_error_does_not_auto_restart_service():
    from app.server import _handle_session_state_change
    import app.server as server_mod

    # Setup session state with configuration error
    state = GeminiSession(on_caption=lambda c: None).state
    state.status = SessionStatus.FAILED
    state.last_event = "Configuration error: language_codes not supported"

    # Reset any existing task
    server_mod._auto_restart_task = None

    _handle_session_state_change(state)

    # Auto restart task should NOT have been created
    assert server_mod._auto_restart_task is None
