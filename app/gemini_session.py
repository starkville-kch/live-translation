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
   of models to try on service start.
2. ``GeminiSession.start()`` spawns ``_run_with_retry()`` as an asyncio Task.
3. Once an initial candidate establishes a connection, it is LOCKED for the
   duration of the service session.
4. On first verified translation output (audio or text), the model is recorded
   as Last Verified Model.
5. In-session reconnects (e.g. GoAway) strictly reuse the locked model and resume session.
6. Clean resets (e.g. Pause -> Resume or language drift watchdog) discard resumption
   tokens and create a fresh context on the same locked model with incremented session epoch.
7. On ``stop()``, transcript is flushed, and the model is unlocked.
"""
import asyncio
import collections
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
    FAILED = "failed"  # sustained failure after max retries or non-retryable config error


@dataclass
class SessionState:
    status: SessionStatus = SessionStatus.STOPPED
    reconnect_count: int = 0
    last_event: str = ""
    last_latency_ms: float = 0.0
    last_update: float = field(default_factory=time.monotonic)


class TranscriptEntry(NamedTuple):
    timestamp: float   # time.monotonic() of turn start
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


def evaluate_drift_score(
    input_lang: str | None,
    input_text: str,
    output_lang: str | None,
    output_text: str,
    expected_source: str = "ko",
    target_language: str = "en",
) -> int:
    """Evaluate language drift score for a completed turn.

    Returns:
        0: within expected source/target envelope.
        1: weak drift (unexpected input language code or unexpected script).
        2: strong drift (output language does not match target language).
    """
    score = 0
    clean_src = (expected_source or "ko").strip().lower()
    clean_tgt = (target_language or "en").strip().lower()

    # 1. Primary input check via Gemini's language_code
    if input_lang:
        in_clean = input_lang.strip().lower()
        if not (in_clean.startswith(clean_src) or in_clean.startswith(clean_tgt)):
            score += 1
    elif input_text and clean_src == "ko":
        # Fallback script heuristic if language_code is missing for Korean source:
        # Flag Japanese Hiragana (0x3040-0x309F) / Katakana (0x30A0-0x30FF) or Thai (0x0E00-0x0E7F)
        for ch in input_text:
            code = ord(ch)
            if (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF) or (0x0E00 <= code <= 0x0E7F):
                score += 1
                break

    # 2. Target output check (must match target language)
    if output_lang:
        out_clean = output_lang.strip().lower()
        if not out_clean.startswith(clean_tgt):
            score += 2
    elif output_text and clean_tgt == "en":
        # If English output text contains substantial Hangul/Japanese instead of English
        for ch in output_text:
            code = ord(ch)
            if (0xAC00 <= code <= 0xD7A3) or (0x3040 <= code <= 0x30FF):
                score += 2
                break

    return score


class GeminiSession:
    def __init__(
        self,
        on_caption: Callable[[str], None],
        on_state_change: Callable[[SessionState], None] | None = None,
        on_source_transcript: Callable[[str], None] | None = None,
        on_audio_chunk: Callable[[bytes], None] | None = None,
        glossary=None,  # GlossaryCorrector | None
        target_language_code: str = "en",
        expected_source_language: str = "ko",
    ):
        self.target_language_code: str = (target_language_code or "en").lower().strip()
        self.expected_source_language: str = (expected_source_language or "ko").lower().strip()
        self.tag: str = f"Gemini:{self.target_language_code}"
        self._on_caption = on_caption
        self._on_state = on_state_change
        self._on_source = on_source_transcript
        self._on_audio = on_audio_chunk
        self._glossary = glossary
        self._state = SessionState()
        self._stop_event = asyncio.Event()
        self._attempt = 0
        self._session_epoch: int = 0
        self._resumption_handle: str | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self._task: asyncio.Task | None = None
        self._client: genai.Client | None = None
        self._transcript: list[TranscriptEntry] = []
        self._current_source: str = ""
        self._current_target: str = ""
        self._turn_in_lang: str | None = None
        self._turn_out_lang: str | None = None
        self._turn_start: float | None = None
        self._first_audio_in_turn_sent_at: float | None = None
        self._last_token_at: float = 0.0
        self._has_verified_output: bool = False
        self._dropped_audio_chunks: int = 0
        self._auto_drift_correction: bool = bool(gemini_cfg().get("auto_drift_correction", False))

        self._drift_history: collections.deque = collections.deque(maxlen=3)
        self._consecutive_clean_turns: int = 0
        self._last_watchdog_reset_at: float = 0.0
        self._turn_id: int = 0
        self._last_evaluated_turn_id: int = -1


    @property
    def dropped_audio_chunks(self) -> int:
        return self._dropped_audio_chunks

    @property
    def _current_ko(self) -> str:

        """Backward compatibility alias for tests."""
        return self._current_source

    @_current_ko.setter
    def _current_ko(self, val: str) -> None:
        self._current_source = val

    @property
    def _current_en(self) -> str:
        """Backward compatibility alias for tests."""
        return self._current_target

    @_current_en.setter
    def _current_en(self, val: str) -> None:
        self._current_target = val

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=gemini_api_key())
        return self._client

    @property
    def current_model(self) -> str:
        return model_resolver.active_model

    @property
    def session_epoch(self) -> int:
        return self._session_epoch

    @property
    def auto_drift_correction(self) -> bool:
        return self._auto_drift_correction

    @auto_drift_correction.setter
    def auto_drift_correction(self, val: bool) -> None:
        self._auto_drift_correction = bool(val)

    def set_auto_drift_correction(self, enabled: bool) -> None:
        self._auto_drift_correction = bool(enabled)

    def clear_drift_state(self) -> None:
        self._drift_history.clear()
        self._consecutive_clean_turns = 0

    @property
    def transcript(self) -> list[TranscriptEntry]:
        return list(self._transcript)

    def reset_transcript(self) -> None:
        self._transcript.clear()
        self._current_source = ""
        self._current_target = ""
        self._turn_in_lang = None
        self._turn_out_lang = None
        self._turn_start = None
        self._first_audio_in_turn_sent_at = None
        self._has_verified_output = False
        self.clear_drift_state()

    def flush_current_turn(self) -> None:
        """Commit any in-progress turn to the transcript (called on stop/pause)."""
        self._commit_current_turn()

    def _commit_current_turn(self) -> None:
        if not (self._current_source.strip() or self._current_target.strip()):
            return

        self._turn_id += 1
        current_turn_id = self._turn_id
        src = self._current_source.strip()
        tgt = self._current_target.strip()
        in_lang = self._turn_in_lang
        out_lang = self._turn_out_lang

        if self._glossary and src and self.expected_source_language == "ko" and self.target_language_code == "en":
            tgt = self._glossary.correct(src, tgt)

        self._transcript.append(TranscriptEntry(
            timestamp=self._turn_start or time.monotonic(),
            source=src,
            target=tgt,
            source_lang=self.expected_source_language,
            target_lang=self.target_language_code,
        ))

        session_log.info(
            "[%s] [Turn committed] %s (%s): %s | %s (%s): %s",
            self.tag,
            self.expected_source_language.upper(),
            in_lang or "auto",
            src,
            self.target_language_code.upper(),
            out_lang or self.target_language_code,
            tgt,
        )

        # Clear turn accumulators immediately to prevent re-committing same turn
        self._current_source = ""
        self._current_target = ""
        self._turn_in_lang = None
        self._turn_out_lang = None
        self._turn_start = None
        self._first_audio_in_turn_sent_at = None

        # Evaluate drift score exactly once per turn
        if current_turn_id > self._last_evaluated_turn_id:
            self._last_evaluated_turn_id = current_turn_id
            self._evaluate_turn_drift(current_turn_id, in_lang, src, out_lang, tgt)

    def _evaluate_turn_drift(
        self,
        turn_id: int,
        in_lang: str | None,
        src: str,
        out_lang: str | None,
        tgt: str,
    ) -> None:
        turn_score = evaluate_drift_score(
            in_lang,
            src,
            out_lang,
            tgt,
            expected_source=self.expected_source_language,
            target_language=self.target_language_code,
        )
        if turn_score == 0:
            had_drift = len(self._drift_history) > 0
            self._drift_history.clear()
            self._consecutive_clean_turns += 1
            if had_drift:
                session_log.info(
                    "[%s] [Drift] confirmation=0/3 (reset by clean %s turn)",
                    self.tag,
                    self.expected_source_language,
                )
        else:
            self._drift_history.append(turn_score)
            self._consecutive_clean_turns = 0
            session_log.info(
                "[%s] [Drift] turn=%d source=%s expected=%s score=+%d",
                self.tag,
                turn_id,
                in_lang or "unknown",
                self.expected_source_language,
                turn_score,
            )

        total_drift = sum(self._drift_history)
        if total_drift > 0:
            session_log.info(
                "[%s] [Drift] confirmation=%d/3",
                self.tag,
                total_drift,
            )

        # Drift auto-recovery:
        # 1. Total drift score >= 3 (sustained across turns)
        # 2. Only active when expected_source_language == "ko"
        # 3. Only active when auto_drift_correction is True
        # 4. Debounced by 15.0 seconds
        if total_drift >= 3:
            now = time.monotonic()
            if self.expected_source_language == "ko" and self._auto_drift_correction:
                if (now - self._last_watchdog_reset_at) >= 15.0:
                    self._last_watchdog_reset_at = now
                    session_log.info("[%s] [Drift] recovered via clean session reset", self.tag)
                    server_log.warning(
                        "[%s] Auto drift recovery: score=%d >= 3. Resetting session cleanly.",
                        self.tag,
                        total_drift,
                    )
                    operator_events.add(
                        "warning",
                        f"Auto drift recovery triggered [{self.target_language_code}] (score {total_drift})"
                    )
                    asyncio.create_task(self.reset_clean(reason="Language drift watchdog"))
            else:
                session_log.info(
                    "[%s] [Drift] Score=%d >= 3 (auto recovery disabled: expected_source=%s, auto_drift_correction=%s)",
                    self.tag,
                    total_drift,
                    self.expected_source_language,
                    self._auto_drift_correction,
                )
                self._emit(last_event="⚠ 비정상 언어 감지 (수동 복구: Pause -> Resume)")
                operator_events.add(
                    "warning",
                    f"Language drift detected [{self.target_language_code}] (score {total_drift}) — manual Pause -> Resume available"
                )


    async def _auto_commit_loop(self, epoch: int) -> None:
        try:
            while not self._stop_event.is_set() and epoch == self._session_epoch:
                await asyncio.sleep(0.5)
                if self._turn_start is not None and (self._current_source or self._current_target):
                    silence_duration = time.monotonic() - self._last_token_at
                    if silence_duration >= 1.5:  # matches PAUSE_THRESHOLD_S
                        self._commit_current_turn()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            session_log.error("[%s] Error in auto-commit loop: %s", self.tag, e)


    def _emit(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self._state, k, v)
        self._state.last_update = time.monotonic()
        if self._on_state:
            self._on_state(SessionState(**vars(self._state)))

    @property
    def state(self) -> SessionState:
        return SessionState(**vars(self._state))

    def _drain_audio_queue(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except Exception:
                break

    async def start(self) -> None:
        self._stop_event.clear()
        self._has_verified_output = False
        self._session_epoch += 1
        self._resumption_handle = None
        self.clear_drift_state()
        self._drain_audio_queue()
        self._task = asyncio.create_task(self._run_with_retry())

    async def pause_clean(self) -> None:
        """Pause service: closes Gemini session, discards resumption handle, drains queues.
        Preserves model lock in model_resolver."""
        self._stop_event.set()
        self.flush_current_turn()
        self._resumption_handle = None
        self._has_verified_output = False
        self.clear_drift_state()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._drain_audio_queue()
        self._emit(status=SessionStatus.STOPPED, last_event="Paused (clean standby)")
        operator_events.add("gemini", "Gemini translation paused (clean standby)")

    async def resume_clean(self) -> None:
        """Resume service: creates a fresh Gemini Live session with clean context and incremented epoch.
        Reuses the existing locked model without re-resolving."""
        self._session_epoch += 1
        self._stop_event.clear()
        self._resumption_handle = None
        self._has_verified_output = False
        self.clear_drift_state()
        self._drain_audio_queue()
        server_log.info("Resuming Gemini session cleanly (epoch %d) on locked model: %s", self._session_epoch, model_resolver.locked_model)
        self._task = asyncio.create_task(self._run_with_retry(is_clean_resume=True))

    async def reset_clean(self, reason: str = "Clean reset") -> None:
        """Reset active session cleanly (destroys old context, increments epoch, reconnects locked model)."""
        server_log.info("Performing clean Gemini reset (epoch %d -> %d): %s", self._session_epoch, self._session_epoch + 1, reason)
        self._session_epoch += 1
        self._stop_event.set()
        self.flush_current_turn()
        self._resumption_handle = None
        self._has_verified_output = False
        self.clear_drift_state()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._drain_audio_queue()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_with_retry(is_clean_resume=True))

    async def stop(self) -> None:
        self._stop_event.set()
        self.flush_current_turn()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._resumption_handle = None
        self.clear_drift_state()
        self._drain_audio_queue()
        model_resolver.unlock_session()
        self._emit(status=SessionStatus.STOPPED,
                   last_event="Stopped by operator")
        operator_events.add("gemini", "Gemini session stopped")

    async def send_audio(self, chunk: bytes) -> None:
        try:
            self._audio_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass  # drop under backpressure rather than stall

    async def _run_with_retry(self, is_clean_resume: bool = False) -> None:
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
            current_epoch = self._session_epoch
            try:
                is_resume = self._resumption_handle is not None and not is_clean_resume
                if self._attempt > 0 or is_resume:
                    status = SessionStatus.RECONNECTING
                    event_msg = f"Reconnecting to {locked_model} (attempt {self._attempt})" if self._attempt > 0 else f"Reconnecting to {locked_model} (resuming session)"
                    if self._attempt > 0:
                        operator_events.add("network", f"Reconnecting to Gemini (attempt {self._attempt})", {"attempt": self._attempt, "model": locked_model})
                    else:
                        operator_events.add("gemini", f"Resuming Gemini session ({locked_model})")
                else:
                    status = SessionStatus.CONNECTING
                    event_msg = f"Attempting connection to {locked_model}"
                    operator_events.add("gemini", f"Attempting connection to {locked_model}")

                self._emit(status=status, last_event=event_msg, reconnect_count=self._attempt)
                server_log.info(event_msg)
                session_log.debug(event_msg)

                await self._run_session(model=locked_model, is_reconnect=(self._attempt > 0 or is_resume), epoch=current_epoch)
                self._attempt = 0  # reset on clean run completion
                if self._stop_event.is_set():
                    return

            except (ValueError, TypeError) as e:
                # Fatal non-retryable configuration error (e.g. invalid LiveConnectConfig / SDK schema incompatibility)
                err_msg = f"Configuration error: {e}"
                server_log.error("Non-retryable Gemini configuration error: %s", e)
                session_log.error("Non-retryable Gemini configuration error: %s", e)
                self._emit(
                    status=SessionStatus.FAILED,
                    last_event=err_msg,
                    reconnect_count=self._attempt
                )
                operator_events.add("error", "Gemini configuration error — session stopped", {"error": str(e), "model": locked_model})
                return

            except asyncio.CancelledError:
                server_log.info("Retry loop cancelled (epoch %d) — exiting cleanly", current_epoch)
                session_log.debug("Retry loop cancelled (epoch %d) — exiting cleanly", current_epoch)
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
            # Gemini Developer API Live Translate configuration
            return types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                translation_config=types.TranslationConfig(
                    target_language_code=self.target_language_code,
                    echo_target_language=True,
                ),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice,
                        )
                    )
                ),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                realtime_input_config=types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(
                        disabled=False,
                        start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                        prefix_padding_ms=200,
                        silence_duration_ms=700,
                    ),
                ),
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
                realtime_input_config=types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(
                        disabled=False,
                        start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                        prefix_padding_ms=200,
                        silence_duration_ms=700,
                    ),
                ),
                context_window_compression=types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow(),
                ),
                session_resumption=types.SessionResumptionConfig(
                    handle=self._resumption_handle,
                ),
            )

    async def _run_session(self, model: str, is_reconnect: bool, epoch: int) -> None:
        config = self._build_config(model)
        is_resuming = self._resumption_handle is not None

        server_log.info(
            "[%s] Session connect starting: model=%s resumption_handle_present=%s epoch=%d",
            self.tag,
            model,
            is_resuming,
            epoch,
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
                server_log.info("[%s] Gemini Live session connected successfully on model: %s (epoch %d)", self.tag, model, epoch)
                operator_events.add("gemini", f"Live translation active [{self.target_language_code}] ({model})")

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._send_loop(session, epoch))
                    tg.create_task(self._recv_loop(session, model, epoch))
                    tg.create_task(self._auto_commit_loop(epoch))
                    while not self._stop_event.is_set() and epoch == self._session_epoch:
                        await asyncio.sleep(0.1)
        except (ValueError, TypeError) as e:
            # Propagate configuration errors directly without swallowing as 1000/disconnect
            raise e
        except asyncio.CancelledError:
            if self._stop_event.is_set() or epoch != self._session_epoch:
                return
            raise
        except Exception as e:
            if "1000" in str(e) and (self._stop_event.is_set() or epoch != self._session_epoch):
                return
            log_fn = server_log.info if "GoAway" in str(e) else server_log.error
            log_fn(
                "[%s] SESSION_FAILURE: model=%s type=%s message=%s (epoch=%d)",
                self.tag,
                model,
                type(e).__name__,
                str(e),
                epoch,
            )
            raise e

    async def _send_loop(self, session, epoch: int) -> None:
        try:
            while not self._stop_event.is_set() and epoch == self._session_epoch:
                try:
                    chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                    if epoch != self._session_epoch:
                        break
                    if self._turn_start is None and not self._current_source:
                        self._first_audio_in_turn_sent_at = time.monotonic()
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=chunk, mime_type="audio/pcm;rate=16000")
                    )
                except asyncio.TimeoutError:
                    continue
        except Exception:
            if not self._stop_event.is_set() and epoch == self._session_epoch:
                raise

    async def _recv_loop(self, session, model: str, epoch: int) -> None:
        try:
            async for response in session.receive():
                if self._stop_event.is_set() or epoch != self._session_epoch:
                    break

                if hasattr(response, "session_resumption_update") and response.session_resumption_update:
                    update = response.session_resumption_update
                    if hasattr(update, "handle") and update.handle:
                        self._resumption_handle = update.handle
                        session_log.debug("[%s] Resumption handle updated (epoch %d)", self.tag, epoch)

                if hasattr(response, "go_away") and response.go_away:
                    server_log.info("[%s] GoAway received — initiating graceful reconnect", self.tag)
                    self._emit(last_event="GoAway: reconnecting")
                    operator_events.add("network", f"GoAway [{self.target_language_code}] — reconnecting")
                    raise RuntimeError("GoAway")

                sc = getattr(response, "server_content", None)

                # Audio PCM chunks (24kHz PCM16 mono)
                if sc and epoch == self._session_epoch:
                    for part in getattr(getattr(sc, "model_turn", None), "parts", None) or []:
                        blob = getattr(part, "inline_data", None)
                        if blob and self._on_audio and epoch == self._session_epoch:
                            self._on_audio(blob.data)
                            if not self._has_verified_output:
                                self._has_verified_output = True
                                model_resolver.record_verified_success(model)

                # Incremental Source transcriptions
                if sc and epoch == self._session_epoch:
                    it = getattr(sc, "input_transcription", None)
                    if it and getattr(it, "text", None):
                        in_text = it.text
                        in_lang = getattr(it, "language_code", None) or getattr(it, "languageCode", None)
                        if in_lang:
                            self._turn_in_lang = in_lang
                        self._current_source += in_text
                        self._last_token_at = time.monotonic()
                        session_log.info(
                            "[%s] [%s (%s)] %s",
                            self.tag,
                            self.expected_source_language.upper(),
                            in_lang or self._turn_in_lang or "auto",
                            in_text,
                        )
                        if self._on_source and epoch == self._session_epoch:
                            self._on_source(in_text)

                # Incremental target translated text
                target_text = response.text or ""
                if sc and epoch == self._session_epoch:
                    ot = getattr(sc, "output_transcription", None)
                    if ot:
                        out_lang = getattr(ot, "language_code", None) or getattr(ot, "languageCode", None)
                        if out_lang:
                            self._turn_out_lang = out_lang
                        if getattr(ot, "text", None) and not target_text:
                            target_text = ot.text

                if target_text and epoch == self._session_epoch:
                    if not self._has_verified_output:
                        self._has_verified_output = True
                        model_resolver.record_verified_success(model)

                    if self._turn_start is None:
                        self._turn_start = time.monotonic()
                        if self._first_audio_in_turn_sent_at is not None:
                            latency_ms = (self._turn_start - self._first_audio_in_turn_sent_at) * 1000
                            self._emit(last_latency_ms=latency_ms)
                    self._current_target += target_text
                    self._last_token_at = time.monotonic()
                    if self._on_caption and epoch == self._session_epoch:
                        self._on_caption(target_text)
                    session_log.debug(
                        "[%s] [%s delta (%s)] %s",
                        self.tag,
                        self.target_language_code.upper(),
                        self._turn_out_lang or self.target_language_code,
                        target_text,
                    )

                # Completed turn boundary from server
                if sc and getattr(sc, "turn_complete", False) and epoch == self._session_epoch:
                    self._commit_current_turn()

        except Exception as e:
            if "1000" in str(e) and (self._stop_event.is_set() or epoch != self._session_epoch):
                return
            if not self._stop_event.is_set() and epoch == self._session_epoch:
                raise

