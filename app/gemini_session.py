"""
app/gemini_session.py — Gemini Live API Session Manager
========================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Manages a single, long-running Gemini Live API WebSocket session for the
duration of a church service (typically 60–90 minutes).

Session lifecycle
-----------------
1. ``resolve_live_model()`` queries the Gemini model list at startup and
   selects the best available live-translate model, saving it to config.yaml.
2. ``GeminiSession.start()`` spawns ``_run_with_retry()`` as an asyncio Task.
3. ``_run_session()`` opens the WebSocket, then launches two concurrent tasks:
     • ``_send_loop()`` — drains the audio queue and forwards PCM chunks to
       Gemini via ``send_realtime_input()``.
     • ``_recv_loop()`` — iterates Gemini response frames, dispatching:
         - Audio PCM  → ``on_audio_chunk()`` callback (24 kHz PCM16)
         - Korean transcript → ``on_source_transcript()`` + internal buffer
         - English caption delta → ``on_caption()`` callback + internal buffer
         - ``turn_complete`` → commit current turn to the transcript log
         - ``session_resumption_update`` → store handle for reconnect
         - ``go_away`` → raise RuntimeError to trigger controlled reconnect
4. On error or GoAway, ``_run_with_retry()`` reconnects with exponential
   backoff (up to ``MAX_RECONNECT_ATTEMPTS`` = 3 attempts).

Model config decisions
----------------------
``gemini-3.5-live-translate-preview`` (translate model):
  • ``response_modalities=["AUDIO"]``  — TEXT-only is not supported on this model
  • ``translation_config`` with ``target_language_code="en"``
  • ``voice_config`` pinned to ``"orus"`` (deep male) for voice consistency
  • ``input_audio_transcription`` — surfaces Korean source text
  • ``output_audio_transcription`` — surfaces English translated text
  • ``context_window_compression`` → SlidingWindow to prevent context overflow
    during a 90-minute service
  • ``session_resumption`` → passes the stored handle so the model context is
    preserved across mandatory ~10-minute GoAway reconnects

Transcript export
-----------------
Every ``turn_complete`` frame commits a ``TranscriptEntry(timestamp, korean,
english)`` to the in-memory list.  On ``stop()``, ``_write_session_log()``
in server.py exports these to timestamped text files under
``logs/sessions/YYYYMMDD_HHMMSS/``.

Caption latency
---------------
First English token arrival relative to first audio chunk sent = ~2.2 s,
measured as ``last_latency_ms`` and shown in the operator status monitor.
"""
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, NamedTuple

from google import genai
from google.genai import types

from app.config import gemini_api_key, gemini_model, save_gemini_model
from app.logger import session_log

def _model_rank(name: str) -> tuple:
    """Sort key: translate models first, then by version number descending, stable > preview."""
    is_translate = "translate" in name
    is_preview = "preview" in name
    # Extract leading version digits (e.g. "3.5" → (3, 5), "3.1" → (3, 1))
    import re
    nums = tuple(int(x) for x in re.findall(r"\d+", name.split("live")[0]))
    return (is_translate, nums, not is_preview)


def resolve_live_model() -> str:
    """Pick the best available Live API model at startup, update config, and return it.

    Selection order: translate models first (highest version), then other live
    models. Re-runs on every server start so upgrades are picked up automatically.
    """
    try:
        client = genai.Client(api_key=gemini_api_key())
        live_models = [
            m.name.removeprefix("models/")
            for m in client.models.list()
            if "live" in m.name
        ]
        if live_models:
            chosen = max(live_models, key=_model_rank)
            save_gemini_model(chosen)
            session_log.info("Auto-selected Gemini model: %s", chosen)
            return chosen
    except Exception as e:
        session_log.warning(
            "Model auto-detection failed, using config value: %s", e)
    return gemini_model()


GEMINI_MODEL = resolve_live_model()

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
    ):
        self._on_caption = on_caption
        self._on_state = on_state_change
        self._on_source = on_source_transcript
        self._on_audio = on_audio_chunk
        self._state = SessionState()
        self._stop_event = asyncio.Event()
        self._resumption_handle: str | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self._task: asyncio.Task | None = None
        self._client = genai.Client(api_key=gemini_api_key())
        self._transcript: list[TranscriptEntry] = []
        self._current_ko: str = ""
        self._current_en: str = ""
        self._turn_start: float | None = None
        self._first_audio_in_turn_sent_at: float | None = None

    @property
    def transcript(self) -> list[TranscriptEntry]:
        return list(self._transcript)

    def reset_transcript(self) -> None:
        self._transcript.clear()
        self._current_ko = ""
        self._current_en = ""
        self._turn_start = None
        self._first_audio_in_turn_sent_at = None

    def flush_current_turn(self) -> None:
        """Commit any in-progress turn to the transcript (called on stop)."""
        if self._current_ko or self._current_en:
            self._transcript.append(TranscriptEntry(
                timestamp=self._turn_start or time.monotonic(),
                korean=self._current_ko.strip(),
                english=self._current_en.strip(),
            ))
            self._current_ko = ""
            self._current_en = ""
            self._turn_start = None

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
        self._task = asyncio.create_task(self._run_with_retry())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._emit(status=SessionStatus.STOPPED,
                   last_event="Stopped by operator")

    async def send_audio(self, chunk: bytes) -> None:
        try:
            self._audio_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass  # drop under backpressure rather than stall

    async def _run_with_retry(self) -> None:
        attempt = 0
        MAX_BACKOFF_SECONDS = 60.0
        while not self._stop_event.is_set():
            try:
                is_resume = self._resumption_handle is not None
                if attempt > 0 or is_resume:
                    status = SessionStatus.RECONNECTING
                    event_msg = f"Reconnecting (attempt {attempt})" if attempt > 0 else "Reconnecting (resuming session)"
                else:
                    status = SessionStatus.CONNECTING
                    event_msg = "Connecting to Gemini"
                self._emit(status=status, last_event=event_msg)
                session_log.info(event_msg)

                await self._run_session()
                attempt = 0  # reset on clean run completion
                if self._stop_event.is_set():
                    return

            except asyncio.CancelledError:
                session_log.info("Retry loop cancelled — exiting cleanly")
                raise  # must re-raise, never swallow

            except Exception as e:
                # 1000 = clean WebSocket close (triggered by our own stop()); not a real error
                if "1000" in str(e) and self._stop_event.is_set():
                    return
                attempt += 1
                if attempt >= MAX_RECONNECT_ATTEMPTS:
                    session_log.error(
                        "Max reconnect attempts reached — translation unavailable")
                    self._emit(
                        status=SessionStatus.FAILED,
                        last_event=f"Translation unavailable: {e}",
                    )
                    return
                delay = min(RECONNECT_BASE_DELAY * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
                session_log.warning(
                    "Session error (attempt %d): %s — retrying in %.1fs",
                    attempt, e, delay
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    def _build_config(self) -> types.LiveConnectConfig:
        is_translate_model = "translate" in GEMINI_MODEL
        if is_translate_model:
            # gemini-3.5-live-translate-preview: use translation_config.
            # Korean source: server_content.input_transcription.text  (enabled by input_audio_transcription)
            # English output: server_content.output_transcription.text (enabled by output_audio_transcription)
            # Audio PCM also arrives in model_turn inline_data — we discard it (text captions only).
            return types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                translation_config=types.TranslationConfig(
                    target_language_code="en",
                    echo_target_language=True,
                ),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name="orus",  # deep male voice — consistent across sessions
                        )
                    )
                ),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                context_window_compression=types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow(),
                ),
                session_resumption=types.SessionResumptionConfig(
                    handle=self._resumption_handle,
                ),
            )
        else:
            # General live model: TEXT modality with system prompt translation.
            return types.LiveConnectConfig(
                response_modalities=["TEXT"],
                system_instruction=SYSTEM_PROMPT,
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
    ):
        self._on_caption = on_caption
        self._on_state = on_state_change
        self._on_source = on_source_transcript
        self._on_audio = on_audio_chunk
        self._state = SessionState()
        self._stop_event = asyncio.Event()
        self._resumption_handle: str | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self._task: asyncio.Task | None = None
        self._client = genai.Client(api_key=gemini_api_key())
        self._transcript: list[TranscriptEntry] = []
        self._current_ko: str = ""
        self._current_en: str = ""
        self._turn_start: float | None = None
        self._first_audio_in_turn_sent_at: float | None = None

    @property
    def transcript(self) -> list[TranscriptEntry]:
        return list(self._transcript)

    def reset_transcript(self) -> None:
        self._transcript.clear()
        self._current_ko = ""
        self._current_en = ""
        self._turn_start = None
        self._first_audio_in_turn_sent_at = None

    def flush_current_turn(self) -> None:
        """Commit any in-progress turn to the transcript (called on stop)."""
        if self._current_ko or self._current_en:
            self._transcript.append(TranscriptEntry(
                timestamp=self._turn_start or time.monotonic(),
                korean=self._current_ko.strip(),
                english=self._current_en.strip(),
            ))
            self._current_ko = ""
            self._current_en = ""
            self._turn_start = None

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
        self._task = asyncio.create_task(self._run_with_retry())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._emit(status=SessionStatus.STOPPED,
                   last_event="Stopped by operator")

    async def send_audio(self, chunk: bytes) -> None:
        try:
            self._audio_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass  # drop under backpressure rather than stall

    async def _run_with_retry(self) -> None:
        attempt = 0
        MAX_BACKOFF_SECONDS = 60.0
        while not self._stop_event.is_set():
            try:
                is_resume = self._resumption_handle is not None
                if attempt > 0 or is_resume:
                    status = SessionStatus.RECONNECTING
                    event_msg = f"Reconnecting (attempt {attempt})" if attempt > 0 else "Reconnecting (resuming session)"
                else:
                    status = SessionStatus.CONNECTING
                    event_msg = "Connecting to Gemini"
                self._emit(status=status, last_event=event_msg)
                session_log.info(event_msg)

                await self._run_session()
                attempt = 0  # reset on clean run completion
                if self._stop_event.is_set():
                    return

            except asyncio.CancelledError:
                session_log.info("Retry loop cancelled — exiting cleanly")
                raise  # must re-raise, never swallow

            except Exception as e:
                # 1000 = clean WebSocket close (triggered by our own stop()); not a real error
                if "1000" in str(e) and self._stop_event.is_set():
                    return
                attempt += 1
                if attempt >= MAX_RECONNECT_ATTEMPTS:
                    session_log.error(
                        "Max reconnect attempts reached — translation unavailable")
                    self._emit(
                        status=SessionStatus.FAILED,
                        last_event=f"Translation unavailable: {e}",
                    )
                    return
                delay = min(RECONNECT_BASE_DELAY * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
                session_log.warning(
                    "Session error (attempt %d): %s — retrying in %.1fs",
                    attempt, e, delay
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    def _build_config(self) -> types.LiveConnectConfig:
        is_translate_model = "translate" in GEMINI_MODEL
        if is_translate_model:
            # gemini-3.5-live-translate-preview: use translation_config.
            # Korean source: server_content.input_transcription.text  (enabled by input_audio_transcription)
            # English output: server_content.output_transcription.text (enabled by output_audio_transcription)
            # Audio PCM also arrives in model_turn inline_data — we discard it (text captions only).
            return types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                translation_config=types.TranslationConfig(
                    target_language_code="en",
                    echo_target_language=True,
                ),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name="orus",  # deep male voice — consistent across sessions
                        )
                    )
                ),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                context_window_compression=types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow(),
                ),
                session_resumption=types.SessionResumptionConfig(
                    handle=self._resumption_handle,
                ),
            )
        else:
            # General live model: TEXT modality with system prompt translation.
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

    async def _run_session(self) -> None:
        config = self._build_config()

        async with self._client.aio.live.connect(
            model=GEMINI_MODEL, config=config
        ) as session:
            self._emit(status=SessionStatus.CONNECTED, last_event="Session connected",
                       reconnect_count=self._state.reconnect_count)
            session_log.info("Gemini session connected (model=%s, resume=%s)",
                             GEMINI_MODEL, bool(self._resumption_handle))
            self._resumption_handle = None  # consumed

            send_task = asyncio.create_task(self._send_loop(session))
            recv_task = asyncio.create_task(self._recv_loop(session))
            stop_task = asyncio.create_task(self._stop_event.wait())

            done: set = set()
            try:
                done, pending = await asyncio.wait(
                    [send_task, recv_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            finally:
                for t in [send_task, recv_task, stop_task]:
                    if not t.done():
                        t.cancel()

            # Re-raise exception from recv only if it's not a clean stop
            for t in done:
                if t != stop_task and not t.cancelled() and t.exception():
                    ex = t.exception()
                    if "1000" in str(ex) and self._stop_event.is_set():
                        continue
                    raise ex

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

    async def _recv_loop(self, session) -> None:
        try:
            async for response in session.receive():
                if self._stop_event.is_set():
                    break

                # Session resumption handle update
                if hasattr(response, "session_resumption_update") and response.session_resumption_update:
                    update = response.session_resumption_update
                    if hasattr(update, "handle") and update.handle:
                        self._resumption_handle = update.handle
                        session_log.debug("Resumption handle updated")

                # GoAway — reconnect before the connection actually drops
                if hasattr(response, "go_away") and response.go_away:
                    session_log.info(
                        "GoAway received — initiating graceful reconnect")
                    self._emit(last_event="GoAway: reconnecting")
                    raise RuntimeError("GoAway")

                sc = getattr(response, "server_content", None)

                # Translated audio PCM — model_turn.parts[].inline_data (24kHz PCM16 mono)
                if sc:
                    for part in getattr(getattr(sc, "model_turn", None), "parts", None) or []:
                        blob = getattr(part, "inline_data", None)
                        if blob and self._on_audio:
                            self._on_audio(blob.data)

                # Korean source transcript (log + transcript export)
                if sc:
                    it = getattr(sc, "input_transcription", None)
                    if it and getattr(it, "text", None):
                        self._current_ko += it.text
                        session_log.info("[KO] %s", it.text)
                        if self._on_source:
                            self._on_source(it.text)

                # English translation — two paths:
                # 1. Translate model: server_content.output_transcription.text
                # 2. General model:   response.text
                en_text = response.text or ""
                if not en_text and sc:
                    ot = getattr(sc, "output_transcription", None)
                    if ot and getattr(ot, "text", None):
                        en_text = ot.text

                if en_text:
                    if self._turn_start is None:
                        self._turn_start = time.monotonic()
                        if self._first_audio_in_turn_sent_at is not None:
                            latency_ms = (self._turn_start - self._first_audio_in_turn_sent_at) * 1000
                            self._emit(last_latency_ms=latency_ms)
                    self._current_en += en_text
                    self._on_caption(en_text)
                    session_log.debug("[EN delta] %s", en_text)

                # Turn complete — commit to transcript, reset buffers
                if sc and getattr(sc, "turn_complete", False):
                    if self._current_en or self._current_ko:
                        self._transcript.append(TranscriptEntry(
                            timestamp=self._turn_start or time.monotonic(),
                            korean=self._current_ko.strip(),
                            english=self._current_en.strip(),
                        ))
                        session_log.info(
                            "[EN turn] %s", self._current_en.strip())
                    self._current_ko = ""
                    self._current_en = ""
                    self._turn_start = None
                    self._first_audio_in_turn_sent_at = None

        except Exception as e:
            if "1000" in str(e) and self._stop_event.is_set():
                return  # clean stop — not an error
            if not self._stop_event.is_set():
                raise
