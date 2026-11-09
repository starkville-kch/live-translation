"""Exercise the real connection/retry lifecycle with an in-memory provider."""
import asyncio

import pytest
from google.genai import types

from app.gemini_session import GeminiSession, SessionStatus


def handle_update(handle, resumable=True):
    return types.LiveServerMessage(session_resumption_update=types.LiveServerSessionResumptionUpdate(
        new_handle=handle, resumable=resumable,
    ))


def goaway():
    return types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="50s"))


class Socket:
    def __init__(self, messages):
        self.messages = messages

    async def receive(self):
        for message in self.messages:
            if isinstance(message, Exception):
                raise message
            yield message
        await asyncio.Event().wait()

    async def send_realtime_input(self, **kwargs):
        pass


class Connection:
    def __init__(self, script):
        self.script = script

    async def __aenter__(self):
        if isinstance(self.script, Exception):
            raise self.script
        return Socket(self.script)

    async def __aexit__(self, *args):
        pass


class Provider:
    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.configs = []
        self.aio = self.live = self

    def connect(self, *, model, config):
        self.configs.append(config)
        assert self.scripts, "Unexpected extra connection"
        return Connection(self.scripts.pop(0))


class LocalSession(GeminiSession):
    def __init__(self, provider, target="en"):
        super().__init__(on_caption=lambda text: None, target_language_code=target)
        self.provider = provider
        self.delays = []

    def _get_client(self):
        return self.provider

    async def _wait_before_retry(self, delay):
        self.delays.append(delay)
        await asyncio.sleep(0)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def test_goaway_preserves_latest_valid_handle_per_output_language(caplog):
    caplog.set_level("INFO", logger="session")
    async def run():
        providers = [Provider([handle_update(token), handle_update("", False), goaway()], [])
                     for token in ("private-en-handle", "private-zh-handle")]
        sessions = [LocalSession(provider, target) for provider, target in zip(providers, ("en", "zh"))]
        try:
            await asyncio.gather(*(session.start() for session in sessions))
            await until(lambda: all(session.state.resumed_reconnect_count == 1 for session in sessions))
            for provider, session, token in zip(providers, sessions, ("private-en-handle", "private-zh-handle")):
                assert [config.session_resumption.handle for config in provider.configs] == [None, token]
                assert all(config.translation_config.target_language_code == session.target_language_code for config in provider.configs)
                assert session.state.reconnect_count == 1
                assert session.state.fresh_reconnect_count == 0
                assert token not in caplog.text
            assert "handle_age_s=" in caplog.text and "reconnect_s=" in caplog.text
        finally:
            await asyncio.gather(*(session.stop() for session in sessions))

    asyncio.run(run())


def test_pause_resume_keeps_context_but_stop_start_clears_it():
    async def run():
        provider = Provider([handle_update("saved-context")], [], [])
        session = LocalSession(provider)
        try:
            await session.start()
            await until(lambda: session._resumption_handle == "saved-context")
            epoch = session.session_epoch
            await session.pause_clean()
            assert session._resumption_handle == "saved-context"
            assert session._audio_queue.empty()
            await session.resume_clean()
            await until(lambda: session.state.resumed_reconnect_count == 1)
            assert session.session_epoch == epoch + 1
            assert provider.configs[1].session_resumption.handle == "saved-context"
            await session.stop()
            assert session._resumption_handle is None
            await session.start()
            await until(lambda: len(provider.configs) == 3)
            assert provider.configs[2].session_resumption.handle is None
            assert session.state.reconnect_count == 0
        finally:
            await session.stop()

    asyncio.run(run())


@pytest.mark.parametrize("fresh_fails", [False, True])
def test_rejected_handle_gets_only_one_fresh_setup_attempt(fresh_fails, caplog):
    async def run():
        token = "secret-rejected-handle"
        provider = Provider([handle_update(token), goaway()], ValueError(f"400 invalid resumption handle: {token}"),
                            ValueError("400 invalid configuration") if fresh_fails else [])
        session = LocalSession(provider)
        try:
            await session.start()
            await until(lambda: session.state.status == SessionStatus.FAILED if fresh_fails else session.state.fresh_reconnect_count == 1)
            assert [config.session_resumption.handle for config in provider.configs] == [None, token, None]
            assert session.state.resumed_reconnect_count == 0
            assert session._resumption_handle is None
            assert token not in caplog.text and token not in session.state.last_event
            assert "Resumption rejected; reconnecting fresh once" in caplog.text
        finally:
            await session.stop()

    asyncio.run(run())


def test_missing_handle_reconnects_fresh():
    async def run():
        provider = Provider([goaway()], [])
        session = LocalSession(provider)
        try:
            await session.start()
            await until(lambda: session.state.fresh_reconnect_count == 1)
            assert [config.session_resumption.handle for config in provider.configs] == [None, None]
            assert session.state.reconnect_count == 1
        finally:
            await session.stop()

    asyncio.run(run())


@pytest.mark.parametrize("during_receive", [False, True])
def test_quota_with_saved_handle_keeps_three_failure_budget(during_receive, caplog):
    async def run():
        token = "private-quota-handle"
        error = RuntimeError(f"429 quota exhausted for session handle {token}")
        script = [error] if during_receive else error
        provider = Provider([handle_update(token), goaway()], script, script, script)
        session = LocalSession(provider)
        try:
            await session.start()
            await until(lambda: session.state.status == SessionStatus.FAILED)
            assert [config.session_resumption.handle for config in provider.configs] == [None, token, token, token]
            assert session.delays == [0.2, 2.0, 5.0]
            assert session.state.error_kind == "quota"
            assert token not in caplog.text and token not in session.state.last_event
        finally:
            await session.stop()

    asyncio.run(run())
