# Technical Reference
### Live Translation System

> **Korean version**: [TECHNICAL.ko.md](TECHNICAL.ko.md)

This document explains how each core component of the system is implemented at the code level. It is a reference for developers adding features or modifying existing behaviour.

---

## 📌 Table of Contents
1. [End-to-End Data Flow](#1-end-to-end-data-flow)
2. [FastAPI Server Structure](#2-fastapi-server-structure)
3. [Gemini Live API Session](#3-gemini-live-api-session)
4. [Audio Capture Pipeline](#4-audio-capture-pipeline)
5. [SSE Caption Broadcast](#5-sse-caption-broadcast)
6. [Web Audio API Real-Time Playback](#6-web-audio-api-real-time-playback)
7. [Caption Commit Strategy](#7-caption-commit-strategy)
8. [Glossary Correction Pass](#8-glossary-correction-pass)
9. [Asyncio Patterns Summary](#9-asyncio-patterns-summary)
10. [Extension Guide](#10-extension-guide)

---

## 1. End-to-End Data Flow

```
[USB Mixer]
    │ PCM audio (16kHz mono PCM16)
    ▼
app/audio.py  →  AudioCapture.read_chunk()
    │ bytes (3200 bytes / 100ms chunk)
    ▼
app/gemini_session.py  →  GeminiSession._audio_queue
    │ asyncio.Queue[bytes]  (maxsize=500)
    ▼
GeminiSession._send_loop()  →  session.send_realtime_input(audio=Blob)
    │                                 [Gemini Live API WebSocket]
    ▼
GeminiSession._recv_loop()
    ├── output_transcription.text  →  on_caption(delta)
    ├── input_transcription.text   →  on_source_transcript(delta)
    └── model_turn.inline_data     →  on_audio_chunk(pcm_bytes)
    │
    ▼
app/broadcast.py  →  CaptionBroadcaster
    ├── on_caption_delta()   →  SSE "update" event  → /stream
    ├── _schedule_commit()   →  SSE "commit" event  → /stream  (after 1.5s silence)
    ├── on_source_delta()    →  SSE "source" event  → /stream  (operator only)
    └── on_audio_chunk()     →  WS binary           → /audio-stream
    │
    ▼
[Attendee mobile browser]
    ├── SSE EventSource  →  caption rendering
    └── WebSocket        →  Web Audio API playback
```

---

## 2. FastAPI Server Structure

### Lifespan
`app/server.py` uses the FastAPI `lifespan` context manager for startup/shutdown sequencing.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: model resolution, broadcaster + session singleton init
    yield
    # Shutdown: stop translation pipeline, write session transcript log
```

Two global singletons persist for the server's lifetime:
- `_broadcaster`: `CaptionBroadcaster` — owns all SSE/WS fan-out
- `_session`: `GeminiSession` — owns the Gemini API connection

### Route Map

| Route | Type | Returns | Role |
|-------|------|---------|------|
| `GET /` | HTTP | `HTMLResponse` | Operator console (embedded HTML) |
| `GET /live` | HTTP | `HTMLResponse` | Attendee caption page |
| `GET /stream` | SSE | `EventSourceResponse` | Caption event stream |
| `WS /audio-stream` | WebSocket | binary frames | 24kHz PCM16 audio |
| `GET /api/status` | HTTP | JSON | System status snapshot |
| `POST /api/start` | HTTP | JSON | Start translation pipeline |
| `POST /api/stop` | HTTP | JSON | Stop pipeline + write log |
| `POST /api/pause` | HTTP | JSON | Pause mic + billing |
| `POST /api/resume` | HTTP | JSON | Resume from pause |

### SSE Endpoint Pattern

Uses `sse_starlette`. A new generator instance is created per client connection.

```python
from sse_starlette.sse import EventSourceResponse

@app.get("/stream")
async def stream(request: Request):
    async def event_generator():
        queue = asyncio.Queue()
        _broadcaster.register(queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield {"event": event["type"], "data": event["data"]}
        except asyncio.TimeoutError:
            yield {"event": "ping", "data": ""}  # keepalive
        finally:
            _broadcaster.unregister(queue)
    return EventSourceResponse(event_generator())
```

Each connecting client gets its own `asyncio.Queue` registered with the broadcaster. When the client disconnects, the `finally` block deregisters and discards the queue.

### Binary WebSocket Audio

```python
@app.websocket("/audio-stream")
async def audio_stream(websocket: WebSocket):
    await websocket.accept()
    _broadcaster.register_audio(websocket)
    try:
        while True:
            await websocket.receive_bytes()  # connection keepalive only
    except WebSocketDisconnect:
        pass
    finally:
        _broadcaster.unregister_audio(websocket)
```

Data flows server→client only. `receive_bytes()` is called solely to detect disconnection.

### Embedded HTML Serving
All HTML is defined as Python strings inside `server.py`. No template engine is used. Advantage: single-file deployment. Trade-off: HTML maintainability decreases as it grows. Could be migrated to Jinja2 templates later.

---

## 3. Gemini Live API Session

### Connection Config (`_build_config`)

The dedicated translate model requires `translation_config`.

```python
types.LiveConnectConfig(
    response_modalities=["AUDIO"],          # TEXT not supported (error 1007)
    translation_config=types.TranslationConfig(
        target_language_code="en",
        echo_target_language=True,
    ),
    speech_config=types.SpeechConfig(       # voice pinning is mandatory
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="orus")
        )
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(
        language_hints=types.LanguageHints(language_codes=["ko", "en"])
    ),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    context_window_compression=types.ContextWindowCompressionConfig(
        sliding_window=types.SlidingWindow()
    ),
    session_resumption=types.SessionResumptionConfig(handle=self._resumption_handle),
)
```

### Response Parsing (`_recv_loop`)

A single response frame can carry multiple payload types simultaneously.

```python
async for response in session.receive():
    sc = response.server_content

    # 1. Synthesised audio PCM (24kHz, PCM16, mono)
    for part in (sc.model_turn.parts or []):
        if part.inline_data:
            on_audio_chunk(part.inline_data.data)  # ~12,000 bytes/chunk

    # 2. Korean source transcription
    if sc.input_transcription?.text:
        current_ko += sc.input_transcription.text

    # 3. English translation text
    if sc.output_transcription?.text:
        on_caption(sc.output_transcription.text)

    # 4. GoAway — triggers immediate reconnect
    if response.go_away:
        raise RuntimeError("GoAway")

    # 5. Session resumption handle update
    if response.session_resumption_update?.handle:
        self._resumption_handle = response.session_resumption_update.handle
```

### Auto-Reconnect (`_run_with_retry`)

```
attempt=0 → connect
    │
    ├─ clean run → reset attempt=0, loop
    │
    └─ exception raised
          attempt += 1
          attempt >= 3 → emit FAILED, exit
          delay = min(2 * 2^(attempt-1), 60)s → retry
```

GoAway path: `RuntimeError("GoAway")` raised → attempt increments → immediate retry with negligible delay since the GoAway delay is near-zero.

### Session Resumption vs. Fresh Connect
If `_resumption_handle` is set, the next connect call passes it to `SessionResumptionConfig`, preserving model context across the reconnect. The handle is cleared immediately after being sent and refreshed from the next response.

---

## 4. Audio Capture Pipeline

### PCM Format Chain

```
PyAudio device (native rate, e.g. 48000 Hz)
    │ raw bytes (int16, little-endian, mono)
    ▼
_resample_chunk()  →  16kHz mono PCM16
    │ linear interpolation resampler (no NumPy dependency)
    ▼
GeminiSession._audio_queue  (asyncio.Queue, maxsize=500)
    │
    ▼
session.send_realtime_input(audio=Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
```

### Resampler

```python
# Target: 16kHz, 100ms = 1600 samples = 3200 bytes
ratio = native_rate / 16000
indices = [int(i * ratio) for i in range(target_samples)]
resampled = [source_samples[i] for i in indices]
```

Nearest-neighbour linear index mapping. Prioritises low latency over audio fidelity — appropriate for speech recognition input.

### RMS Level Metering

```python
rms = sqrt(mean(sample**2 for sample in chunk))
db = 20 * log10(rms / 32768)  # relative to 0 dBFS
```

Computed every 100ms (10 Hz). Sustained readings below `-60 dBFS` trigger the `NO_SIGNAL` warning.

---

## 5. SSE Caption Broadcast

### Event Types

| Event | `data` format | Recipients | Description |
|-------|--------------|------------|-------------|
| `update` | EN text delta | Attendees + Operator | Replace current streaming line |
| `commit` | EN full line | Attendees + Operator | Finalise line, add to history |
| `source` | KO text delta | **Operator only** | Korean source streaming |
| `unavailable` | `""` | All | Session failed, show banner |
| `ping` | `""` | All | 15s keepalive (no UI effect) |
| `paused` | `""` | All | Show paused state |
| `resumed` | `""` | All | Show live state |

### Fan-out Architecture

```python
class CaptionBroadcaster:
    _queues: list[asyncio.Queue]    # one per SSE client
    _audio_clients: list[WebSocket] # audio WS clients

    def broadcast(self, event_type: str, data: str):
        for q in self._queues:
            q.put_nowait({"type": event_type, "data": data})
        # QueueFull on put_nowait → that client drops the event only
```

Each client has an independent queue, so a slow client cannot block a fast one.

---

## 6. Web Audio API Real-Time Playback

### Server → Browser Flow

```
GeminiSession  →  on_audio_chunk(pcm_bytes)
    ↓
CaptionBroadcaster.on_audio_chunk()
    ↓
websocket.send_bytes(pcm_bytes)  (to each /audio-stream client)
    ↓
[Browser JavaScript]
ws.onmessage = (e) => scheduleAudioChunk(e.data)
```

### Browser PCM16 → Float32 Conversion

```javascript
// ArrayBuffer (Int16, 24kHz, mono) → Float32Array
const int16 = new Int16Array(arrayBuffer);
const float32 = new Float32Array(int16.length);
for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768.0;  // normalise to [-1.0, 1.0]
}
```

### Gapless Audio Buffer Scheduling

```javascript
function scheduleAudioChunk(float32Data) {
    const buffer = audioCtx.createBuffer(1, float32Data.length, 24000);
    buffer.getChannelData(0).set(float32Data);
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(audioCtx.destination);
    // Schedule start immediately after the previous chunk ends
    nextStartTime = Math.max(audioCtx.currentTime, nextStartTime);
    source.start(nextStartTime);
    nextStartTime += buffer.duration;
}
```

iOS Safari requires a user gesture (tap) before `AudioContext` can be created — this is why the "Listen to Audio" button initialises `AudioContext` on click.

---

## 7. Caption Commit Strategy

How the system decides when a streaming line is "done" and ready to display permanently.

### Three Commit Paths

```
Streaming deltas arriving...
    │
    ├─ No new delta for 1.5s     →  [Path 1] Silence timer commit
    │                                broadcast("commit", current_line)
    │                                glossary.correct(ko, en) applied first
    │
    ├─ Current line exceeds 150  →  [Path 2] Force-commit overflow
    │  characters                    _find_split() searches last 60 chars
    │                                ". " → "! " → "? " → "; " → ", " → " "
    │
    └─ turn_complete signal      →  [Path 3] Auto-commit loop
                                    _auto_commit_loop() detects 1.5s silence
```

### Why `turn_complete` Is Not the Primary Trigger
Gemini fires `turn_complete` on every filler utterance ("um", "uh") in Korean sermon speech. This caused excessive caption fragmentation during testing. The 1.5s silence timer aligns much better with natural spoken sentence boundaries.

---

## 8. Glossary Correction Pass

`app/glossary.py` + `config/glossary.yaml`

### How It Works

1. Runs only when a caption line **commits** — never on streaming drafts.
2. Checks whether the Korean term (or any spelling variant) appears in the accumulated `input_transcription` for the turn.
3. If the correct English term is already in the output, skips it.
4. If missing, appends `[Correct Term]` to the end of the committed line.

```python
# Example: 당회 in Korean source, "Session" absent from English output
"The elders discussed the matter." → "The elders discussed the matter. [Session]"
```

### Boundary Matching

Korean nouns take particles directly (no space), so only the left boundary is checked.

```python
pattern = r"(?<![가-힣])" + re.escape(phrase)

# ✅ "당회" matches "당회에서", "당회를", "당회의"
# ❌ "당회" does NOT match "장로당회" (Korean character immediately before)
```

### Enabling / Disabling Categories

Set `enabled: true/false` per category in `config/glossary.yaml`:
- **A (PCA polity titles)**: `enabled: true` — 당회/Session, 장로/Elder, 목사/Pastor, 집사/Deacon
- **C (confessional documents)**: `enabled: true` — Westminster Confession
- **B, D, E, F**: `enabled: false` — activate after confirming real misses in logs

---

## 9. Asyncio Patterns Summary

### Task Structure Inside a Session

```
_run_session() contains:
    ┌──────────────────────────────────────────────┐
    │  asyncio.wait(FIRST_COMPLETED)               │
    │  ┌───────────┐ ┌───────────┐ ┌───────┐ ┌─────────┐ │
    │  │ send_loop │ │ recv_loop │ │commit │ │  stop   │ │
    │  │(audio out)│ │(responses)│ │(loop) │ │ (event) │ │
    │  └───────────┘ └───────────┘ └───────┘ └─────────┘ │
    └──────────────────────────────────────────────┘
    First task to complete/raise → all others cancelled
```

### Key Synchronisation Primitives

| Primitive | Location | Role |
|-----------|----------|------|
| `asyncio.Queue(maxsize=500)` | `_audio_queue` | Audio chunk backpressure |
| `asyncio.Queue()` (per client) | SSE broadcast | Client isolation |
| `asyncio.Event` | `_stop_event` | Stop signal propagation |
| `asyncio.wait_for(timeout=15)` | SSE generator | Keepalive ping trigger |

### Backpressure Policy

- `_audio_queue.put_nowait()` — drops the chunk if full (never blocks the audio thread)
- SSE `q.put_nowait()` — drops that event for the slow client only
- Audio WS `send_bytes()` — exception removes that client from the list

---

## 10. Extension Guide

### Adding a New REST Endpoint

```python
# Inside app/server.py
@app.post("/api/my-endpoint")
async def my_endpoint():
    # _session and _broadcaster singletons are directly accessible
    return {"ok": True}
```

### Adding a New SSE Event Type

1. Add a broadcast method in `app/broadcast.py`:
   ```python
   def broadcast_my_event(self, data: str):
       self.broadcast("my_event", data)
   ```
2. Listen in attendee JavaScript:
   ```javascript
   es.addEventListener("my_event", (e) => { /* handle */ });
   ```

### Adding a Glossary Entry

Append to the `direct:` list in `config/glossary.yaml`:
```yaml
- category: A
  ko: "새로운 한국어 용어"
  en: "Correct English Term"
  enabled: true
  variants: ["spelling variant 1", "spelling variant 2"]  # optional
```
Requires server restart — `GlossaryCorrector` loads once at startup.

### Changing the Audio Sample Rate

Edit only `audio.sample_rate` in `config.yaml`. The resampler applies automatically. Gemini always receives 16kHz regardless of the device rate.

### Adding a Second Language Target (Phase 10 prep)

1. Create a second `GeminiSession` with `target_language_code="zh"`.
2. Create a second `CaptionBroadcaster` for Chinese captions.
3. Add `/stream?lang=zh` route that uses the second broadcaster's queues.
4. Wire the same `AudioCapture` output to both sessions via `asyncio.Queue` duplication.
