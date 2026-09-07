"""
tests/test_audio_classifier.py — Heuristic Music/Speech Detector (Observer-Only)
=================================================================================
Synthetic-signal unit tests for app/audio_classifier.py, plus a guard test
proving the classifier is fully decoupled from TranslationManager's audio
fan-out and billing.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.audio import AudioCapture
from app.audio_classifier import AudioClassifier
from app.gemini_session import GeminiSession
from app.translation_manager import TranslationManager

SR = 16000


def _pcm16_bytes(x: np.ndarray) -> bytes:
    return np.clip(x * 32767.0, -32768, 32767).astype(np.int16).tobytes()


def _speech_like(duration_s: float, seed: int = 0) -> np.ndarray:
    """Band-limited noise with aperiodic syllable-rate amplitude dips, no beat."""
    rng = np.random.default_rng(seed)
    n = int(SR * duration_s)
    noise = rng.normal(0.0, 1.0, n)

    # Concentrate energy at low/mid frequencies (peaky, low rolloff) via a
    # simple moving-average low-pass (cheap stand-in for a bandpass filter).
    kernel = np.ones(9) / 9.0
    tonal = np.convolve(noise, kernel, mode="same")

    # Aperiodic envelope: smoothed random walk, clipped to stay positive —
    # syllable-rate dips with no fixed periodicity (no beat).
    steps = rng.normal(0.0, 1.0, n // 160 + 1)
    walk = np.cumsum(steps)
    walk = walk - np.min(walk)
    walk = walk / (np.max(walk) + 1e-9)
    envelope = np.repeat(walk, 160)[:n]
    envelope = 0.2 + 0.8 * envelope

    x = tonal * envelope
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.7
    return x


def _music_like(duration_s: float, bpm: float = 120.0, seed: int = 0) -> np.ndarray:
    """Periodic broadband percussive onsets at a clear beat-band tempo."""
    rng = np.random.default_rng(seed)
    n = int(SR * duration_s)
    x = np.zeros(n)

    beat_period_samples = int(SR * 60.0 / bpm)
    decay = np.exp(-np.linspace(0, 8, int(SR * 0.12)))
    for onset in range(0, n, beat_period_samples):
        burst_len = min(len(decay), n - onset)
        if burst_len <= 0:
            continue
        burst_noise = rng.normal(0.0, 1.0, burst_len)  # broadband -> flat spectrum
        x[onset : onset + burst_len] += burst_noise * decay[:burst_len]

    x = x / (np.max(np.abs(x)) + 1e-9) * 0.8
    return x


def _white_noise_constant(duration_s: float, seed: int = 0) -> np.ndarray:
    """Ambiguous input: steady broadband noise, no beat, no syllable-rate dips."""
    rng = np.random.default_rng(seed)
    n = int(SR * duration_s)
    x = rng.normal(0.0, 1.0, n)
    return x / (np.max(np.abs(x)) + 1e-9) * 0.3


def _feed(clf: AudioClassifier, signal: np.ndarray, chunk_ms: int = 100) -> None:
    chunk_len = int(SR * chunk_ms / 1000)
    for i in range(0, len(signal), chunk_len):
        clf.process_chunk(_pcm16_bytes(signal[i : i + chunk_len]))


def test_speech_like_classifies_speech_after_hysteresis():
    clf = AudioClassifier(sample_rate=SR, hysteresis_s=2.0)
    _feed(clf, _speech_like(6.0, seed=1))
    assert clf.state.label == "speech"


def test_music_like_classifies_music_after_hysteresis():
    clf = AudioClassifier(sample_rate=SR, hysteresis_s=2.0)
    _feed(clf, _music_like(6.0, bpm=120.0, seed=2))
    assert clf.state.label == "music"


def test_hysteresis_ignores_brief_burst_inside_sustained_speech():
    clf = AudioClassifier(sample_rate=SR, hysteresis_s=3.0)
    _feed(clf, _speech_like(5.0, seed=3))
    assert clf.state.label == "speech"

    # Brief (<hysteresis window) music burst inside sustained speech.
    _feed(clf, _music_like(1.0, bpm=120.0, seed=4))
    assert clf.state.label == "speech"

    # Sustained speech resumes; published label still unaffected throughout.
    _feed(clf, _speech_like(2.0, seed=5))
    assert clf.state.label == "speech"


def test_ambiguous_input_stays_uncertain():
    clf = AudioClassifier(sample_rate=SR, hysteresis_s=2.0)
    _feed(clf, _white_noise_constant(5.0, seed=6))
    assert clf.state.label == "uncertain"


def test_process_chunk_never_raises_and_sets_uncertain_on_internal_error():
    clf = AudioClassifier(sample_rate=SR)
    with patch.object(AudioClassifier, "_extract_features", side_effect=RuntimeError("boom")):
        clf.process_chunk(b"\x00\x00" * 1600)  # must not raise
    assert clf.state.label == "uncertain"


def test_pipeline_isolation_billed_seconds_and_fanout_unchanged():
    """Guard test: classifier presence/failure must not change billed_seconds
    or per-target chunk delivery in TranslationManager._audio_pipe."""

    async def _run(classifier_raises: bool) -> tuple[float, int, int]:
        mock_audio = type("A", (), {})()
        mock_audio.start = lambda *a, **kw: None
        mock_audio.stop = lambda: None

        chunk = b"\x00\x00" * 1600  # 100ms @ 16kHz mono PCM16

        async def _mock_chunks():
            for _ in range(10):
                yield chunk

        mock_audio.chunks = _mock_chunks
        mgr = TranslationManager(audio_capture=mock_audio)

        patch_target = (
            patch.object(AudioClassifier, "process_chunk", side_effect=RuntimeError("classifier down"))
            if classifier_raises
            else patch.object(AudioClassifier, "process_chunk", wraps=AudioClassifier.process_chunk)
        )

        with patch.object(GeminiSession, "start", new_callable=AsyncMock), patch_target:
            await mgr.start(active_targets=["en", "uk"], expected_source_language="ko")
            await asyncio.sleep(0.1)

            billed = mgr.billed_seconds
            en_count = mgr.sessions["en"]._audio_queue.qsize()
            uk_count = mgr.sessions["uk"]._audio_queue.qsize()

            await mgr.stop()
            return billed, en_count, uk_count

    billed_normal, en_normal, uk_normal = asyncio.run(_run(classifier_raises=False))
    billed_failing, en_failing, uk_failing = asyncio.run(_run(classifier_raises=True))

    assert billed_normal == billed_failing
    assert en_normal == en_failing
    assert uk_normal == uk_failing
    assert abs(billed_normal - 1.0) < 1e-3
    assert en_normal == 10
    assert uk_normal == 10
