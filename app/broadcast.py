"""
In-process SSE broadcast to all connected attendee clients.
Maintains a "current line" that gets refined as Gemini streams tokens,
appending to scrollback only after a natural pause (~1.5s of no new tokens).
"""
import asyncio
import time
from dataclasses import dataclass


PAUSE_THRESHOLD_S = 1.5  # seconds without new tokens before committing current line


@dataclass
class CaptionEvent:
    kind: str  # "update" | "commit" | "unavailable" | "ping" | "paused" | "resumed"
    text: str = ""


class CaptionBroadcaster:
    def __init__(self):
        self._clients: list[asyncio.Queue] = []       # SSE caption subscribers
        self._audio_clients: list[asyncio.Queue] = [] # WebSocket audio subscribers
        self._current_line = ""
        self._last_token_at: float = 0.0
        self._commit_task: asyncio.Task | None = None
        self._unavailable = False
        self._caption_count = 0

    @property
    def caption_count(self) -> int:
        return self._caption_count

    def reset(self) -> None:
        self._current_line = ""
        self._caption_count = 0
        if self._commit_task and not self._commit_task.done():
            self._commit_task.cancel()

    def add_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._clients.append(q)
        return q

    def remove_client(self, q: asyncio.Queue) -> None:
        self._clients.discard(q) if hasattr(self._clients, "discard") else None
        try:
            self._clients.remove(q)
        except ValueError:
            pass

    def on_caption_delta(self, delta: str) -> None:
        self._unavailable = False
        self._current_line += delta
        self._caption_count += 1
        self._last_token_at = time.monotonic()
        self._push(CaptionEvent(kind="update", text=self._current_line))

        # Restart the commit timer on every new token
        if self._commit_task and not self._commit_task.done():
            self._commit_task.cancel()
        loop = asyncio.get_event_loop()
        self._commit_task = loop.create_task(self._schedule_commit())

    async def _schedule_commit(self) -> None:
        try:
            await asyncio.sleep(PAUSE_THRESHOLD_S)
            if self._current_line:
                self._push(CaptionEvent(kind="commit", text=self._current_line))
                self._current_line = ""
        except asyncio.CancelledError:
            pass

    def add_audio_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._audio_clients.append(q)
        return q

    def remove_audio_client(self, q: asyncio.Queue) -> None:
        try:
            self._audio_clients.remove(q)
        except ValueError:
            pass

    def on_audio_chunk(self, pcm: bytes) -> None:
        dead = []
        for q in self._audio_clients:
            try:
                q.put_nowait(pcm)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            try:
                self._audio_clients.remove(q)
            except ValueError:
                pass

    @property
    def audio_client_count(self) -> int:
        return len(self._audio_clients)

    def set_unavailable(self) -> None:
        if not self._unavailable:
            self._unavailable = True
            self._push(CaptionEvent(kind="unavailable"))

    def _push(self, event: CaptionEvent) -> None:
        dead = []
        for q in self._clients:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    @property
    def client_count(self) -> int:
        return len(self._clients)
