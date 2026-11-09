"""Source language never blocks multilingual captions, audio, or archives."""
import asyncio

import pytest

from google.genai import types

from app.gemini_session import GeminiSession


class ReceivedTurns:
    def __init__(self, *turns: list[types.LiveServerMessage]):
        self.turns = list(turns)

    async def receive(self):
        if self.turns:
            for response in self.turns.pop(0):
                yield response


def response(source: str = "", language: str | None = None, target: str = "", audio: bytes = b"", complete: bool = False) -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        input_transcription=types.Transcription(text=source, language_code=language) if source or language else None,
        output_transcription=types.Transcription(text=target, language_code="en") if target else None,
        model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=audio))]) if audio else None,
        turn_complete=complete,
    ))


@pytest.mark.parametrize("language,source", [("zh", "你好"), ("zh-Hans", "欢迎"), ("fr", "Bonjour"), ("vi", "Xin chao")])
def test_non_korean_english_sources_remain_visible_and_audible(language, source):
    async def run():
        captions, sources, audio, visibility = [], [], [], []
        session = GeminiSession(
            on_caption=captions.append, on_source_transcript=sources.append,
            on_audio_chunk=audio.append, on_output_visibility=lambda hidden, muted: visibility.append((hidden, muted)),
        )
        messages = [response(source, language, "Hello", b"translated", complete=True) for _ in range(4)]
        await session._recv_loop(ReceivedTurns(messages), session.current_model, session.session_epoch)
        assert captions == ["Hello"] * 4
        assert sources == [source] * 4
        assert audio == [b"translated"] * 4
        assert len(session.transcript) == 4
        assert all(turn.source_lang == language.lower() for turn in session.transcript)
        assert all(turn.target_lang == "en" for turn in session.transcript)
        assert not session.state.output_hidden
        assert not session.state.audio_muted
        assert visibility == []
        assert session.session_epoch == 0
        assert session._task is None

    asyncio.run(run())


def test_missing_language_metadata_keeps_text_audio_and_unknown_archive_label():
    async def run():
        captions, sources, audio = [], [], []
        session = GeminiSession(on_caption=captions.append, on_source_transcript=sources.append, on_audio_chunk=audio.append)
        messages = [response(target="First ", audio=b"first"),
                    response("Original", target="second.", audio=b"second", complete=True)]
        await session._recv_loop(ReceivedTurns(messages), session.current_model, 0)
        assert captions == ["First ", "second."]
        assert sources == ["Original"]
        assert audio == [b"first", b"second"]
        assert session.transcript[0].source_lang == "unknown"
        assert session.transcript[0].target == "First second."
        assert not session.state.output_hidden
        assert not session.state.audio_muted

    asyncio.run(run())


def test_late_chinese_metadata_does_not_drop_or_duplicate_pending_text():
    async def run():
        captions, sources = [], []
        session = GeminiSession(on_caption=captions.append, on_source_transcript=sources.append)
        messages = [response(target="First "), response("你好", "zh-CN", "second.", complete=True)]
        await session._recv_loop(ReceivedTurns(messages), session.current_model, 0)
        assert captions == ["First ", "second."]
        assert sources == ["你好"]
        assert session.transcript[0].target == "First second."
        assert session.transcript[0].source_lang == "zh-cn"

    asyncio.run(run())


def test_language_switches_preserve_every_completed_turn_without_reconnecting():
    async def run():
        captions = []
        session = GeminiSession(on_caption=captions.append)
        messages = [response(source, language, target, complete=True) for source, language, target in [
            ("안녕하세요", "ko", "Hello"), ("你好", "zh", "Welcome"),
            ("Keep the unity", "en", "Keep the unity"), ("감사합니다", "ko", "Thank you"),
        ]]
        await session._recv_loop(ReceivedTurns(messages), session.current_model, 0)
        assert captions == ["Hello", "Welcome", "Keep the unity", "Thank you"]
        assert [turn.source_lang for turn in session.transcript] == ["ko", "zh", "en", "ko"]
        assert session.session_epoch == 0
        assert not session.state.output_hidden
        assert not session.state.audio_muted
        session.reset_transcript()
        assert session.transcript == []
        assert not session.state.output_hidden
        assert not session.state.audio_muted

    asyncio.run(run())
