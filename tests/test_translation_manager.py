"""
tests/test_translation_manager.py — Unit Tests for Multi-Target TranslationManager
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.audio import AudioCapture
from app.gemini_session import GeminiSession, SessionStatus
from app.translation_manager import TranslationManager


def test_manager_creates_single_audiocapture_and_per_target_sessions():
    async def _run():
        mock_audio = MagicMock(spec=AudioCapture)
        mock_audio.start = MagicMock()
        mock_audio.stop = MagicMock()
        mock_audio.pause = MagicMock()
        mock_audio.resume = MagicMock()
        mock_audio.drain = MagicMock()

        # Async generator for audio chunks
        async def _mock_chunks():
            yield b"\x01\x00" * 800

        mock_audio.chunks = _mock_chunks
        mock_audio.state = MagicMock()
        mock_audio.state.status.value = "connected"
        mock_audio.state.level_rms = 0.5
        mock_audio.state.device_name = "USB Audio"

        mgr = TranslationManager(audio_capture=mock_audio)

        with patch.object(GeminiSession, "start", new_callable=AsyncMock) as mock_sess_start:
            await mgr.start(
                device_index=1,
                active_targets=["en", "uk", "zh"],
                expected_source_language="ko",
            )

            assert mgr.is_running is True
            assert mgr.is_paused is False
            assert mgr.active_targets == ["en", "uk", "zh"]
            assert mgr.expected_source_language == "ko"
            assert len(mgr.sessions) == 3

            # Exactly one AudioCapture started
            mock_audio.start.assert_called_once_with(device_index=1)

            # Check each target session
            assert mgr.sessions["en"].target_language_code == "en"
            assert mgr.sessions["en"].expected_source_language == "ko"
            assert mgr.sessions["uk"].target_language_code == "uk"
            assert mgr.sessions["uk"].expected_source_language == "ko"
            assert mgr.sessions["zh"].target_language_code == "zh"
            assert mgr.sessions["zh"].expected_source_language == "ko"

            # Check all sessions started
            assert mock_sess_start.call_count == 3

            await mgr.stop()
            assert mgr.is_running is False
            mock_audio.stop.assert_called_once()

    asyncio.run(_run())


def test_manager_fans_out_pcm_to_all_targets():
    async def _run():
        mock_audio = MagicMock(spec=AudioCapture)
        mock_audio.start = MagicMock()
        mock_audio.stop = MagicMock()

        chunk_1 = b"\xaa\xbb" * 800

        async def _mock_chunks():
            yield chunk_1

        mock_audio.chunks = _mock_chunks
        mgr = TranslationManager(audio_capture=mock_audio)

        with patch.object(GeminiSession, "start", new_callable=AsyncMock):
            await mgr.start(active_targets=["uk", "zh"], expected_source_language="en")

            # Allow pipe task to run
            await asyncio.sleep(0.05)

            # Both sessions should have received the chunk in their audio queues
            q_uk = mgr.sessions["uk"]._audio_queue
            q_zh = mgr.sessions["zh"]._audio_queue

            assert not q_uk.empty()
            assert not q_zh.empty()
            assert await q_uk.get() == chunk_1
            assert await q_zh.get() == chunk_1

            await mgr.stop()

    asyncio.run(_run())


def test_manager_non_blocking_fanout_and_failure_isolation():
    """A slow or stalled session queue must not block audio fan-out to other sessions."""
    async def _run():
        mock_audio = MagicMock(spec=AudioCapture)
        mock_audio.start = MagicMock()
        mock_audio.stop = MagicMock()

        chunk_a = b"\x11\x22" * 800

        async def _mock_chunks():
            for _ in range(5):
                yield chunk_a

        mock_audio.chunks = _mock_chunks
        mgr = TranslationManager(audio_capture=mock_audio)

        with patch.object(GeminiSession, "start", new_callable=AsyncMock):
            await mgr.start(active_targets=["en", "uk"], expected_source_language="ko")

            # Artificially fill the UK queue to maxsize to test overflow handling
            uk_sess = mgr.sessions["uk"]
            while not uk_sess._audio_queue.full():
                uk_sess._audio_queue.put_nowait(b"\x00" * 100)

            # Allow pipe task to push 5 chunks
            await asyncio.sleep(0.05)

            # English queue should have received chunks without being blocked
            en_sess = mgr.sessions["en"]
            assert not en_sess._audio_queue.empty()
            assert await en_sess._audio_queue.get() == chunk_a

            # UK queue is still operational (dropped oldest frame without crashing)
            assert uk_sess._audio_queue.full()

            await mgr.stop()

    asyncio.run(_run())


def test_manager_pause_resume_clean_lifecycle():
    async def _run():
        mock_audio = MagicMock(spec=AudioCapture)
        mock_audio.start = MagicMock()
        mock_audio.stop = MagicMock()
        mock_audio.pause = MagicMock()
        mock_audio.resume = MagicMock()
        mock_audio.drain = MagicMock()

        async def _mock_chunks():
            while True:
                yield b"\x00" * 100
                await asyncio.sleep(0.01)

        mock_audio.chunks = _mock_chunks
        mgr = TranslationManager(audio_capture=mock_audio)

        with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
             patch.object(GeminiSession, "pause_clean", new_callable=AsyncMock) as mock_pause, \
             patch.object(GeminiSession, "resume_clean", new_callable=AsyncMock) as mock_resume, \
             patch.object(GeminiSession, "stop", new_callable=AsyncMock) as mock_stop:

            await mgr.start(active_targets=["en", "uk"])
            assert mgr.is_running is True

            # Pause cleanly
            await mgr.pause_clean()
            assert mgr.is_paused is True
            mock_audio.pause.assert_called_once()
            assert mock_pause.call_count == 2

            # Resume cleanly
            await mgr.resume_clean()
            assert mgr.is_paused is False
            mock_audio.resume.assert_called_once()
            mock_audio.drain.assert_called_once()
            assert mock_resume.call_count == 2

            # Stop
            await mgr.stop()
            assert mgr.is_running is False
            assert mock_stop.call_count == 2

    asyncio.run(_run())


def test_manager_validation_and_duplicate_normalization():
    async def _run():
        mgr = TranslationManager()

        # Duplicate targets should be normalized
        with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
             patch.object(AudioCapture, "start", MagicMock()), \
             patch.object(AudioCapture, "stop", MagicMock()):
            await mgr.start(active_targets=["en", "uk", "en", "UK"])
            assert mgr.active_targets == ["en", "uk"]
            await mgr.stop()

        # Empty target list should raise ValueError
        with pytest.raises(ValueError, match="At least one valid active target"):
            await mgr.start(active_targets=[])

        # Source in targets should raise ValueError
        with pytest.raises(ValueError, match="cannot be in active target"):
            await mgr.start(active_targets=["en", "ko"], expected_source_language="ko")

        # Invalid target code
        with pytest.raises(ValueError, match="Invalid target language code"):
            await mgr.start(active_targets=["invalid_xyz"])

    asyncio.run(_run())


def test_manager_state_representation():
    mgr = TranslationManager()
    st = mgr.state()
    assert st["is_running"] is False
    assert st["is_paused"] is False
    assert "sessions" in st
    assert "audio" in st
    assert st["audio"]["status"] == "stopped"


def test_manager_directional_failure_isolation_and_source_preview():
    """Verify failure isolation matrix in both directions:
    Test A (Secondary ZH failure/reconnect):
      Primary EN continues translating; operator [발화] continues unbroken.
    Test B (Primary EN failure/reconnect):
      Secondary ZH continues translating; shared microphone remains active;
      operator [발화] pauses only during EN disconnect and resumes cleanly on reconnect.
    """
    async def _run():
        mock_audio = MagicMock(spec=AudioCapture)
        mock_audio.start = MagicMock()
        mock_audio.stop = MagicMock()

        source_events = []
        caption_events = []

        def _on_source(text: str):
            source_events.append(text)

        def _on_caption(target: str, text: str):
            caption_events.append((target, text))

        mgr = TranslationManager(
            audio_capture=mock_audio,
            on_source=_on_source,
            on_caption=_on_caption,
        )

        with patch.object(GeminiSession, "start", new_callable=AsyncMock):
            await mgr.start(active_targets=["en", "zh"], expected_source_language="ko")
            assert mgr.primary_target == "en"

            sess_en = mgr.sessions["en"]
            sess_zh = mgr.sessions["zh"]

            # 1. Baseline: both receive speech
            sess_en._on_source("안녕하세요 (EN)")
            sess_zh._on_source("안녕하세요 (ZH)")
            # Only primary EN calls operator _on_source
            assert source_events == ["안녕하세요 (EN)"]

            sess_en._on_caption("Hello")
            sess_zh._on_caption("你好")
            assert ("en", "Hello") in caption_events
            assert ("zh", "你好") in caption_events

            # 2. Test A: Secondary ZH fails / reconnects (epoch 1 -> 2)
            sess_zh.session_status = SessionStatus.RECONNECTING
            # Primary EN continues translation & source preview
            sess_en._on_source("환영합니다 (EN)")
            sess_en._on_caption("Welcome")
            assert source_events == ["안녕하세요 (EN)", "환영합니다 (EN)"]
            assert ("en", "Welcome") in caption_events
            # Microphone remains active and service remains running
            assert mgr.is_running is True
            mock_audio.stop.assert_not_called()

            # ZH reconnects
            sess_zh.session_status = SessionStatus.CONNECTED
            sess_zh._on_caption("欢迎")
            assert ("zh", "欢迎") in caption_events

            # 3. Test B: Primary EN fails / reconnects (epoch 1 -> 2)
            sess_en.session_status = SessionStatus.RECONNECTING
            # Secondary ZH continues translating uninterrupted
            sess_zh._on_caption("很高兴见到你")
            assert ("zh", "很高兴见到你") in caption_events
            # Service and audio capture remain alive
            assert mgr.is_running is True
            mock_audio.stop.assert_not_called()

            # EN reconnects cleanly and resumes source deltas
            sess_en.session_status = SessionStatus.CONNECTED
            sess_en._on_source("감사합니다 (EN)")
            sess_en._on_caption("Thank you")
            assert source_events[-1] == "감사합니다 (EN)"
            assert ("en", "Thank you") in caption_events

            await mgr.stop()

    asyncio.run(_run())

