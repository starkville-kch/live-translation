"""Fixed-model Live Translate configuration and committed text behavior."""
import asyncio

import pytest

from app.config import gemini_model
from app.gemini_session import GeminiSession, SessionStatus, TranscriptEntry, _is_goaway_exception, _is_quota_error, _matches_exception_text
from app.text_corrections import NameCorrector


def test_translation_configuration_is_empty_transcription_and_selected_output():
    for target in ("en", "uk", "zh"):
        session = GeminiSession(on_caption=lambda text: None, target_language_code=target)
        config = session._build_config()
        assert config.response_modalities == ["AUDIO"]
        assert config.input_audio_transcription.model_dump(exclude_none=True) == {}
        assert config.output_audio_transcription.model_dump(exclude_none=True) == {}
        assert config.translation_config.target_language_code == target
        assert config.translation_config.echo_target_language is True
        assert config.system_instruction is None
        assert config.session_resumption.handle is None
        assert config.context_window_compression.sliding_window is not None
        assert session.current_model == gemini_model()


def test_committed_source_and_target_receive_only_local_corrections(tmp_path):
    path = tmp_path / "names.yaml"
    path.write_text('스타필드 한인 교회: 스타크빌 한인교회\nStarfield: Starkville\n', encoding="utf-8")
    session = GeminiSession(on_caption=lambda text: None, corrector=NameCorrector(path))
    session._observe_source_language("ko")
    session._current_source = "스타필드 한인 교회 주옵소서 주옵소서 주옵소서 주옵소서 주옵소서"
    session._current_target = "Starfield amen amen amen amen amen"
    session.flush_current_turn()
    assert session.transcript[0].source == "스타크빌 한인교회 주옵소서"
    assert session.transcript[0].target == "Starkville amen"
    assert session._build_config().input_audio_transcription.model_dump(exclude_none=True) == {}


def test_transcript_entry_retains_neutral_and_legacy_access():
    entry = TranscriptEntry(1.0, "Original", "Translated", "en", "uk")
    assert entry.source == entry.korean == "Original"
    assert entry.target == entry.english == "Translated"
    assert entry.source_lang == "en"
    assert entry.target_lang == "uk"


def test_exception_group_and_chained_goaway_are_recognized():
    assert not _is_goaway_exception(None)
    assert not _is_goaway_exception(ValueError("Other"))
    assert _is_goaway_exception(ExceptionGroup("group", [ValueError("Other"), RuntimeError("GoAway")]))
    wrapper = RuntimeError("wrapper")
    wrapper.__cause__ = RuntimeError("GoAway")
    assert _is_goaway_exception(wrapper)
    assert _matches_exception_text(RuntimeError("1000: normal closure"), "1000")


@pytest.mark.parametrize("error", [ValueError("Invalid configuration"), RuntimeError("404 NOT_FOUND configured model")])
def test_configuration_or_missing_model_fails_without_fallback(error):
    class UnavailableSession(GeminiSession):
        def __init__(self):
            super().__init__(on_caption=lambda text: None)
            self.attempted_models = []

        async def _run_session(self, model, is_reconnect, epoch):
            self.attempted_models.append(model)
            raise error

    async def run():
        session = UnavailableSession()
        await session._run_with_retry()
        assert session.state.status == SessionStatus.FAILED
        assert session.attempted_models == [gemini_model()]
        assert session._attempt == 0
        assert session.current_model in session.state.last_event

    asyncio.run(run())


def test_goaway_reuses_configured_model_without_catalog_or_fallback():
    class ReconnectingSession(GeminiSession):
        def __init__(self):
            super().__init__(on_caption=lambda text: None)
            self.attempted_models = []

        async def _run_session(self, model, is_reconnect, epoch):
            self.attempted_models.append(model)
            if len(self.attempted_models) == 1:
                raise RuntimeError("GoAway")
            self._stop_event.set()

    async def run():
        session = ReconnectingSession()
        await session._run_with_retry()
        assert session.attempted_models == [session.current_model, session.current_model]
        assert session._attempt == 0

    asyncio.run(run())


@pytest.mark.parametrize("error", [
    RuntimeError("429 RESOURCE_EXHAUSTED"), RuntimeError("BILLING_DISABLED"),
    RuntimeError("Quota exceeded"), RuntimeError("RATE_LIMIT_EXCEEDED"),
    ExceptionGroup("receive tasks", [RuntimeError("429 RESOURCE_EXHAUSTED")]),
])
def test_quota_and_billing_errors_are_recognized_without_treating_503_as_quota(error):
    assert _is_quota_error(error)
    assert not _is_quota_error(RuntimeError("503 UNAVAILABLE"))


@pytest.mark.parametrize("after_handshake", [False, True])
def test_quota_retry_budget_is_finite_at_connect_and_after_handshakes(after_handshake):
    class QuotaSession(GeminiSession):
        def __init__(self):
            super().__init__(on_caption=lambda text: None)
            self.models = []
            self.delays = []
            self._connected_once = after_handshake

        async def _run_session(self, model, is_reconnect, epoch):
            self.models.append(model)
            if after_handshake:
                # A successful handshake resets the general retry counter in
                # production, but must not reset the separate quota budget.
                self._attempt = 0
                self._connected_once = True
                raise ExceptionGroup("receive tasks", [RuntimeError("429 RESOURCE_EXHAUSTED")])
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

        async def _wait_before_retry(self, delay):
            self.delays.append(delay)

    async def run():
        session = QuotaSession()
        await session._run_with_retry()
        assert session.models == [session.current_model] * 3
        assert session.delays == [2.0, 5.0]
        assert session.state.status == SessionStatus.FAILED
        assert session.state.error_kind == "quota"
        assert "지출 한도" in session.state.last_event
        assert "Spending limit" in session.state.last_event
        assert "Automatic retries stopped" in session.state.last_event

    asyncio.run(run())


def test_server_does_not_schedule_automatic_restart_after_quota_failure():
    from app import server
    from app.gemini_session import SessionState

    # There is intentionally no running event loop here: creating an automatic
    # restart task would raise instead of quietly scheduling more paid calls.
    assert server._auto_restart_task is None or server._auto_restart_task.done()
    previous = server.manager.get_broadcaster(server.manager.primary_target)
    was_unavailable = previous._unavailable if previous is not None else False
    try:
        server._handle_session_state_change(SessionState(status=SessionStatus.FAILED, error_kind="quota"))
        assert server._auto_restart_task is None or server._auto_restart_task.done()
    finally:
        if previous is not None:
            previous._unavailable = was_unavailable
