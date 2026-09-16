"""
app/audio_classifier.py — Heuristic Music/Speech Detector (Observer-Only, Phase 1)
===================================================================================
Passive NumPy-only audio classifier. Labels the live PCM16 stream as
"speech" / "music" / "uncertain" for operator awareness and the session log.

This module is intentionally decoupled from the translation pipeline: it has
no knowledge of `_audio_pipe`, sessions, billing, or pause/resume state, and
its public entry point (`process_chunk`) never raises.
"""
import time
from dataclasses import dataclass, field

import numpy as np

from app.logger import server_log


@dataclass
class ClassifierState:
    label: str = "uncertain"
    confidence: float = 0.0
    since_s: float = 0.0
    raw_label: str = "uncertain"
    updated_at: float = field(default_factory=time.monotonic)


class AudioClassifier:
    """Ring-buffered heuristic classifier over PCM16 mono audio chunks.

    Feed chunks via `process_chunk()`; read the published label via `.state`.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        window_s: float = 1.0,
        hop_s: float = 0.5,
        hysteresis_s: float = 17.0,
    ):
        self.sample_rate = sample_rate
        self.window_samples = int(sample_rate * window_s)
        self.hop_samples = int(sample_rate * hop_s)
        self.hysteresis_s = hysteresis_s

        self._buffer = np.zeros(0, dtype=np.int16)
        self._hann = np.hanning(self.window_samples).astype(np.float32)

        # Hysteresis and since_s are tracked against audio time processed
        # (hop_s per emitted frame), not wall-clock time. In production these
        # coincide 1:1 since chunks arrive at real-time cadence from the mic;
        # tracking audio time keeps the logic independent of scheduling jitter.
        self._audio_clock: float = 0.0
        self._label_since: float = 0.0
        self._raw_streak_label: str = "uncertain"
        self._raw_streak_start: float = 0.0

        self.state = ClassifierState(updated_at=time.monotonic())

    def process_chunk(self, pcm: bytes) -> None:
        """Append a PCM16 chunk and classify on hop boundaries. Never raises."""
        try:
            self._process_chunk(pcm)
        except Exception as e:
            server_log.debug("[AudioClassifier] process_chunk error: %s", e)
            self._label_since = self._audio_clock
            self.state = ClassifierState(
                label="uncertain", confidence=0.0, since_s=0.0,
                raw_label="uncertain", updated_at=time.monotonic(),
            )

    # ── internal ────────────────────────────────────────────────────────────

    def _process_chunk(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype=np.int16)
        self._buffer = np.concatenate([self._buffer, samples])
        while len(self._buffer) >= self.window_samples:
            frame = self._buffer[: self.window_samples]
            if len(self._buffer) > self.hop_samples:
                self._buffer = self._buffer[self.hop_samples :]
            else:
                self._buffer = np.zeros(0, dtype=np.int16)
            self._emit(frame)

    def _emit(self, frame: np.ndarray) -> None:
        self._audio_clock += self.hop_samples / float(self.sample_rate)

        feats = self._extract_features(frame)
        raw_label, confidence = self._classify_raw(feats)
        published = self._apply_hysteresis(raw_label)

        if published != self.state.label:
            self._label_since = self._audio_clock

        self.state = ClassifierState(
            label=published,
            confidence=confidence,
            since_s=round(self._audio_clock - self._label_since, 1),
            raw_label=raw_label,
            updated_at=time.monotonic(),
        )

    def _extract_features(self, frame: np.ndarray) -> dict:
        x = frame.astype(np.float32) / 32768.0
        windowed = x * self._hann
        # ONE FFT, reused across all spectral features below.
        spectrum = np.abs(np.fft.rfft(windowed)) + 1e-12

        return {
            "spectral_flatness": self._spectral_flatness(spectrum),
            "rolloff": self._rolloff(spectrum),
            "env_var": self._envelope_variance(x),
            "beat_strength": self._beat_autocorr(x),
        }

    @staticmethod
    def _spectral_flatness(spectrum: np.ndarray) -> float:
        geo_mean = np.exp(np.mean(np.log(spectrum)))
        arith_mean = np.mean(spectrum)
        return float(geo_mean / arith_mean) if arith_mean > 0 else 0.0

    def _rolloff(self, spectrum: np.ndarray, pct: float = 0.85) -> float:
        power = spectrum ** 2
        cumulative = np.cumsum(power)
        total = cumulative[-1]
        if total <= 0:
            return 0.0
        idx = int(np.searchsorted(cumulative, pct * total))
        freq_per_bin = self.sample_rate / (2.0 * (len(spectrum) - 1))
        return float(idx * freq_per_bin)

    def _envelope_variance(self, x: np.ndarray, sub_frame_ms: float = 20.0) -> float:
        """Syllable-rate amplitude-dip measure over ~20ms sub-frames."""
        sub_len = max(1, int(self.sample_rate * sub_frame_ms / 1000.0))
        n_sub = len(x) // sub_len
        if n_sub < 2:
            return 0.0
        trimmed = x[: n_sub * sub_len].reshape(n_sub, sub_len)
        envelope = np.sqrt(np.mean(trimmed ** 2, axis=1))
        mean_env = np.mean(envelope)
        return float(np.std(envelope) / mean_env) if mean_env > 0 else 0.0

    def _beat_autocorr(self, x: np.ndarray, onset_hop_ms: float = 20.0) -> float:
        """Autocorrelate a short onset envelope (not raw samples) for a 60-140 BPM peak.

        This is the single most discriminating feature: a loud sermon is
        arrhythmic, sustained praise music is not.
        """
        hop_len = max(1, int(self.sample_rate * onset_hop_ms / 1000.0))
        n_hops = len(x) // hop_len
        if n_hops < 8:
            return 0.0

        trimmed = x[: n_hops * hop_len].reshape(n_hops, hop_len)
        onset_env = np.sqrt(np.mean(trimmed ** 2, axis=1))
        onset_env = onset_env - np.mean(onset_env)
        if not np.any(onset_env):
            return 0.0

        autocorr = np.correlate(onset_env, onset_env, mode="full")
        autocorr = autocorr[len(autocorr) // 2 :]
        if autocorr[0] <= 0:
            return 0.0
        autocorr = autocorr / autocorr[0]

        hop_rate = 1000.0 / onset_hop_ms  # onset-envelope samples per second
        min_lag = max(1, int(hop_rate * 60.0 / 140.0))  # 140 BPM
        max_lag = min(len(autocorr) - 1, int(hop_rate * 60.0 / 60.0))  # 60 BPM
        if min_lag >= max_lag:
            return 0.0
        band = autocorr[min_lag : max_lag + 1]
        return float(np.max(band)) if len(band) else 0.0

    def _classify_raw(self, feats: dict) -> tuple[str, float]:
        """Rule stack keyed on beat_strength; other features corroborate, not vote."""
        beat = feats["beat_strength"]
        flat = feats["spectral_flatness"]
        env_var = feats["env_var"]
        rolloff = feats["rolloff"]

        if beat > 0.35 and flat > 0.12:
            confidence = min(1.0, 0.5 + (beat - 0.35) / 0.5)
            return "music", confidence

        if beat < 0.3 and env_var > 0.1 and rolloff < 4000:
            confidence = min(1.0, 0.5 + (env_var - 0.1) / 0.4)
            return "speech", confidence

        return "uncertain", 0.3

    def _apply_hysteresis(self, raw_label: str) -> str:
        """Require ~15-20s of sustained raw agreement before flipping the published label."""
        if raw_label == self._raw_streak_label:
            streak_duration = self._audio_clock - self._raw_streak_start
        else:
            self._raw_streak_label = raw_label
            self._raw_streak_start = self._audio_clock
            streak_duration = 0.0

        published = self.state.label
        if (
            raw_label != published
            and raw_label != "uncertain"
            and streak_duration >= self.hysteresis_s
        ):
            return raw_label
        return published
