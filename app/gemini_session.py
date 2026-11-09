"""Fixed-model Gemini Live Translate session with automatic source detection."""
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, NamedTuple

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from app.config import gemini_api_key, gemini_cfg, gemini_model
from app.events import operator_events
from app.logger import session_log, server_log
from app.text_corrections import NameCorrector

MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_BASE_DELAY = 2.0
QUOTA_RETRY_DELAYS = (2.0, 5.0)
QUOTA_MESSAGE = (
    "지출 한도 또는 API 할당량·요청 제한에 도달했습니다. / "
    "Spending limit or API quota/rate limit reached."
)


def _matches_exception_text(exc: BaseException | None, target: str) -> bool:
    """Inspect grouped and chained exceptions without losing the original error."""
    if exc is None:
        return False
    if target in repr(exc) or target in str(exc):
        return True
    nested = getattr(exc, "exceptions", ())
    if any(_matches_exception_text(item, target) for item in nested):
        return True
    return _matches_exception_text(exc.__cause__, target) or _matches_exception_text(exc.__context__, target)


def _is_goaway_exception(exc: BaseException | None) -> bool:
    return _matches_exception_text(exc, "GoAway")


def _is_quota_error(exc: BaseException) -> bool:
    """Recognize billing/quota errors through WebSocket task exception groups."""
    message = str(exc).casefold()
    markers = ("429", "resource_exhausted", "quota", "rate limit", "rate_limit", "billing", "spend cap", "spending limit")
    if any(marker in message for marker in markers):
        return True
    nested = getattr(exc, "exceptions", ())
    return any(_is_quota_error(item) for item in nested) or any(
        _is_quota_error(cause) for cause in (exc.__cause__, exc.__context__) if cause is not None
    )


def _is_resumption_rejected(exc: BaseException) -> bool:
    """Only setup rejection qualifies for the one fresh-session fallback."""
    message = repr(exc).casefold()
    return not _is_quota_error(exc) and (
        any(_matches_exception_text(exc, marker) for marker in ("400", "INVALID_ARGUMENT", "1008"))
        or (any(marker in message for marker in ("handle", "resumption", "session"))
            and any(marker in message for marker in ("invalid", "expired", "not found", "rejected")))
    )


class SessionStatus(str, Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class SessionState:
    status: SessionStatus = SessionStatus.STOPPED
    reconnect_count: int = 0
    last_event: str = ""
    last_latency_ms: float = 0.0
    last_update: float = field(default_factory=time.monotonic)
    output_hidden: bool = False
    audio_muted: bool = False
    error_kind: str = ""
    resumed_reconnect_count: int = 0
    fresh_reconnect_count: int = 0


class TranscriptEntry(NamedTuple):
    timestamp: float
    source: str
    target: str
    source_lang: str = "ko"
    target_lang: str = "en"

    @property
    def korean(self) -> str:
        return self.source

    @property
    def english(self) -> str:
        return self.target


class GeminiSession:
    def __init__(
        self,
        on_caption: Callable[[str], None],
        on_state_change: Callable[[SessionState], None] | None = None,
        on_source_transcript: Callable[[str], None] | None = None,
        on_audio_chunk: Callable[[bytes], None] | None = None,
        corrector: NameCorrector | None = None,
        target_language_code: str = "en",
        on_output_visibility: Callable[[bool, bool], None] | None = None,
    ):
        self.target_language_code = (target_language_code or "en").strip().lower()
        self.tag = f"Gemini:{self.target_language_code}"
        self._model = gemini_model()
        self._on_caption = on_caption
        self._on_state = on_state_change
        self._on_source = on_source_transcript
        self._on_audio = on_audio_chunk
        self._on_visibility = on_output_visibility
        self._corrector = corrector or NameCorrector()
        self._state = SessionState()
        self._stop_event = asyncio.Event()
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self._task: asyncio.Task | None = None
        self._client: genai.Client | None = None
        self._session_epoch = 0
        self._attempt = 0
        self._reconnect_count = 0
        self._connected_once = False
        self._resumption_handle: str | None = None
        self._resumption_updated_at: float | None = None
        self._dropped_audio_chunks = 0
        self._transcript: list[TranscriptEntry] = []
        self._clear_turn()

    @property
    def current_model(self) -> str:
        return self._model

    @property
    def session_epoch(self) -> int:
        return self._session_epoch

    @property
    def dropped_audio_chunks(self) -> int:
        return self._dropped_audio_chunks

    @property
    def transcript(self) -> list[TranscriptEntry]:
        return list(self._transcript)

    @property
    def state(self) -> SessionState:
        return SessionState(**vars(self._state))

    def _emit(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self._state, key, value)
        self._state.last_update = time.monotonic()
        if self._on_state:
            self._on_state(self.state)

    def _set_visibility(self, hidden: bool, muted: bool) -> None:
        if (hidden, muted) == (self._state.output_hidden, self._state.audio_muted):
            return
        self._emit(output_hidden=hidden, audio_muted=muted)
        if self._on_visibility:
            self._on_visibility(hidden, muted)

    def _clear_turn(self) -> None:
        self._current_source = ""
        self._current_target = ""
        self._pending_source = ""
        self._pending_target = ""
        self._turn_in_lang: str | None = None
        self._turn_out_lang: str | None = None
        self._turn_start: float | None = None
        self._first_audio_in_turn_sent_at: float | None = None
        self._last_token_at = 0.0

    def reset_transcript(self) -> None:
        self._transcript.clear()
        self._clear_turn()
        self._set_visibility(False, False)

    def flush_current_turn(self) -> None:
        self._commit_current_turn()

    def _commit_current_turn(self) -> None:
        if not (self._current_source.strip() or self._current_target.strip() or self._turn_in_lang):
            return
        source, target = self._current_source.strip(), self._current_target.strip()
        # Language metadata labels the transcript; it does not gate output.
        session_log.info(
            "[%s] [Turn received] source (%s): %s | target (%s): %s",
            self.tag, self._turn_in_lang or "unknown", source,
            self._turn_out_lang or self.target_language_code, target,
        )
        self._transcript.append(TranscriptEntry(
            timestamp=self._turn_start or time.monotonic(),
            source=self._corrector.correct(source),
            target=self._corrector.correct(target),
            source_lang=self._turn_in_lang or "unknown",
            target_lang=self.target_language_code,
        ))
        self._clear_turn()

    def _observe_source_language(self, language: str) -> None:
        self._turn_in_lang = language

    def _publish_pending_text(self) -> None:
        if self._pending_source and self._on_source:
            self._on_source(self._pending_source)
        if self._pending_target:
            self._on_caption(self._pending_target)
        self._pending_source = self._pending_target = ""

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=gemini_api_key())
        return self._client

    def _drain_audio_queue(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _clear_resumption(self) -> None:
        self._resumption_handle = None
        self._resumption_updated_at = None

    async def start(self) -> None:
        self._stop_event.clear()
        self._session_epoch += 1
        self._reconnect_count = 0
        self._connected_once = False
        self._clear_resumption()
        self._emit(reconnect_count=0, resumed_reconnect_count=0, fresh_reconnect_count=0)
        self._clear_turn()
        self._set_visibility(False, False)
        self._drain_audio_queue()
        self._task = asyncio.create_task(self._run_with_retry())

    async def _close_current_session(self) -> None:
        self._stop_event.set()
        self.flush_current_turn()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._clear_turn()
        self._set_visibility(False, False)
        self._drain_audio_queue()

    async def pause_clean(self) -> None:
        await self._close_current_session()
        self._emit(status=SessionStatus.STOPPED, last_event="Paused (clean standby)", reconnect_count=self._reconnect_count)
        operator_events.add("gemini", "Gemini translation paused (clean standby)")

    async def resume_clean(self) -> None:
        self._session_epoch += 1
        self._stop_event.clear()
        self._clear_turn()
        self._set_visibility(False, False)
        self._drain_audio_queue()
        server_log.info("Resuming Gemini session cleanly (epoch %d) on configured model: %s", self._session_epoch, self._model)
        self._task = asyncio.create_task(self._run_with_retry())

    async def stop(self) -> None:
        await self._close_current_session()
        self._reconnect_count = 0
        self._connected_once = False
        self._clear_resumption()
        self._emit(status=SessionStatus.STOPPED, last_event="Stopped by operator", reconnect_count=0,
                   resumed_reconnect_count=0, fresh_reconnect_count=0, error_kind="")
        operator_events.add("gemini", "Gemini session stopped")

    async def send_audio(self, chunk: bytes) -> None:
        try:
            self._audio_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            self._dropped_audio_chunks += 1

    async def _auto_commit_loop(self, epoch: int) -> None:
        while not self._stop_event.is_set() and epoch == self._session_epoch:
            await asyncio.sleep(0.5)
            if self._turn_start is not None and time.monotonic() - self._last_token_at >= 1.5:
                self._commit_current_turn()

    async def _run_with_retry(self) -> None:
        self._attempt = 0
        quota_failures = 0
        while not self._stop_event.is_set():
            epoch = self._session_epoch
            reconnect = self._connected_once
            try:
                status = SessionStatus.RECONNECTING if reconnect or self._attempt else SessionStatus.CONNECTING
                self._emit(status=status, last_event=f"Connecting to configured model: {self._model}", error_kind="")
                await self._run_session(self._model, reconnect, epoch)
                if self._stop_event.is_set():
                    return
                raise ConnectionError("Gemini stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop_event.is_set() or epoch != self._session_epoch:
                    return
                if _is_quota_error(exc):
                    quota_failures += 1
                    # This budget survives successful handshakes: a stream
                    # that immediately fails again must still stop.
                    if quota_failures > len(QUOTA_RETRY_DELAYS):
                        self._emit(status=SessionStatus.FAILED, error_kind="quota", last_event=(
                            f"{QUOTA_MESSAGE} 자동 재시도를 중단했습니다. / Automatic retries stopped."
                        ))
                        operator_events.add("error", self._state.last_event)
                        session_log.error("[%s] Quota/billing retry budget exhausted: %s", self.tag, exc)
                        return
                    delay = QUOTA_RETRY_DELAYS[quota_failures - 1]
                    self._emit(status=SessionStatus.RECONNECTING, error_kind="quota", last_event=(
                        f"{QUOTA_MESSAGE} {delay:g}초 후 재시도 ({quota_failures}/3). / "
                        f"Retrying in {delay:g}s ({quota_failures}/3)."
                    ))
                    operator_events.add("warning", self._state.last_event)
                    session_log.warning("[%s] Quota/billing failure %d/3; retry in %.1fs: %s", self.tag, quota_failures, delay, exc)
                    await self._wait_before_retry(delay)
                    continue
                unavailable = any(_matches_exception_text(exc, marker) for marker in ("404", "NOT_FOUND", "not found", "1008"))
                if isinstance(exc, (ValueError, TypeError)) or unavailable:
                    label = "Configured model unavailable" if unavailable else "Configuration error"
                    self._emit(status=SessionStatus.FAILED, last_event=f"{label}: {self._model}: {exc}")
                    operator_events.add("error", f"{label}: {self._model}")
                    session_log.error("[%s] %s: %s", self.tag, label, exc)
                    return
                if _is_goaway_exception(exc):
                    delay = 0.2
                    session_log.info("[%s] GoAway: reconnecting to %s", self.tag, self._model)
                else:
                    self._attempt += 1
                    if self._attempt >= MAX_RECONNECT_ATTEMPTS:
                        self._emit(status=SessionStatus.FAILED, last_event=f"Translation unavailable on {self._model}: {exc}")
                        operator_events.add("error", f"Gemini connection failed: {self._model}")
                        return
                    delay = min(RECONNECT_BASE_DELAY * 2 ** (self._attempt - 1), 60.0)
                    server_log.warning("[%s] Connection error on %s: %s; retry in %.1fs", self.tag, self._model, exc, delay)
                await self._wait_before_retry(delay)

    async def _wait_before_retry(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    def _build_config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            session_resumption=types.SessionResumptionConfig(handle=self._resumption_handle),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=self.target_language_code, echo_target_language=True,
            ),
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=gemini_cfg().get("voice", "orus")),
            )),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False, start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                    prefix_padding_ms=200, silence_duration_ms=700,
                ),
            ),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        )

    async def _run_session(self, model: str, is_reconnect: bool, epoch: int) -> None:
        # A rejected handle gets exactly one setup attempt without that handle.
        # Transport and quota errors retain the normal retry budgets instead.
        for _ in range(2):
            handle = self._resumption_handle
            age = time.monotonic() - self._resumption_updated_at if self._resumption_updated_at is not None else None
            began = time.monotonic()
            connected = False
            if is_reconnect and not handle:
                session_log.info("[%s] No resumable handle; reconnecting fresh once", self.tag)
            try:
                async with self._get_client().aio.live.connect(model=model, config=self._build_config()) as session:
                    connected = True
                    self._attempt = 0
                    self._connected_once = True
                    if is_reconnect:
                        self._reconnect_count += 1
                        mode = "resumed" if handle else "fresh"
                        counts = {f"{mode}_reconnect_count": getattr(self._state, f"{mode}_reconnect_count") + 1}
                        self._emit(**counts)
                        session_log.info("[%s] Reconnect completed: mode=%s handle_age_s=%s reconnect_s=%.3f",
                                         self.tag, mode, f"{age:.3f}" if age is not None else "none", time.monotonic() - began)
                    self._emit(status=SessionStatus.CONNECTED, last_event=f"Connected to {model}", reconnect_count=self._reconnect_count)
                    operator_events.add("gemini", f"Live translation active [{self.target_language_code}] ({model})")
                    async with asyncio.TaskGroup() as tasks:
                        tasks.create_task(self._send_loop(session, epoch))
                        receiver = tasks.create_task(self._recv_loop(session, model, epoch))
                        tasks.create_task(self._auto_commit_loop(epoch))
                        while not self._stop_event.is_set() and epoch == self._session_epoch:
                            if receiver.done():
                                await receiver
                                raise ConnectionError("Gemini receive stream ended")
                            await asyncio.sleep(0.1)
                return
            except Exception as exc:
                if handle and not connected and _is_resumption_rejected(exc):
                    self._clear_resumption()
                    session_log.warning("[%s] Resumption rejected; reconnecting fresh once (handle_age_s=%s)",
                                        self.tag, f"{age:.3f}" if age is not None else "none")
                    continue
                message = str(exc)
                for private_handle in (handle, self._resumption_handle):
                    if private_handle:
                        message = message.replace(private_handle, "[resumption handle omitted]")
                if message != str(exc):
                    # A provider error can echo the supplied handle. Keep it out
                    # of state, event messages, logs, and exported summaries.
                    if isinstance(exc, (ValueError, TypeError)):
                        raise type(exc)(message) from None
                    raise RuntimeError(message) from None
                raise

    async def _send_loop(self, session, epoch: int) -> None:
        while not self._stop_event.is_set() and epoch == self._session_epoch:
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if epoch != self._session_epoch:
                return
            if self._turn_start is None:
                self._first_audio_in_turn_sent_at = time.monotonic()
            await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))

    async def _recv_loop(self, session, model: str, epoch: int) -> None:
        while not self._stop_event.is_set() and epoch == self._session_epoch:
            received = False
            async for response in session.receive():
                received = True
                if self._stop_event.is_set() or epoch != self._session_epoch:
                    return
                update = getattr(response, "session_resumption_update", None)
                if update and update.resumable and update.new_handle:
                    self._resumption_handle = update.new_handle
                    self._resumption_updated_at = time.monotonic()
                if getattr(response, "go_away", None):
                    self._emit(last_event="GoAway: reconnecting")
                    raise RuntimeError("GoAway")
                content = getattr(response, "server_content", None)
                if not content:
                    continue
                incoming = getattr(content, "input_transcription", None)
                language = (getattr(incoming, "language_code", None) or getattr(incoming, "languageCode", None))
                if language:
                    self._observe_source_language(language.lower())
                source = getattr(incoming, "text", None) or ""
                outgoing = getattr(content, "output_transcription", None)
                if outgoing:
                    self._turn_out_lang = getattr(outgoing, "language_code", None) or getattr(outgoing, "languageCode", None)
                target = response.text or getattr(outgoing, "text", None) or ""
                if source or target:
                    now = time.monotonic()
                    if self._turn_start is None:
                        self._turn_start = now
                        if self._first_audio_in_turn_sent_at is not None:
                            self._emit(last_latency_ms=(now - self._first_audio_in_turn_sent_at) * 1000)
                    self._last_token_at = now
                if source:
                    self._current_source += source
                    self._pending_source += source
                    session_log.info("[%s] [Source (%s)] %s", self.tag, self._turn_in_lang or "unknown", source)
                if target:
                    self._current_target += target
                    self._pending_target += target
                    session_log.debug("[%s] [Target (%s)] %s", self.tag, self._turn_out_lang or self.target_language_code, target)
                self._publish_pending_text()
                # Forward translated audio regardless of the detected source language.
                if not self._state.audio_muted and self._on_audio:
                    for part in getattr(getattr(content, "model_turn", None), "parts", None) or []:
                        blob = getattr(part, "inline_data", None)
                        if blob and getattr(blob, "data", None):
                            self._on_audio(blob.data)
                if getattr(content, "turn_complete", False):
                    self._commit_current_turn()
            if not received:
                return
