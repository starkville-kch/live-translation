"""
app/audio.py — USB Mixer Audio Capture Pipeline
================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Captures mono 16 kHz PCM16 audio from the church USB mixer and puts it into
an asyncio queue for downstream consumption by ``GeminiSession``.

Pipeline
--------
  USB Mixer ──USB──▶ PyAudio stream ──▶ _capture_loop() thread
                                              │
                               ┌──────────────┴─────────────────┐
                               ▼                                 ▼
                         downmix to mono              resample to 16 kHz
                        (numpy average)         (scipy Butterworth LPF +
                               │                 phase-tracking interpolator)
                               ▼
                    asyncio.Queue[bytes]  ──▶  GeminiSession.send_audio()

Audio format contract
---------------------
The Gemini Live API expects ``audio/pcm;rate=16000``:
  • Sample rate : 16,000 Hz
  • Channels    : 1 (mono)
  • Bit depth   : 16-bit signed integer (PCM16 / ``paInt16``)
  • Chunk size  : ``chunk_ms`` ms (default 100 ms = 1,600 samples = 3,200 bytes)

If the device supports 16 kHz mono natively (e.g. MME devices on Windows),
the stream is opened directly at that format and no software resampling
occurs.  Otherwise, numpy handles stereo-to-mono downmixing and a
``Resampler`` class applies a 4th-order Butterworth anti-aliasing filter
before decimating to 16 kHz.

Resilience
----------
- **DirectSound rejection:** Devices under the Windows DirectSound host API
  are refused at startup (known non-blocking ``stream.read()`` driver bug).
- **USB hot-plug reconnection:** If the USB device disconnects mid-capture,
  the loop retries with exponential backoff (2 s → 30 s cap) until the
  device reappears or ``stop()`` is called.

CLI helpers
-----------
  python -m app.audio --list            # print all input device indices + names
  python -m app.audio --test <idx> <s>  # record <s> seconds → test_capture_<idx>.wav
"""
import asyncio
import math
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Callable

import numpy as np
from scipy import signal

import pyaudio

from app.config import audio_cfg, save_audio_device
from app.logger import audio_log

TARGET_RATE = 16000
TARGET_CHANNELS = 1
TARGET_WIDTH = 2  # PCM16 = 2 bytes per sample
SILENCE_FLOOR_RMS = 50  # below this = silence (for disconnection detection)
SILENCE_TIMEOUT_S = 10  # seconds of near-silence before "no signal" state


class _DirectSoundError(Exception):
    """Raised when a DirectSound device is detected — non-retryable."""


class AudioStatus(str, Enum):
    CONNECTED = "connected"
    NO_SIGNAL = "no_signal"
    DISCONNECTED = "disconnected"
    STOPPED = "stopped"


@dataclass
class DeviceInfo:
    index: int
    name: str
    max_input_channels: int
    default_sample_rate: float


@dataclass
class AudioState:
    status: AudioStatus = AudioStatus.STOPPED
    level_rms: float = 0.0  # 0–100 normalised
    device_name: str = ""
    last_update: float = field(default_factory=time.monotonic)


_pa = pyaudio.PyAudio()


def list_input_devices() -> list[DeviceInfo]:
    devices = []
    for i in range(_pa.get_device_count()):
        info = _pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            try:
                api_info = _pa.get_host_api_info_by_index(info["hostApi"])
                api_name = api_info["name"]
                name_with_api = f"{info['name']} [{api_name}]"
            except Exception:
                name_with_api = info["name"]
            devices.append(DeviceInfo(
                index=i,
                name=name_with_api,
                max_input_channels=info["maxInputChannels"],
                default_sample_rate=info["defaultSampleRate"],
            ))
    return devices


class Resampler:
    def __init__(self, src_rate: int, target_rate: int = 16000):
        self.src_rate = src_rate
        self.target_rate = target_rate
        self.ratio = src_rate / target_rate
        self.input_samples_count = 0
        
        # Design lowpass filter to prevent aliasing (cutoff at 7.5 kHz or 0.45 * target_rate)
        cutoff = min(7500.0, target_rate * 0.45)
        nyq = src_rate / 2.0
        normalized_cutoff = cutoff / nyq
        self.b, self.a = signal.butter(4, normalized_cutoff, btype='low')
        self.zi = signal.lfilter_zi(self.b, self.a)
        
    def process(self, samples: np.ndarray) -> np.ndarray:
        # Apply anti-aliasing filter
        filtered, self.zi = signal.lfilter(self.b, self.a, samples, zi=self.zi)
        
        n_in = len(filtered)
        if n_in == 0:
            return np.array([], dtype=np.int16)
            
        # Determine output sample range using exact integer math
        m_start = (self.input_samples_count * self.target_rate + self.src_rate - 1) // self.src_rate
        m_end = ((self.input_samples_count + n_in) * self.target_rate + self.src_rate - 1) // self.src_rate
        
        self.input_samples_count += n_in
        
        if m_start >= m_end:
            return np.array([], dtype=np.int16)
            
        m_indices = np.arange(m_start, m_end)
        # Convert output indices back to fractional input indices relative to current chunk
        out_indices = m_indices * self.ratio - (self.input_samples_count - n_in)
        
        # Linear interpolation
        idx_floor = out_indices.astype(np.int32)
        idx_ceil = np.minimum(idx_floor + 1, n_in - 1)
        frac = out_indices - idx_floor
        
        resampled = (1.0 - frac) * filtered[idx_floor] + frac * filtered[idx_ceil]
        return resampled.astype(np.int16)


def compute_rms(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return 0.0
    mean_square = np.mean(samples.astype(np.float64) ** 2)
    return np.sqrt(mean_square)


def _rms_to_level(rms: float) -> float:
    """Map raw RMS (0–32768) to 0–100 log scale."""
    if rms < 1:
        return 0.0
    db = 20 * math.log10(rms / 32768.0)
    # -60 dB = 0, 0 dB = 100
    return max(0.0, min(100.0, (db + 60) / 60 * 100))


class AudioCapture:
    def __init__(self, on_state_change: Callable[[AudioState], None] | None = None):
        self._cfg = audio_cfg()
        self._on_state_change = on_state_change
        self._state = AudioState()
        self._stop_event = threading.Event()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _emit(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self._state, k, v)
        self._state.last_update = time.monotonic()
        if self._on_state_change:
            self._on_state_change(AudioState(**vars(self._state)))

    def start(self, device_index: int | None = None) -> None:
        if device_index is None:
            device_index = self._cfg.get("device_index")
        if device_index is not None:
            save_audio_device(device_index)
        self._device_index = device_index
        self._stop_event.clear()
        self._loop = asyncio.get_event_loop()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._emit(status=AudioStatus.STOPPED, level_rms=0.0)

    @property
    def state(self) -> AudioState:
        return AudioState(**vars(self._state))

    async def chunks(self) -> AsyncIterator[bytes]:
        while not self._stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield chunk
            except asyncio.TimeoutError:
                continue

    def _capture_loop(self) -> None:
        chunk_ms = self._cfg.get("chunk_ms", 100)
        backoff = 2.0
        max_backoff = 30.0

        while not self._stop_event.is_set():
            stream = None
            try:
                self._open_and_read(chunk_ms)
                # If _open_and_read returns normally, the inner loop broke
                # due to a stream read error — fall through to retry.
                backoff = 2.0  # Reset on any successful connection period
            except _DirectSoundError:
                # Fatal — don't retry, DirectSound will never work correctly
                return
            except Exception as e:
                audio_log.warning("Audio capture error: %s — retrying in %.0fs", e, backoff)
                self._emit(status=AudioStatus.DISCONNECTED, level_rms=0.0)

            # Wait before retry (interruptible by stop_event)
            if self._stop_event.wait(timeout=backoff):
                break
            backoff = min(backoff * 2, max_backoff)

            # Re-initialize PyAudio so re-plugged USB devices are discoverable
            self._reinit_pyaudio()

        audio_log.info("Audio capture stopped")

    def _reinit_pyaudio(self) -> None:
        """Terminate and re-create the global PyAudio instance.

        After a USB device is unplugged and re-plugged, PortAudio's cached
        device list is stale.  Terminating and re-creating forces a fresh
        enumeration so the device index becomes valid again.
        """
        global _pa
        try:
            _pa.terminate()
        except Exception:
            pass
        _pa = pyaudio.PyAudio()
        audio_log.info("PyAudio re-initialized (device list refreshed)")

    def _open_and_read(self, chunk_ms: int) -> None:
        """Open the audio stream and run the read loop.

        Returns normally when the stream breaks (e.g. USB disconnect).
        Raises _DirectSoundError if the device uses DirectSound.
        Raises Exception for any other open/setup failure.
        """
        device_info = (_pa.get_device_info_by_index(self._device_index)
                       if self._device_index is not None
                       else _pa.get_default_input_device_info())

        # ── DirectSound rejection ───────────────────────────────────
        try:
            api_info = _pa.get_host_api_info_by_index(device_info["hostApi"])
            if "DirectSound" in api_info.get("name", ""):
                audio_log.error(
                    "Device '%s' uses the DirectSound host API, which has a "
                    "known non-blocking read bug. Please select the [MME] or "
                    "[Windows WASAPI] version of this device instead.",
                    device_info["name"]
                )
                self._emit(status=AudioStatus.DISCONNECTED, level_rms=0.0)
                raise _DirectSoundError()
        except _DirectSoundError:
            raise
        except Exception:
            pass  # Can't determine host API — proceed cautiously

        # ── Native 16kHz mono detection ─────────────────────────────
        native_16k = False
        try:
            native_16k = _pa.is_format_supported(
                rate=TARGET_RATE,
                input_device=self._device_index if self._device_index is not None
                    else device_info["index"],
                input_channels=1,
                input_format=pyaudio.paInt16,
            )
        except Exception:
            pass

        if native_16k:
            src_rate = TARGET_RATE
            src_channels = 1
            audio_log.info("Device supports native 16kHz mono — bypassing resampler")
        else:
            src_channels = min(int(device_info["maxInputChannels"]), 2)
            src_rate = int(device_info["defaultSampleRate"])

        frames_per_chunk = int(src_rate * chunk_ms / 1000)

        stream = _pa.open(
            format=pyaudio.paInt16,
            channels=src_channels,
            rate=src_rate,
            input=True,
            input_device_index=self._device_index,
            frames_per_buffer=frames_per_chunk,
        )

        try:
            self._emit(
                status=AudioStatus.CONNECTED,
                device_name=device_info["name"],
            )
            audio_log.info("Audio capture started: %s @ %dHz %dch",
                           device_info["name"], src_rate, src_channels)

            resampler = None
            if src_rate != TARGET_RATE:
                resampler = Resampler(src_rate, TARGET_RATE)

            needs_downmix = src_channels > 1
            last_nonsilent = time.monotonic()
            non_blocking_warned = False

            while not self._stop_event.is_set():
                t_start = time.monotonic()
                try:
                    raw = stream.read(frames_per_chunk, exception_on_overflow=False)
                except OSError as e:
                    audio_log.warning("Stream read error (device likely disconnected): %s", e)
                    self._emit(status=AudioStatus.DISCONNECTED, level_rms=0.0)
                    return  # Return to outer retry loop

                t_end = time.monotonic()
                read_duration = t_end - t_start
                expected_duration = chunk_ms / 1000.0

                # Rate-limit safety net for non-blocking driver bugs
                if read_duration < expected_duration * 0.5:
                    if not non_blocking_warned:
                        audio_log.warning(
                            "Audio device read returned instantly (took %.6fs for "
                            "expected %.3fs). Throttling to real-time.",
                            read_duration, expected_duration
                        )
                        non_blocking_warned = True
                    sleep_time = expected_duration - read_duration
                    if sleep_time > 0:
                        time.sleep(sleep_time)

                # Convert to numpy array
                samples = np.frombuffer(raw, dtype=np.int16)

                # Downmix to mono if needed
                if needs_downmix:
                    samples = samples.reshape(-1, src_channels)
                    samples = (samples.sum(axis=1) // src_channels).astype(np.int16)

                # Resample to 16kHz if needed
                if resampler is not None:
                    samples = resampler.process(samples)

                rms = compute_rms(samples)
                level = _rms_to_level(rms)

                # Convert back to bytes for queue/SSE/Gemini
                raw = samples.tobytes()

                now = time.monotonic()
                if rms > SILENCE_FLOOR_RMS:
                    last_nonsilent = now

                if now - last_nonsilent > SILENCE_TIMEOUT_S:
                    status = AudioStatus.NO_SIGNAL
                else:
                    status = AudioStatus.CONNECTED

                self._emit(status=status, level_rms=level)

                if self._loop and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self._enqueue(raw), self._loop
                    )
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    async def _enqueue(self, data: bytes) -> None:
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()  # Drop oldest chunk to maintain recency
                self._queue.put_nowait(data)
            except Exception:
                pass


# CLI helper: python -m app.audio --list  or  --test <index> <seconds>
if __name__ == "__main__":
    import sys

    if "--list" in sys.argv:
        for d in list_input_devices():
            print(f"[{d.index:2d}] {d.name}  ({int(d.default_sample_rate)}Hz, {d.max_input_channels}ch)")

    elif "--test" in sys.argv:
        import wave
        idx = int(sys.argv[sys.argv.index("--test") + 1])
        duration = int(sys.argv[sys.argv.index("--test") + 2]) if len(sys.argv) > sys.argv.index("--test") + 2 else 10
        out_path = f"test_capture_{idx}.wav"
        print(f"Recording {duration}s from device {idx} → {out_path}")

        frames = []
        loop = asyncio.new_event_loop()

        def collect():
            async def _run():
                cap = AudioCapture()
                cap._loop = loop
                cap.start(device_index=idx)
                start = time.monotonic()
                async for chunk in cap.chunks():
                    frames.append(chunk)
                    if time.monotonic() - start >= duration:
                        cap.stop()
                        break
            loop.run_until_complete(_run())

        collect()
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TARGET_RATE)
            wf.writeframes(b"".join(frames))
        print(f"Saved {out_path} ({len(frames)} chunks)")
