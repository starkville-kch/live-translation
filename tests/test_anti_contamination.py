"""Manual pause/resume and stale-event isolation remain intact."""
import asyncio

from google.genai import types

from app.audio import AudioCapture
from app.gemini_session import GeminiSession


class ReceivedMessage:
    def __init__(self, message):
        self.message = message

    async def receive(self):
        yield self.message


def test_audio_frames_are_dropped_while_paused():
    async def run():
        capture = AudioCapture()
        await capture._enqueue(b"first")
        assert await capture._queue.get() == b"first"
        capture.pause()
        await capture._enqueue(b"paused")
        assert capture._queue.empty()
        capture.resume()
        await capture._enqueue(b"resumed")
        capture.drain()
        assert capture._queue.empty()

    asyncio.run(run())


def test_manual_pause_resume_preserves_model_and_increments_epoch():
    class StandbySession(GeminiSession):
        async def _run_with_retry(self, is_clean_resume=False):
            await self._stop_event.wait()

    async def run():
        session = StandbySession(on_caption=lambda text: None)
        model = session.current_model
        await session.start()
        await asyncio.sleep(0)
        first_epoch = session.session_epoch
        session._reconnect_count = 2
        await session.pause_clean()
        assert session.current_model == model
        assert session.state.reconnect_count == 2
        await session.resume_clean()
        await asyncio.sleep(0)
        assert session.current_model == model
        assert session.session_epoch == first_epoch + 1
        await session.stop()
        assert session.state.reconnect_count == 0

    asyncio.run(run())


def test_stale_epoch_discards_text_and_audio():
    async def run():
        captions, sources, audio = [], [], []
        session = GeminiSession(on_caption=captions.append, on_source_transcript=sources.append, on_audio_chunk=audio.append)
        session._session_epoch = 2
        message = types.LiveServerMessage(server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text="이전 원문", language_code="ko"),
            output_transcription=types.Transcription(text="Old translation", language_code="en"),
            model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=b"old"))]),
        ))
        await session._recv_loop(ReceivedMessage(message), session.current_model, epoch=1)
        assert captions == sources == audio == []
        assert session.transcript == []

    asyncio.run(run())