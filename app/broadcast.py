"""
app/broadcast.py — Real-Time Caption & Audio Broadcaster
=========================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Thread-safe, in-process fan-out layer that delivers caption events and raw
PCM audio chunks to every connected attendee simultaneously.

Architecture overview
---------------------
                    ┌─────────────────┐
  GeminiSession ──▶ │ CaptionBroadcaster │ ──▶ SSE queue (per attendee)
                    │                    │ ──▶ WS audio queue (per listener)
                    └────────────────────┘

Caption streaming (SSE)
-----------------------
Gemini streams English translation tokens incrementally.  The broadcaster
accumulates them into a ``_current_line`` string and immediately sends an
``"update"`` event to all SSE clients so captions appear word-by-word.

After ``PAUSE_THRESHOLD_S`` seconds of silence (no new tokens), a
``"commit"`` event finalises the line and ``_current_line`` is cleared.
This debounce approach prevents flickering on the attendee screen while
still feeling real-time.

If the line exceeds ``MAX_LINE_CHARS`` during continuous speech (no pause
long enough to trigger the silence timer), it is force-committed at the
last word boundary so the attendee screen never freezes on a paragraph.

Audio streaming (WebSocket)
---------------------------
Raw 24 kHz PCM16 mono bytes arrive from the Gemini translate model and are
put into per-client ``asyncio.Queue`` objects.  Clients that fall behind
(queue full) are silently evicted to avoid back-pressure on the main loop.

Special events
--------------
``"ping"``        — keepalive, sent every 15 s by the server lifespan loop
``"unavailable"`` — Gemini session failed; show warning banner to attendees
``"paused"``      — operator clicked Pause; attendees see a pause indicator
``"resumed"``     — operator clicked Resume
"""
import asyncio
import time
from dataclasses import dataclass

from app.events import operator_events


PAUSE_THRESHOLD_S = 1.5  # seconds without new tokens before committing current line
MAX_LINE_CHARS = 150      # force-commit when line exceeds this length (continuous speech)
_BOUNDARY_LOOKBACK = 60  # search the last N chars for a natural split point


@dataclass
class CaptionEvent:
    kind: str  # "update" | "commit" | "source" | "unavailable" | "ping" | "paused" | "resumed"
    text: str = ""


class CaptionBroadcaster:
    def __init__(self, glossary=None):  # glossary: GlossaryCorrector | None
        self._clients: list[asyncio.Queue] = []       # SSE caption subscribers
        self._audio_clients: list[asyncio.Queue] = [] # WebSocket audio subscribers
        self._current_line = ""
        self._current_ko = ""   # Korean source accumulated for this turn (for glossary)
        self._last_token_at: float = 0.0
        self._commit_task: asyncio.Task | None = None
        self._unavailable = False
        self._caption_count = 0
        self._glossary = glossary

    @property
    def caption_count(self) -> int:
        return self._caption_count

    def reset(self) -> None:
        self._current_line = ""
        self._current_ko = ""
        self._caption_count = 0
        if self._commit_task and not self._commit_task.done():
            self._commit_task.cancel()

    def add_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._clients.append(q)
        operator_events.add("user", f"Attendee joined ({len(self._clients)} connected)",
                            {"count": len(self._clients)})
        return q

    def remove_client(self, q: asyncio.Queue) -> None:
        self._clients.discard(q) if hasattr(self._clients, "discard") else None
        removed = False
        try:
            self._clients.remove(q)
            removed = True
        except ValueError:
            pass
        if removed:
            operator_events.add("user", f"Attendee left ({len(self._clients)} connected)",
                                {"count": len(self._clients)})

    def on_source_delta(self, delta: str) -> None:
        """Korean source text delta — pushed to all SSE clients (attendee page ignores it)."""
        self._current_ko += delta
        self._push(CaptionEvent(kind="source", text=delta))

    def on_caption_delta(self, delta: str) -> None:
        self._unavailable = False
        self._current_line += delta
        self._caption_count += 1
        self._last_token_at = time.monotonic()

        # Force-commit when the line grows too long during continuous speech.
        # Prefer splitting after a sentence-end or clause boundary in the last
        # _BOUNDARY_LOOKBACK chars; fall back to the last space if none found.
        if len(self._current_line) >= MAX_LINE_CHARS:
            cut = self._find_split(self._current_line)
            to_commit = self._current_line[:cut].rstrip()
            remainder = self._current_line[cut:].lstrip()
            if self._glossary and self._current_ko:
                to_commit = self._glossary.correct(self._current_ko, to_commit)
            if self._commit_task and not self._commit_task.done():
                self._commit_task.cancel()
            self._push(CaptionEvent(kind="commit", text=to_commit))
            self._current_line = remainder
            self._current_ko = ""  # reset KO buffer after commit
            if remainder:
                self._push(CaptionEvent(kind="update", text=remainder))
                loop = asyncio.get_event_loop()
                self._commit_task = loop.create_task(self._schedule_commit())
            return

        self._push(CaptionEvent(kind="update", text=self._current_line))

        # Restart the silence commit timer on every new token
        if self._commit_task and not self._commit_task.done():
            self._commit_task.cancel()
        loop = asyncio.get_event_loop()
        self._commit_task = loop.create_task(self._schedule_commit())

    def _find_split(self, line: str) -> int:
        """Return the index after which to split the line.

        Search the last _BOUNDARY_LOOKBACK characters for:
          1. Sentence end followed by a space:  '. '  '! '  '? '
          2. Clause boundary followed by a space:  ', '  '; '
          3. Last space (word boundary fallback)
        Returns the position *after* the punctuation/space so the commit
        text ends naturally and the remainder starts cleanly.
        """
        search_start = max(0, len(line) - _BOUNDARY_LOOKBACK)
        window = line[search_start:]

        # Try sentence-end boundaries first
        for punct in ('. ', '! ', '? ', '; ', ', '):
            pos = window.rfind(punct)
            if pos >= 0:
                return search_start + pos + len(punct)

        # Fall back to last space
        pos = line.rfind(' ')
        return pos if pos > 0 else len(line)

    async def _schedule_commit(self) -> None:
        try:
            await asyncio.sleep(PAUSE_THRESHOLD_S)
            if self._current_line:
                text = self._current_line
                if self._glossary and self._current_ko:
                    text = self._glossary.correct(self._current_ko, text)
                self._push(CaptionEvent(kind="commit", text=text))
                self._current_line = ""
                self._current_ko = ""
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
