"""
app/gemini_session.py — Gemini Live API Session Manager
========================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Manages a single, long-running Gemini Live API WebSocket session for the
duration of a church service (typically 60–90 minutes).

Session lifecycle
-----------------
1. ``model_resolver.get_candidate_sequence()`` determines the priority
   of models to try on service start (Recommended / Auto / Manual).
2. ``GeminiSession.start()`` spawns ``_run_with_retry()`` as an asyncio Task.
3. Once an initial candidate establishes a connection, it is LOCKED for the
   duration of the service session.
4. On first verified translation output (audio or text), the model is recorded
   as Last Known Good (LKG).
5. Subsequent in-session reconnects (e.g. GoAway) always use the locked model.
6. On ``stop()``, transcript is flushed, and the model is unlocked.
"""
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, NamedTuple

from google import genai
from google.genai import types

from app.config import gemini_api_key, gemini_cfg
from app.events import operator_events
from app.logger import session_log, server_log
from app.model_resolver import model_resolver

SYSTEM_PROMPT = (
    "You are a real-time simultaneous interpreter for a church service. "
    "You will hear continuous Korean speech from a live sermon. "
    "Output ONLY the English translation, as a continuous stream, matching "
    "the pacing of the speaker. Do not wait for sentence completion if a "
    "clause's meaning is already clear — begin translating as soon as possible "
    "and revise if needed. Do not add commentary, labels, speaker names, or "
    "explanations. Do not translate filler words or false starts literally; "
    "smooth them naturally. If audio is silent or unintelligible, output nothing."
)

MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_BASE_DELAY = 2.0  # seconds, doubled each attempt


class SessionStatus(str, Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"  # sustained failure after max retries


@dataclass
class SessionState:
    status: SessionStatus = SessionStatus.STOPPED
    reconnect_count: int = 0
    last_event: str = ""
    last_latency_ms: float = 0.0
    last_update: float = field(default_factory=time.monotonic)


class TranscriptEntry(NamedTuple):
    timestamp: float   # time.monotonic() of turn start
    korean: str
    english: str


class GeminiSession:
    def __init__(
        self,
        on_caption: Callable[[str], None],
        on_state_change: Callable[[SessionState], None] | None = None,
        on_source_transcript: Callable[[str], None] | None = None,
        on_audio_chunk: Callable[[bytes], None] | None = None,
        glossary=None,  # GlossaryCorrector | None
    ):
        self._on_caption = on_caption
        self._on_state = on_state_change
        self._on_source = on_source_transcript
        self._on_audio = on_audio_chunk
        self._glossary = glossary
        self._state = SessionState()
        self._stop_event = asyncio.Event()
        self._attempt = 0
        self._resumption_handle: str | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self._task: asyncio.Task | None = None
        self._client: genai.Client | None = None
        self._transcript: list[TranscriptEntry] = []
        self._current_ko: str = ""
        self._current_en: str = ""
        self._turn_start: float | None = None
        self._first_audio_in_turn_sent_at: float | None = None
        self._last_token_at: float = 0.0
        self._has_verified_output: bool = False

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=gemini_api_key())
        return self._client

    @property
    def current_model(self) -> str:
        return model_resolver.active_model

    @property
    def transcript(self) -> list[TranscriptEntry]:
        return list(self._transcript)

    def reset_transcript(self) -> None:
        self._transcript.clear()
        self._current_ko = ""
        self._current_en = ""
        self._turn_start = None
        self._first_audio_in_turn_sent_at = None
        self._has_verified_output = False

    def flush_current_turn(self) -> None:
        """Commit any in-progress turn to the transcript (called on stop)."""
        self._commit_current_turn()

    def _commit_current_turn(self) -> None:
        if self._current_ko.strip() or self._current_en.strip():
            ko = self._current_ko.strip()
            en = self._current_en.strip()
            if self._glossary and ko:
                en = self._glossary.correct(ko, en)
            self._transcript.append(TranscriptEntry(
                timestamp=self._turn_start or time.monotonic(),
                korean=ko,
                english=en,
            ))
            session_log.info(
                "[Turn committed] KO: %s | EN: %s",
                ko,
                en,
            )
        self._current_ko = ""
        self._current_en = ""
        self._turn_start = None
        self._first_audio_in_turn_sent_at = None

    async def _auto_commit_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(0.5)
                if self._turn_start is not None and (self._current_ko or self._current_en):
                    silence_duration = time.monotonic() - self._last_token_at
                    if silence_duration >= 1.5:  # matches PAUSE_THRESHOLD_S
                        self._commit_current_turn()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            session_log.error("Error in auto-commit loop: %s", e)

    def _emit(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self._state, k, v)
        self._state.last_update = time.monotonic()
        if self._on_state:
            self._on_state(SessionState(**vars(self._state)))

    @property
    def state(self) -> SessionState:
        return SessionState(**vars(self._state))

    async def start(self) -> None:
        self._stop_event.clear()
        self._has_verified_output = False
        self._task = asyncio.create_task(self._run_with_retry())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        model_resolver.unlock_session()
        self._emit(status=SessionStatus.STOPPED,
                   last_event="Stopped by operator")
        operator_events.add("gemini", "Gemini session stopped")

    async def send_audio(self, chunk: bytes) -> None:
        try:
            self._audio_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass  # drop under backpressure rather than stall

    async def _run_with_retry(self) -> None:
        self._attempt = 0
        MAX_BACKOFF_SECONDS = 60.0

        # Phase 1: If session is not locked, resolve candidate sequence (Initial Connection Fallback)
        if model_resolver.locked_model is None:
            candidates = model_resolver.get_candidate_sequence()
            connected_model = None
            primary_candidate = candidates[0] if candidates else model_resolver.fallback_model

            for idx, candidate in enumerate(candidates):
                if self._stop_event.is_set():
                    return
                try:
                    self._emit(
                        status=SessionStatus.CONNECTING,
                        last_event=f"Connecting to {candidate}",
                        reconnect_count=0,
                    )
                    server_log.info("Attempting initial connection with candidate [%d/%d]: %s", idx + 1, len(candidates), candidate)
                    operator_events.add("gemini", f"Connecting to {candidate}")

                    is_fallback = (candidate != primary_candidate)
                    fallback_reason = f"Primary candidate '{primary_candidate}' failed; fell back to '{candidate}'" if is_fallback else ""

                    # Lock the model once chosen
                    model_resolver.lock_session(candidate, is_fallback=is_fallback, reason=fallback_reason)
                    connected_model = candidate
                    break

                except Exception as e:
                    server_log.warning("Initial connection candidate %s failed: %s", candidate, e)
                    continue

            if connected_model is None:
                err_msg = f"All candidate models failed: {', '.join(candidates)}"
                server_log.error(err_msg)
                self._emit(status=SessionStatus.FAILED, last_event=err_msg)
                operator_events.add("error", "Gemini connection failed for all candidates", {"candidates": candidates})
                return

        # Phase 2: In-session loop with exponential backoff on GoAway / errors using locked model
        locked_model = model_resolver.locked_model or model_resolver.fallback_model

        while not self._stop_event.is_set():
            try:
                is_resume = self._resumption_handle is not None
                if self._attempt > 0 or is_resume:
                    status = SessionStatus.RECONNECTING
                    event_msg = f"Reconnecting to {locked_model} (attempt {self._attempt})" if self._attempt > 0 else f"Reconnecting to {locked_model} (resuming session)"
                    if self._attempt > 0:
                        operator_events.add("network", f"Reconnecting to Gemini (attempt {self._attempt})", {"attempt": self._attempt, "model": locked_model})
                    else:
                        operator_events.add("gemini", f"Resuming Gemini session ({locked_model})")
                else:
                    status = SessionStatus.CONNECTING
                    event_msg = f"Connected to {locked_model}"
                    operator_events.add("gemini", f"Connected to {locked_model}")

                self._emit(status=status, last_event=event_msg, reconnect_count=self._attempt)
                server_log.info(event_msg)
                session_log.debug(event_msg)

                await self._run_session(model=locked_model, is_reconnect=(self._attempt > 0 or is_resume))
                self._attempt = 0  # reset on clean run completion
                if self._stop_event.is_set():
                    return

            except asyncio.CancelledError:
                server_log.info("Retry loop cancelled — exiting cleanly")
                session_log.debug("Retry loop cancelled — exiting cleanly")
                raise

            except Exception as e:
                if "1000" in str(e) and self._stop_event.is_set():
                    return

                is_goaway = "GoAway" in str(e)
                if is_goaway:
                    delay = 0.2
                    server_log.info("GoAway received — reconnecting immediately in %.1fs", delay)
                    session_log.debug("GoAway received — reconnecting immediately in %.1fs", delay)
                else:
                    self._attempt += 1
                    if self._attempt >= MAX_RECONNECT_ATTEMPTS:
                        server_log.error("Max reconnect attempts reached for %s — translation unavailable", locked_model)
                        session_log.debug("Max reconnect attempts reached for %s — translation unavailable", locked_model)
                        self._emit(
                            status=SessionStatus.FAILED,
                            last_event=f"Translation unavailable: {e}",
                            reconnect_count=self._attempt
                        )
                        operator_events.add("error", "Gemini failed: max reconnects reached",
                                            {"error": str(e), "attempts": self._attempt, "model": locked_model})
                        return
                    delay = min(RECONNECT_BASE_DELAY * (2 ** (self._attempt - 1)), MAX_BACKOFF_SECONDS)
                    server_log.warning(
                        "Session error (attempt %d on %s): %s — retrying in %.1fs",
                        self._attempt, locked_model, e, delay
                    )
                    session_log.debug(
                        "Session error (attempt %d on %s): %s — retrying in %.1fs",
                        self._attempt, locked_model, e, delay
                    )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    def _build_config(self, model_name: str) -> types.LiveConnectConfig:
        voice = gemini_cfg().get("voice", "orus")
        is_translate_model = "translate" in model_name.lower()

        if is_translate_model:
            return types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                translation_config=types.TranslationConfig(
                    target_language_code="en",
                    echo_target_language=True,
                ),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice,
                        )
                    )
                ),
                input_audio_transcription=types.AudioTranscriptionConfig(
                    language_hints=types.LanguageHints(language_codes=["ko", "en"]),
                ),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                context_window_compression=types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow(),
                ),
                session_resumption=types.SessionResumptionConfig(
                    handle=self._resumption_handle,
                ),
            )
        else:
            # Fallback for general models with system prompt translation
            return types.LiveConnectConfig(
                response_modalities=["TEXT"],
                system_instruction=SYSTEM_PROMPT,
                input_audio_transcription=types.AudioTranscriptionConfig(),
                context_window_compression=types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow(),
                ),
                session_resumption=types.SessionResumptionConfig(
                    handle=self._resumption_handle,
                ),
            )

    async def _run_session(self, model: str, is_reconnect: bool) -> None:
        config = self._build_config(model)
        is_resuming = self._resumption_handle is not None

        server_log.info(
            "Session connect starting: model=%s resumption_handle_present=%s",
            model,
            is_resuming
        )

        client = self._get_client()
        try:
            async with client.aio.live.connect(
                model=model, config=config
            ) as session:
                self._attempt = 0
                self._emit(
                    status=SessionStatus.CONNECTED,
                    last_event="Connected to Gemini",
                    reconnect_count=0,
                )
                server_log.info("Gemini Live session connected successfully on model: %s", model)
                operator_events.add("gemini", f"Live translation active ({model})")

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._send_loop(session))
                    tg.create_task(self._recv_loop(session, model))
                    tg.create_task(self._auto_commit_loop())
                    while not self._stop_event.is_set():
                        await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            if self._stop_event.is_set():
                return
            raise
        except Exception as e:
            if "1000" in str(e) and self._stop_event.is_set():
                return
            log_fn = server_log.info if "GoAway" in str(e) else server_log.error
            log_fn(
                "SESSION_FAILURE: model=%s type=%s message=%s",
                model,
                type(e).__name__,
                str(e)
            )
            raise e

    async def _send_loop(self, session) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                    if self._turn_start is None and not self._current_ko:
                        self._first_audio_in_turn_sent_at = time.monotonic()
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=chunk, mime_type="audio/pcm;rate=16000")
                    )
                except asyncio.TimeoutError:
                    continue
        except Exception:
            if not self._stop_event.is_set():
                raise

    async def _recv_loop(self, session, model: str) -> None:
        try:
            async for response in session.receive():
                if self._stop_event.is_set():
                    break

                if hasattr(response, "session_resumption_update") and response.session_resumption_update:
                    update = response.session_resumption_update
                    if hasattr(update, "handle") and update.handle:
                        self._resumption_handle = update.handle
                        session_log.debug("Resumption handle updated")

                if hasattr(response, "go_away") and response.go_away:
                    server_log.info("GoAway received — initiating graceful reconnect")
                    self._emit(last_event="GoAway: reconnecting")
                    operator_events.add("network", "GoAway — reconnecting")
                    raise RuntimeError("GoAway")

                sc = getattr(response, "server_content", None)

                # Audio PCM chunks (24kHz PCM16 mono)
                if sc:
                    for part in getattr(getattr(sc, "model_turn", None), "parts", None) or []:
                        blob = getattr(part, "inline_data", None)
                        if blob and self._on_audio:
                            self._on_audio(blob.data)
                            if not self._has_verified_output:
                                self._has_verified_output = True
                                model_resolver.record_verified_success(model)

                # Korean source text
                if sc:
                    it = getattr(sc, "input_transcription", None)
                    if it and getattr(it, "text", None):
                        self._current_ko += it.text
                        self._last_token_at = time.monotonic()
                        session_log.info("[KO] %s", it.text)
                        if self._on_source:
                            self._on_source(it.text)

                # English translated text
                en_text = response.text or ""
                if not en_text and sc:
                    ot = getattr(sc, "output_transcription", None)
                    if ot and getattr(ot, "text", None):
                        en_text = ot.text

                if en_text:
                    if not self._has_verified_output:
                        self._has_verified_output = True
                        model_resolver.record_verified_success(model)

                    if self._turn_start is None:
                        self._turn_start = time.monotonic()
                        if self._first_audio_in_turn_sent_at is not None:
                            latency_ms = (self._turn_start - self._first_audio_in_turn_sent_at) * 1000
                            self._emit(last_latency_ms=latency_ms)
                    self._current_en += en_text
                    self._last_token_at = time.monotonic()
                    self._on_caption(en_text)
                    session_log.debug("[EN delta] %s", en_text)

                if sc and getattr(sc, "turn_complete", False):
                    self._commit_current_turn()

        except Exception as e:
            if "1000" in str(e) and self._stop_event.is_set():
                return
            if not self._stop_event.is_set():
                raise
