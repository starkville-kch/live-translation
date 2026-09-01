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
from typing import Callable, Dict, List, Optional

from app.audio import AudioCapture
from app.broadcast import CaptionBroadcaster, CaptionEvent
from app.config import gemini_cfg
from app.gemini_session import GeminiSession, SessionState
from app.languages import is_valid_language_code
from app.logger import server_log


class TranslationManager:
    def __init__(
        self,
        audio_capture: Optional[AudioCapture] = None,
        on_caption: Optional[Callable[[str, str], None]] = None,
        on_source: Optional[Callable[[str], None]] = None,
        on_audio: Optional[Callable[[str, bytes], None]] = None,
        on_session_state: Optional[Callable[[str, SessionState], None]] = None,
        glossary=None,
        default_target: str = "en",
        default_source: str = "ko",
    ):
        self.audio: AudioCapture = audio_capture or AudioCapture()
        self._on_caption = on_caption
        self._on_source = on_source
        self._on_audio = on_audio
        self._on_session_state = on_session_state
        self._glossary = glossary

        self.sessions: Dict[str, GeminiSession] = {}
        self.broadcasters: Dict[str, CaptionBroadcaster] = {}
        self.active_targets: List[str] = [default_target]
        self.expected_source_language: str = default_source
        self.auto_drift_correction: bool = bool(gemini_cfg().get("auto_drift_correction", False))


        # Ensure default primary broadcaster is available before start()
        self.broadcasters[default_target] = CaptionBroadcaster(
            glossary=self._glossary if (default_source == "ko" and default_target == "en") else None,
            source_lang=default_source,
            target_lang=default_target,
        )

        self._is_running: bool = False
        self._is_paused: bool = False
        self._pipe_task: Optional[asyncio.Task] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._billed_seconds: float = 0.0
        self._start_time: Optional[float] = None
        self._pause_start: Optional[float] = None

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
    def runtime_seconds(self) -> float:
        if self._is_running and self._start_time is not None:
            return max(0.0, time.monotonic() - self._start_time)
        return 0.0


    @property
    def primary_target(self) -> str:
        return self.active_targets[0] if self.active_targets else "en"

    @property
    def primary_broadcaster(self) -> CaptionBroadcaster:
        tgt = self.primary_target
        if tgt not in self.broadcasters:
            self.broadcasters[tgt] = CaptionBroadcaster(
                glossary=self._glossary if (self.expected_source_language == "ko" and tgt == "en") else None,
                source_lang=self.expected_source_language,
                target_lang=tgt,
            )
        return self.broadcasters[tgt]

    def get_broadcaster(self, target: str) -> Optional[CaptionBroadcaster]:
        clean = (target or "").lower().strip()
        return self.broadcasters.get(clean)

    def set_auto_drift_correction(self, enabled: bool) -> None:
        self.auto_drift_correction = bool(enabled)
        for s in self.sessions.values():
            s.set_auto_drift_correction(self.auto_drift_correction)
            s.clear_drift_state()

    def _create_session_for_target(self, target: str, source: str) -> GeminiSession:
        if target not in self.broadcasters:
            self.broadcasters[target] = CaptionBroadcaster(
                glossary=self._glossary if (source == "ko" and target == "en") else None,
                source_lang=source,
                target_lang=target,
            )
        broadcaster = self.broadcasters[target]


        def _caption_cb(text: str) -> None:
            broadcaster.on_caption_delta(text)
            if self._on_caption:
                self._on_caption(target, text)

        def _state_cb(state: SessionState) -> None:
            if self._on_session_state:
                self._on_session_state(target, state)

        def _source_cb(src_text: str) -> None:
            # Distribute source text deltas across all broadcasters for tap-to-reveal original transcripts
            for b in self.broadcasters.values():
                b.on_source_delta(src_text)
            if self._on_source:
                self._on_source(src_text)

        def _audio_cb(pcm_bytes: bytes) -> None:
            broadcaster.on_audio_chunk(pcm_bytes)
            if self._on_audio:
                self._on_audio(target, pcm_bytes)

        sess = GeminiSession(
            on_caption=_caption_cb,
            on_state_change=_state_cb,
            on_source_transcript=_source_cb,
            on_audio_chunk=_audio_cb,
            glossary=self._glossary if (source == "ko" and target == "en") else None,
            target_language_code=target,
            expected_source_language=source,
        )
        sess.set_auto_drift_correction(self.auto_drift_correction)
        return sess



    async def _audio_pipe(self) -> None:
        """Non-blocking fan-out loop: pushes 16kHz PCM chunks to all active target session queues."""
        CHUNK_MS = 100
        try:
            async for chunk in self.audio.chunks():
                if not self._is_paused and self._is_running:
                    self._billed_seconds += (CHUNK_MS / 1000.0)
                    for target, session in list(self.sessions.items()):
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

    async def start(
        self,
        device_index: Optional[int] = None,
        active_targets: Optional[List[str]] = None,
        expected_source_language: str = "ko",
    ) -> None:
        async with self._lock:
            if self._is_running:
                raise RuntimeError("TranslationManager is already running. Stop before starting again.")

            # Validate and clean active targets
            raw_targets = active_targets if active_targets is not None else ["en"]
            clean_targets = []
            for t in raw_targets:
                code = str(t).lower().strip()
                if not code:
                    continue
                if not is_valid_language_code(code):
                    raise ValueError(f"Invalid target language code: {t}")
                if code not in clean_targets:
                    clean_targets.append(code)

            if not clean_targets:
                raise ValueError("At least one valid active target language must be specified.")

            clean_src = (expected_source_language or "ko").lower().strip()
            if not is_valid_language_code(clean_src):
                raise ValueError(f"Invalid expected source language code: {expected_source_language}")

            if clean_src in clean_targets:
                raise ValueError(f"Expected source language '{clean_src}' cannot be in active target languages.")

            self.active_targets = clean_targets
            self.expected_source_language = clean_src
            self._is_running = True
            self._is_paused = False
            self._billed_seconds = 0.0
            self._start_time = time.monotonic()
            self._pause_start = None

            # Create / reset broadcasters and sessions for all active targets
            self.sessions.clear()
            for target in self.active_targets:
                if target not in self.broadcasters:
                    self.broadcasters[target] = CaptionBroadcaster(
                        glossary=self._glossary if (clean_src == "ko" and target == "en") else None,
                        source_lang=clean_src,
                        target_lang=target,
                    )
                else:
                    self.broadcasters[target].reset()
                    self.broadcasters[target].source_lang = clean_src
                    self.broadcasters[target].target_lang = target

                session = self._create_session_for_target(target, clean_src)
                self.sessions[target] = session

            # Start all target sessions concurrently
            start_coros = [s.start() for s in self.sessions.values()]
            if start_coros:
                await asyncio.gather(*start_coros, return_exceptions=True)

            # Start single AudioCapture and fan-out pipe
            self.audio.start(device_index=device_index)
            self._pipe_task = asyncio.create_task(self._audio_pipe())
            server_log.info(
                "[TranslationManager] Started translation: src='%s' targets=%s",
                self.expected_source_language,
                self.active_targets,
            )

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
                try:
                    await self._pipe_task
                except asyncio.CancelledError:
                    pass
            self._pipe_task = None

            # Stop AudioCapture
            self.audio.stop()

            # Stop all target sessions
            stop_coros = [s.stop() for s in self.sessions.values()]
            if stop_coros:
                await asyncio.gather(*stop_coros, return_exceptions=True)

            self.sessions.clear()
            server_log.info("[TranslationManager] All target sessions and AudioCapture stopped.")


    async def reset_clean(self, reason: str = "Clean reset") -> None:
        async with self._lock:
            if not self._is_running:
                return
            self.audio.drain()
            reset_coros = [s.reset_clean(reason=reason) for s in self.sessions.values()]
            if reset_coros:
                await asyncio.gather(*reset_coros, return_exceptions=True)
            server_log.info("[TranslationManager] Reset all target sessions cleanly: %s", reason)

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
            "expected_source_language": self.expected_source_language,
            "active_targets": list(self.active_targets),
            "sessions": session_states,
            "audio": {
                "status": audio_status,
                "level_rms": level_rms,
                "device_name": device_name,
            },
        }

