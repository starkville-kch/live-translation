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
                         (audioop.tomono)              (audioop.ratecv)
                               │
                               ▼
                    asyncio.Queue[bytes]  ──▶  GeminiSession.send_audio()

Audio format contract
---------------------
The Gemini Live API expects ``audio/pcm;rate=16000``:
  • Sample rate : 16,000 Hz
  • Channels    : 1 (mono)
  • Bit depth   : 16-bit signed integer (PCM16 / ``paInt16``)
  • Chunk size  : ``chunk_ms`` ms (default 100 ms = 1,600 samples = 3,200 bytes)

The mixer may present audio at 44.1 kHz, 48 kHz, or 96 kHz in stereo;
downmixing and resampling are handled entirely with the stdlib ``audioop``
module so no NumPy/SciPy dependency is required.

Silence detection
-----------------
RMS below ``SILENCE_FLOOR_RMS`` for ``SILENCE_TIMEOUT_S`` consecutive seconds
transitions the status to ``NO_SIGNAL``.  This triggers a warning in the
operator console level meter without stopping the capture loop.

CLI helpers
-----------
  python -m app.audio --list            # print all input device indices + names
  python -m app.audio --test <idx> <s>  # record <s> seconds → test_capture_<idx>.wav
"""
import asyncio
import audioop
import math
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Callable

import numpy as np
import pyaudio

from app.config import audio_cfg, save_audio_device
from app.logger import audio_log

TARGET_RATE = 16000
TARGET_CHANNELS = 1
TARGET_WIDTH = 2  # PCM16 = 2 bytes per sample
SILENCE_FLOOR_RMS = 50  # below this = silence (for disconnection detection)
SILENCE_TIMEOUT_S = 10  # seconds of near-silence before "no signal" state


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
            devices.append(DeviceInfo(
                index=i,
                name=info["name"],
                max_input_channels=info["maxInputChannels"],
                default_sample_rate=info["defaultSampleRate"],
            ))
    return devices


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
        src_rate = int(self._cfg.get("sample_rate", TARGET_RATE))
        frames_per_chunk = int(src_rate * chunk_ms / 1000)
        last_nonsilent = time.monotonic()

        stream = None
        try:
            device_info = (_pa.get_device_info_by_index(self._device_index)
                           if self._device_index is not None
                           else _pa.get_default_input_device_info())
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
            self._emit(
                status=AudioStatus.CONNECTED,
                device_name=device_info["name"],
            )
            audio_log.info("Audio capture started: %s @ %dHz %dch",
                           device_info["name"], src_rate, src_channels)

            resample_state = None

            while not self._stop_event.is_set():
                try:
                    raw = stream.read(frames_per_chunk, exception_on_overflow=False)
                except OSError as e:
                    audio_log.warning("Stream read error: %s", e)
                    self._emit(status=AudioStatus.DISCONNECTED, level_rms=0.0)
                    break

                # Downmix to mono if needed
                if src_channels > 1:
                    raw = audioop.tomono(raw, TARGET_WIDTH, 0.5, 0.5)

                # Resample to 16kHz if needed
                if src_rate != TARGET_RATE:
                    raw, resample_state = audioop.ratecv(
                        raw, TARGET_WIDTH, 1, src_rate, TARGET_RATE, resample_state
                    )

                rms = audioop.rms(raw, TARGET_WIDTH)
                level = _rms_to_level(rms)

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

        except Exception as e:
            audio_log.error("Audio capture fatal error: %s", e)
            self._emit(status=AudioStatus.DISCONNECTED, level_rms=0.0)
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            audio_log.info("Audio capture stopped")

    async def _enqueue(self, data: bytes) -> None:
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            pass  # drop oldest-implied by put_nowait skip; acceptable under backpressure


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
