# Agent Context — SKC Live Translation

## Doc Update Policy
After any major piece of work, update this file and the relevant docs.

| What changed | Files to update |
|---|---|
| New feature or phase completed | `agent.md`, `CHANGELOG.md`, `docs/PLAN.en.md`, `docs/PLAN.ko.md` |
| New file added | `agent.md` (Document Index), `docs/PLAN.en.md` + `PLAN.ko.md` (File Map) |
| Architecture or design decision | `agent.md` (Key Design Decisions), `docs/PLAN.en.md`, `docs/TECHNICAL.en.md` if code-level |
| Build/exe change | `docs/BUILD_EXE.en.md`, `docs/BUILD_EXE.ko.md`, `SKC_translation.spec` |
| Operator workflow change | `how_to_use.html` |

---

## Project Summary
Real-time Korean→English captioning appliance for church services. Audio from USB mixer → Gemini Live API (`gemini-3.5-live-translate-preview`) → English captions over SSE + translated audio over binary WebSocket → attendee phones. Single session per 60–90 min service. Port: **8080** (set in `config.yaml`).

---

## Architecture
```
[USB Mixer] → app/audio.py (PCM16 16kHz) → app/gemini_session.py (Gemini Live)
                                                       ↓
                                              app/broadcast.py
                                              ├── SSE caption fanout (_clients)
                                              └── PCM audio fanout (_audio_clients)
                                                       ↓
                                              app/events.py (OperatorEventLog)
                                              └── thread-safe ring buffer, polled by frontend
                                                       ↓
                                              app/server.py (FastAPI)
                                              ├── GET  /                     ← operator page
                                              ├── GET  /live                 ← attendee phone page
                                              ├── GET  /stream               ← SSE caption stream
                                              ├── WS   /audio-stream         ← binary PCM16 audio
                                              ├── GET  /api/qr.png           ← QR → /live at current port
                                              ├── POST /api/start|stop|pause|resume
                                              └── GET  /api/events?since=N   ← operator event polling
```

---

## Key Design Decisions
These are the non-obvious decisions that can't be derived by reading the code. Don't re-run these experiments.

- **`gemini-3.1-flash-live-preview` is banned**: crashes after ~30s of continuous audio (error 1011 keepalive timeout). Do not use as fallback. Confirmed Phase 12 Round 3.
- **`translation_config` is mandatory, `system_instruction` does nothing**: the translate model requires `translation_config` to activate translation mode. `system_instruction` is accepted by the API but silently ignored by the internal engine. Confirmed Phase 12 Round 2. Don't re-test this.
- **`turn_complete` removed as a commit trigger**: fires on filler utterances ("um", "uh") in sermon speech → excessive fragmentation. Do not re-introduce. Caption lines are committed after 1.5s silence (`PAUSE_THRESHOLD_S`).
- **`SessionResumptionConfig` + `SlidingWindow` are mandatory**: without them the session drops every ~10 min. This is not optional.
- **Port fallback rejected**: if port is in use, show a message + open browser to running service + exit. Do not silently pick a new port — it would break attendee QR codes and cause audio device conflicts.
- **Voice pinned to `orus`**: without `SpeechConfig → PrebuiltVoiceConfig(voice_name="orus")`, Gemini picks a random voice on every GoAway reconnect — audibly jarring mid-sermon.
- **CaptionKit can run in parallel**: Windows shared-mode audio driver allows both apps on the same USB mixer simultaneously. No virtual audio cable needed.
- **`skc_build` conda env for exe builds**: the `agent` env includes PyTorch (~2.5 GB), producing a 3 GB exe. Use the minimal `skc_build` env for ~70 MB output. See `docs/BUILD_EXE.en.md` for full details.
- **Operator vs developer log separation**: runtime events that a volunteer needs to see (Gemini connected, attendee joined, etc.) go to `app/events.py` `OperatorEventLog` (in-memory, polled via `/api/events`). Developer/debug logs go to rotating files `ops.log`/`session.log` in the `logs/` folder. Do not mix these two channels.
- **`ssSet()` JS helper must walk childNodes**: using `el.textContent = label` on a status pill destroys the nested `<span class="ss-tip">` tooltip on every poll tick. The fix walks `childNodes` to update only the bare text node. Do not rewrite this to use `textContent` directly.

---

## Document Index

| Document | Content | Read when |
|---|---|---|
| `app/events.py` | `OperatorEventLog` — thread-safe ring buffer, 7 categories, `since(last_id)` API | Understanding operator event plumbing |
| `docs/PLAN.en.md` | File map, phase history (0–14), tech stack, reliability matrix, config reference | System overview, what phases are done, which files do what |
| `docs/PLAN.ko.md` | Same as above in Korean | — |
| `docs/TECHNICAL.en.md` | Code-level: FastAPI routes, Gemini session config, audio pipeline, asyncio patterns | Code changes, debugging |
| `docs/TECHNICAL.ko.md` | Same as above in Korean | — |
| `docs/WORKTHROUGH.en.md` | Chronological build sessions, verification protocol results (V0–V6), known quirks | Understanding past decisions, known bugs |
| `docs/WORKTHROUGH.ko.md` | Same as above in Korean | — |
| `docs/BUILD_EXE.en.md` | PyInstaller build log (7 attempts), spec decisions, frozen-exe code changes, skc_build env | Rebuilding the exe |
| `docs/BUILD_EXE.ko.md` | Same as above in Korean | — |
| `CHANGELOG.md` | Version history (English) | What changed in each release |
