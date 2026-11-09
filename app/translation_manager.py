"""
app/translation_manager.py — Multi-Target Translation Session Manager
=====================================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Orchestrates single-microphone audio capture and non-blocking in-memory
fan-out to multiple independent, isolated GeminiSession instances (one per
active target language).

Invariants:
- Exactly ONE AudioCapture instance captures 16kHz PCM16 mono.
- Target sessions receive PCM chunks into independent, bounded queues.
- A slow, disconnected, or reconnecting target session never blocks audio
  capture or other target sessions.
- Target configuration is fixed upon start() and cannot be mutated while
  running or paused.
"""
import asyncio
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from app.audio import AudioCapture
from app.broadcast import CaptionBroadcaster, CaptionEvent
from app.text_corrections import NameCorrector
from app.gemini_session import GeminiSession, SessionState, SessionStatus
from app.languages import (
    is_valid_language_code,
)
from app.logger import server_log


class TranslationManager:
    def __init__(
        self,
        audio_capture: Optional[AudioCapture] = None,
        on_caption: Optional[Callable[[str, str], None]] = None,
        on_source: Optional[Callable[[str], None]] = None,
        on_audio: Optional[Callable[[str, bytes], None]] = None,
        on_session_state: Optional[Callable[[str, SessionState], None]] = None,
        corrector: NameCorrector | None = None,
        default_target: str = "en",
    ):
        self.audio: AudioCapture = audio_capture or AudioCapture()
        self._on_caption = on_caption
        self._on_source = on_source
        self._on_audio = on_audio
        self._on_session_state = on_session_state
        self._corrector = corrector or NameCorrector()

        self.sessions: Dict[str, GeminiSession] = {}
        self.broadcasters: Dict[str, CaptionBroadcaster] = {}
        self.active_targets: List[str] = [default_target]


        # Ensure default primary broadcaster is available before start()
        self.broadcasters[default_target] = CaptionBroadcaster(
            corrector=self._corrector,
            source_lang="auto",
            target_lang=default_target,
        )

        self._is_running: bool = False
        self._is_paused: bool = False
        self._pipe_task: Optional[asyncio.Task] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._billed_seconds: float = 0.0
        self._start_time: Optional[float] = None
        self._started_at: Optional[datetime] = None
        self._pause_start: Optional[float] = None
        self._paused_duration_s: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def billed_seconds(self) -> float:
        return self._billed_seconds

    @property
    def current_pause_seconds(self) -> float:
        if self._is_paused and self._pause_start is not None:
            return max(0.0, time.monotonic() - self._pause_start)
        return 0.0

    @property
    def paused_duration_seconds(self) -> float:
        return self._paused_duration_s + self.current_pause_seconds

    @property
    def runtime_seconds(self) -> float:
        if self._is_running and self._start_time is not None:
            now = time.monotonic()
            current_pause = max(0.0, now - self._pause_start) if (self._is_paused and self._pause_start is not None) else 0.0
            return max(0.0, now - self._start_time - self._paused_duration_s - current_pause)
        return 0.0


    @property
    def started_at(self) -> Optional[datetime]:
        """Wall-clock service start, including time later spent paused."""
        return self._started_at

    @property
    def start_monotonic(self) -> Optional[float]:
        return self._start_time

    @property
    def elapsed_seconds(self) -> float:
        """Service timeline for recording alignment; pauses remain on it."""
        if self._is_running and self._start_time is not None:
            return max(0.0, time.monotonic() - self._start_time)
        return 0.0

    @property
    def primary_target(self) -> str:
        return self.active_targets[0] if self.active_targets else "en"

    @property
    def primary_broadcaster(self) -> CaptionBroadcaster:
        target = self.primary_target
        if target not in self.broadcasters:
            self.broadcasters[target] = CaptionBroadcaster(corrector=self._corrector, source_lang="auto", target_lang=target)
        return self.broadcasters[target]

    def get_broadcaster(self, target: str) -> Optional[CaptionBroadcaster]:
        clean = (target or "").lower().strip()
        return self.broadcasters.get(clean)


    def _create_session_for_target(self, target: str) -> GeminiSession:
        if target not in self.broadcasters:
            self.broadcasters[target] = CaptionBroadcaster(corrector=self._corrector, source_lang="auto", target_lang=target)
        broadcaster = self.broadcasters[target]

        def caption(text: str) -> None:
            broadcaster.on_caption_delta(text)
            if self._on_caption:
                self._on_caption(target, text)

        def source(text: str) -> None:
            broadcaster.on_source_delta(text)
            if target == self.primary_target and self._on_source:
                self._on_source(text)

        def translated_audio(data: bytes) -> None:
            broadcaster.on_audio_chunk(data)
            if self._on_audio:
                self._on_audio(target, data)

        def state_changed(state: SessionState) -> None:
            if self._on_session_state:
                self._on_session_state(target, state)

        return GeminiSession(on_caption=caption, on_source_transcript=source,
            on_audio_chunk=translated_audio, on_state_change=state_changed,
            on_output_visibility=broadcaster.set_output_visibility,
            corrector=self._corrector, target_language_code=target)



    async def _audio_pipe(self) -> None:
        """Non-blocking fan-out loop: pushes 16kHz PCM chunks to all active target session queues."""
        CHUNK_MS = 100
        try:
            async for chunk in self.audio.chunks():
                if not self._is_paused and self._is_running:
                    receivers = [(target, session) for target, session in self.sessions.items() if session.state.status != SessionStatus.FAILED]
                    if not receivers:
                        continue
                    self._billed_seconds += (CHUNK_MS / 1000.0)
                    for target, session in receivers:
                        try:
                            session._audio_queue.put_nowait(chunk)
                        except asyncio.QueueFull:
                            session._dropped_audio_chunks += 1
                            # Discard oldest chunk under backpressure to avoid accumulating stale latency
                            try:
                                session._audio_queue.get_nowait()
                                session._audio_queue.put_nowait(chunk)
                            except Exception:
                                pass
                            server_log.warning(
                                "[Gemini:%s] Audio queue full; dropped oldest PCM frame (%d total dropped)",
                                target,
                                session._dropped_audio_chunks,
                            )
                        except Exception as e:
                            server_log.warning(
                                "[Gemini:%s] Error fanning audio chunk: %s",
                                target,
                                e,
                            )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            server_log.error("[TranslationManager] Audio pipe error: %s", e)


    async def start(self, device_index: Optional[int] = None, active_targets: Optional[List[str]] = None) -> None:
        async with self._lock:
            if self._is_running:
                raise RuntimeError("Translation is already running")
            targets = list(dict.fromkeys(str(target).strip().lower() for target in (active_targets if active_targets is not None else ["en"])))
            if not targets or any(not is_valid_language_code(target) for target in targets):
                raise ValueError("At least one valid target language is required")
            self.active_targets = targets
            self._is_running = True
            self._is_paused = False
            self._billed_seconds = 0.0
            self._start_time = time.monotonic()
            self._started_at = datetime.now().astimezone()
            self._pause_start = None
            self._paused_duration_s = 0.0
            self.sessions.clear()
            for target in targets:
                if target in self.broadcasters:
                    self.broadcasters[target].reset()
                self.sessions[target] = self._create_session_for_target(target)
            await asyncio.gather(*(session.start() for session in self.sessions.values()))
            self.audio.start(device_index=device_index)
            self._pipe_task = asyncio.create_task(self._audio_pipe())
            server_log.info("Translation started, targets=%s", targets)

    async def pause_clean(self) -> None:
        async with self._lock:
            if not self._is_running or self._is_paused:
                return
            self._is_paused = True
            self._pause_start = time.monotonic()
            self.audio.pause()

            for b in self.broadcasters.values():
                b.drain_audio_clients()
                b._push(CaptionEvent(kind="paused"))

            pause_coros = [s.pause_clean() for s in self.sessions.values()]
            if pause_coros:
                await asyncio.gather(*pause_coros, return_exceptions=True)
            server_log.info("[TranslationManager] All target sessions paused cleanly.")

    async def resume_clean(self) -> None:
        async with self._lock:
            if not self._is_running or not self._is_paused:
                return
            self.audio.drain()
            self.audio.resume()

            for b in self.broadcasters.values():
                b.drain_audio_clients()
                b._push(CaptionEvent(kind="resumed"))

            resume_coros = [s.resume_clean() for s in self.sessions.values()]
            if resume_coros:
                await asyncio.gather(*resume_coros, return_exceptions=True)
            self._is_paused = False
            if self._pause_start is not None:
                self._paused_duration_s += max(0.0, time.monotonic() - self._pause_start)
                self._pause_start = None
            server_log.info("[TranslationManager] All target sessions resumed cleanly.")

    async def stop(self) -> None:
        async with self._lock:
            if not self._is_running:
                return
            self._is_running = False
            self._is_paused = False
            if self._pipe_task and not self._pipe_task.done():
                self._pipe_task.cancel()
                await asyncio.gather(self._pipe_task, return_exceptions=True)
            self._pipe_task = None
            self.audio.stop()
            await asyncio.gather(*(session.stop() for session in self.sessions.values()), return_exceptions=True)
            self.sessions.clear()
            server_log.info("Translation and capture stopped")




    def state(self) -> dict:
        """Return the composite state of all active sessions and the audio pipeline."""
        session_states = {}
        rt = round(self.runtime_seconds, 1)
        bs = round(self.billed_seconds, 1)
        for target, s in self.sessions.items():
            st = s.state
            session_states[target] = {
                "status": st.status.value if hasattr(st.status, "value") else str(st.status),
                "reconnect_count": st.reconnect_count,
                "last_event": st.last_event,
                "latency_ms": st.last_latency_ms,
                "last_update": st.last_update,
                "epoch": s.session_epoch,
                "dropped_audio_chunks": getattr(s, "dropped_audio_chunks", 0),
                "runtime_seconds": rt,
                "billed_seconds": bs,
                "output_hidden": st.output_hidden,
                "audio_muted": st.audio_muted,
                "error_kind": st.error_kind,
            }

        ast = getattr(self.audio, "state", None)
        audio_status = "stopped"
        level_rms = 0.0
        device_name = ""
        if ast:
            audio_status = ast.status.value if hasattr(ast.status, "value") else str(ast.status)
            level_rms = getattr(ast, "level_rms", 0.0)
            device_name = getattr(ast, "device_name", "")

        return {
            "is_running": self._is_running,
            "is_paused": self._is_paused,
            "active_targets": list(self.active_targets),
            "sessions": session_states,
            "audio": {
                "status": audio_status,
                "level_rms": level_rms,
                "device_name": device_name,
            },
        }

