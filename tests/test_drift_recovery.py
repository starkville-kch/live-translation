"""
tests/test_drift_recovery.py — Phase 8A-2 Drift Recovery & Watchdog Regression Tests
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.gemini_session import GeminiSession, SessionStatus, evaluate_drift_score
from app.translation_manager import TranslationManager


@pytest.fixture(autouse=True)
def _isolate_test_logs():
    with patch("app.gemini_session.session_log"), patch("app.gemini_session.server_log"):
        yield


def test_one_completed_turn_cannot_be_scored_multiple_times():
    """Verify each committed turn has identity tracking and contributes to drift score exactly once."""
    session = GeminiSession(on_caption=lambda c: None, expected_source_language="ko", target_language_code="en")
    session._auto_drift_correction = False

    # Simulate arrival of anomalous turn (in_lang = ja)
    session._current_source = "はい、皆さん"
    session._current_target = "Yes, everyone"
    session._turn_in_lang = "ja"
    session._turn_out_lang = "en"

    # Turn 1 committed
    session._commit_current_turn()
    assert len(session._drift_history) == 1
    assert session._drift_history[0] == 1
    assert session._turn_id == 1
    assert session._last_evaluated_turn_id == 1

    # Redundant / duplicate commit calls (e.g. from auto_commit_loop or flush)
    session._commit_current_turn()
    session.flush_current_turn()

    # Drift history must NOT grow; turn was evaluated once and only once
    assert len(session._drift_history) == 1
    assert session._drift_history[0] == 1
    assert session._turn_id == 1
    assert session._last_evaluated_turn_id == 1


def test_one_non_korean_anomaly_does_not_trigger_recovery():
    """A single isolated source language mismatch (+1) must not trigger recovery, and cleans up on clean turns."""
    session = GeminiSession(on_caption=lambda c: None, expected_source_language="ko", target_language_code="en")
    session._auto_drift_correction = True
    session.reset_clean = AsyncMock()

    # Single isolated Vietnamese mismatch
    session._current_source = "Xin chào"
    session._current_target = "Hello"
    session._turn_in_lang = "vi"
    session._turn_out_lang = "en"
    session._commit_current_turn()

    # Score should be 1/3 (not >= 3 threshold)
    assert sum(session._drift_history) == 1
    session.reset_clean.assert_not_called()

    # Followed by 1 clean Korean turn -> drift history clears immediately back to 0/3
    session._current_source = "은혜로운 말씀"
    session._current_target = "Graceful word"
    session._turn_in_lang = "ko"
    session._turn_out_lang = "en"
    session._commit_current_turn()

    assert len(session._drift_history) == 0
    assert sum(session._drift_history) == 0
    session.reset_clean.assert_not_called()


def test_consecutive_genuine_drift_triggers_recovery():
    """Distinct consecutive turns with sustained drift accumulate score >= 3 and trigger clean reset."""
    async def _run():
        session = GeminiSession(on_caption=lambda c: None, expected_source_language="ko", target_language_code="en")
        session._auto_drift_correction = True
        session._run_with_retry = AsyncMock()

        initial_epoch = session.session_epoch

        # 3 distinct Japanese turns
        for i in range(3):
            session._current_source = f"日本語テキスト {i}"
            session._current_target = f"Japanese text {i}"
            session._turn_in_lang = "ja"
            session._turn_out_lang = "en"
            session._commit_current_turn()

        # Yield control to let async task reset_clean run
        await asyncio.sleep(0.05)

        # Clean reset incremented the epoch
        assert session.session_epoch == initial_epoch + 1

    asyncio.run(_run())


def test_recovery_configuration_contains_no_language_codes():
    """Developer API configuration built for normal sessions and recovery must contain no language_codes."""
    session = GeminiSession(on_caption=lambda c: None, expected_source_language="ko", target_language_code="en")
    config = session._build_config("gemini-3.5-live-translate-preview")

    assert config.input_audio_transcription is not None
    assert getattr(config.input_audio_transcription, "language_codes", None) is None or config.input_audio_transcription.language_codes == []
    assert config.translation_config.target_language_code == "en"


def test_recovery_uses_normal_translation_config():
    """Session builder config must match normal live translation config without forced language hacks."""
    session = GeminiSession(on_caption=lambda c: None, expected_source_language="ko", target_language_code="uk")
    config = session._build_config("gemini-3.5-live-translate-preview")

    assert config.translation_config.target_language_code == "uk"
    assert config.translation_config.echo_target_language is True


def test_clean_recovery_does_not_stop_translation_manager():
    """Clean recovery operates per-session and never stops the outer TranslationManager."""
    async def _run():
        tm = TranslationManager(on_caption=lambda lang, c: None)
        # Mock session startup
        with patch.object(GeminiSession, "_run_with_retry", AsyncMock()):
            await tm.start(active_targets=["en"], expected_source_language="ko")
            assert tm.is_running is True

            session = tm.sessions["en"]
            initial_epoch = session.session_epoch

            # Trigger clean reset
            await session.reset_clean(reason="Test recovery")

            # TranslationManager remains running throughout
            assert tm.is_running is True
            assert session.session_epoch == initial_epoch + 1

            await tm.stop()

    asyncio.run(_run())


def test_korean_resumes_normally_after_recovery():
    """After clean recovery, subsequent Korean speech is scored as 0 and processed cleanly."""
    async def _run():
        session = GeminiSession(on_caption=lambda c: None, expected_source_language="ko", target_language_code="en")
        session._auto_drift_correction = True
        session._run_with_retry = AsyncMock()

        # Trigger reset
        await session.reset_clean(reason="Test reset")
        assert len(session._drift_history) == 0

        # Now resume Korean turn
        session._current_source = "하나님의 은혜로"
        session._current_target = "By the grace of God"
        session._turn_in_lang = "ko"
        session._turn_out_lang = "en"
        session._commit_current_turn()

        assert list(session._drift_history) == []
        assert sum(session._drift_history) == 0
        assert session._consecutive_clean_turns == 1

    asyncio.run(_run())


def test_auto_drift_disabled_for_non_korean_speech():
    """Auto drift recovery must be disabled when expected_source_language is not Korean (e.g. English)."""
    session = GeminiSession(
        on_caption=lambda c: None,
        expected_source_language="en",  # English spoken source
        target_language_code="uk",
    )
    session._auto_drift_correction = True
    session.reset_clean = AsyncMock()

    # 3 turns with unexpected language input
    for i in range(3):
        session._current_source = f"Some French text {i}"
        session._current_target = f"Quelque chose {i}"
        session._turn_in_lang = "fr"
        session._turn_out_lang = "fr"
        session._commit_current_turn()

    # Score will accumulate, but auto recovery must NOT trigger reset_clean because expected_source != 'ko'
    session.reset_clean.assert_not_called()
    assert "비정상 언어 감지" in session.state.last_event
