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
    source: str = ""
    target: str = ""
    source_lang: str = "ko"
    target_lang: str = "en"

    def __init__(
        self,
        kind: str,
        text: str = "",
        ko: str = "",
        source: str = "",
        target: str = "",
        source_lang: str = "ko",
        target_lang: str = "en",
    ):
        self.kind = kind
        self.target = target or text
        self.source = source or ko
        self.source_lang = source_lang
        self.target_lang = target_lang

    @property
    def text(self) -> str:
        return self.target

    @text.setter
    def text(self, val: str) -> None:
        self.target = val

    @property
    def ko(self) -> str:
        return self.source

    @ko.setter
    def ko(self, val: str) -> None:
        self.source = val


class CaptionBroadcaster:
    def __init__(
        self,
        glossary=None,  # glossary: GlossaryCorrector | None
        source_lang: str = "ko",
        target_lang: str = "en",
    ):
        self._clients: list[asyncio.Queue] = []       # SSE caption subscribers
        self._audio_clients: list[asyncio.Queue] = [] # WebSocket audio subscribers
        self._current_line = ""
        self._current_source = ""   # Source accumulated for this turn (for glossary / transcript)
        self.source_lang = source_lang
        self.target_lang = target_lang
        self._last_token_at: float = 0.0
        self._commit_task: asyncio.Task | None = None
        self._unavailable = False
        self._caption_count = 0
        self._glossary = glossary
        self._rtt_samples_local: list[tuple[float, float]] = []   # (timestamp, rtt_ms)
        self._rtt_samples_public: list[tuple[float, float]] = []  # (timestamp, rtt_ms)
        self._active_clients: dict[str, tuple[str, float]] = {}   # client_id -> (route, timestamp)

    @property
    def _current_ko(self) -> str:
        """Backward compatibility alias for tests and legacy callers."""
        return self._current_source

    @_current_ko.setter
    def _current_ko(self, val: str) -> None:
        self._current_source = val


    def record_rtt(self, hostname: str, rtt_ms: float, client_id: str = "") -> None:
        if not isinstance(rtt_ms, (int, float)) or rtt_ms <= 0 or rtt_ms > 10000:
            return

        now = time.monotonic()
        h = (str(hostname) if hostname else "").lower().strip()
        is_local = (
            not h
            or h.endswith(".local")
            or h in ("localhost", "127.0.0.1", "::1")
            or h.startswith("192.168.")
            or h.startswith("10.")
            or h.startswith("172.16.")
            or h.startswith("172.17.")
            or h.startswith("172.18.")
            or h.startswith("172.19.")
            or h.startswith("172.2")
            or h.startswith("172.30.")
            or h.startswith("172.31.")
        )
        route = "local" if is_local else "public"
        clean_cid = str(client_id)[:64] if client_id else ""

        if clean_cid:
            self._active_clients[clean_cid] = (route, now)

        # Prune active clients older than 30s
        self._active_clients = {cid: val for cid, val in self._active_clients.items() if now - val[1] <= 30.0}

        # Prune samples older than 60s
        self._rtt_samples_local = [s for s in self._rtt_samples_local if now - s[0] <= 60.0]
        self._rtt_samples_public = [s for s in self._rtt_samples_public if now - s[0] <= 60.0]

        if is_local:
            self._rtt_samples_local.append((now, float(rtt_ms)))
        else:
            self._rtt_samples_public.append((now, float(rtt_ms)))

    def get_telemetry_stats(self) -> dict:
        now = time.monotonic()
        self._active_clients = {cid: val for cid, val in self._active_clients.items() if now - val[1] <= 30.0}

        local_clients = sum(1 for cid, (route, ts) in self._active_clients.items() if route == "local")
        public_clients = sum(1 for cid, (route, ts) in self._active_clients.items() if route == "public")

        local_valid = [s[1] for s in self._rtt_samples_local if now - s[0] <= 60.0]
        public_valid = [s[1] for s in self._rtt_samples_public if now - s[0] <= 60.0]

        import statistics
        local_rtt = round(statistics.median(local_valid)) if local_valid else None
        public_rtt = round(statistics.median(public_valid)) if public_valid else None

        return {
            "local_rtt_ms": local_rtt,
            "local_samples": len(local_valid),
            "local_listeners": local_clients,
            "public_rtt_ms": public_rtt,
            "public_samples": len(public_valid),
            "public_listeners": public_clients,
            "total_listeners": local_clients + public_clients,
        }

    @property
    def caption_count(self) -> int:
        return self._caption_count

    @property
    def last_caption_ago_s(self) -> float | None:
        if self._last_token_at <= 0:
            return None
        return max(0.0, round(time.monotonic() - self._last_token_at, 1))

    def reset(self) -> None:
        self._current_line = ""
        self._current_source = ""
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
        """Source text delta — pushed to all SSE clients."""
        self._current_source += delta
        self._push(CaptionEvent(
            kind="source",
            source=delta,
            target="",
            source_lang=self.source_lang,
            target_lang=self.target_lang,
        ))

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
            if self._glossary and self._current_source and self.source_lang == "ko" and self.target_lang == "en":
                to_commit = self._glossary.correct(self._current_source, to_commit)
            if self._commit_task and not self._commit_task.done():
                self._commit_task.cancel()
            self._push(CaptionEvent(
                kind="commit",
                target=to_commit,
                source=self._current_source,
                source_lang=self.source_lang,
                target_lang=self.target_lang,
            ))
            self._current_line = remainder
            self._current_source = ""  # reset source buffer after commit
            if remainder:
                self._push(CaptionEvent(
                    kind="update",
                    target=remainder,
                    source="",
                    source_lang=self.source_lang,
                    target_lang=self.target_lang,
                ))
                loop = asyncio.get_event_loop()
                self._commit_task = loop.create_task(self._schedule_commit())
            return

        self._push(CaptionEvent(
            kind="update",
            target=self._current_line,
            source="",
            source_lang=self.source_lang,
            target_lang=self.target_lang,
        ))

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
                if self._glossary and self._current_source and self.source_lang == "ko" and self.target_lang == "en":
                    text = self._glossary.correct(self._current_source, text)
                self._push(CaptionEvent(
                    kind="commit",
                    target=text,
                    source=self._current_source,
                    source_lang=self.source_lang,
                    target_lang=self.target_lang,
                ))
                self._current_line = ""
                self._current_source = ""
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
        for q in self._audio_clients:
            try:
                q.put_nowait(pcm)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(pcm)
                except asyncio.QueueFull:
                    pass

    def drain_audio_clients(self) -> None:
        """Purge all buffered audio chunks across all connected WebSocket client queues."""
        for q in self._audio_clients:
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:
                    break

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
