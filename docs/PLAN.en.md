# Technical Maintainer & Architecture Plan
### Live Translation System

> **Korean version**: [PLAN.ko.md](PLAN.ko.md)

This document serves as the Technical Maintainer & Architecture Plan for the Live Translation System, providing developer and system specifications for maintenance and future development.

---

## 📌 Table of Contents
1. [System Diagram](#1-system-diagram)
2. [Key Design Decisions](#2-key-design-decisions)
3. [Tech Stack Choices & Alternatives Comparison](#3-tech-stack-choices--alternatives-comparison)
4. [File Map](#4-file-map)
5. [Reliability Requirements](#5-reliability-requirements)
6. [Phase Breakdown](#6-phase-breakdown)
7. [Configuration Reference](#7-configuration-reference)
8. [Future Phases](#8-future-phases)

---

## 1. System Diagram

```
USB Mixer
    │ (audio cable)
    ▼
Windows PC (this app)
    │
    ├─ app/audio.py ──────────── captures 16kHz mono PCM16
    │                            resamples from device native rate
    │                            RMS level metering (10Hz)
    │                            detects no-signal / disconnection
    │
    ├─ app/gemini_session.py ─── streams audio to Gemini Live API
    │                            receives Korean source transcription (log only)
    │                            receives English translation text
    │                            handles session resumption + context compression
    │                            handles GoAway, exponential backoff reconnect
    │
    ├─ app/broadcast.py ─────── in-memory SSE fanout (captions) + audio queue fanout (PCM)
    │                            current-line replace UX (1.5s commit threshold)
    │                            separate audio client list for WS /audio-stream
    │
    └─ app/server.py ─────────── FastAPI
          │
          ├─ GET  /                  operator page (device select, start/stop, QR, preview, audio controls)
          ├─ GET  /live              attendee page (large captions, font size, English UI, light theme)
          ├─ GET  /stream            SSE stream → attendee phones (captions only)
          ├─ WS   /audio-stream      binary WebSocket → raw PCM16 chunks to audio-enabled clients
          ├─ GET  /api/status        JSON status (audio, session, attendees, model)
          ├─ GET  /api/devices       list input devices
          ├─ POST /api/devices/select save selected device to config.yaml
          ├─ POST /api/start         start service
          ├─ POST /api/stop          stop service + write session transcript files
          ├─ POST /api/pause         pause audio pipe + billing
          ├─ POST /api/resume        resume audio pipe + billing
          ├─ GET  /logo.webp              local PCA logo (served from app/)
          ├─ GET  /api/qr.png             QR code PNG (links to /live)
          └─ GET  /api/events?since=N     incremental operator event poll
```

---

## 2. Key Design Decisions

### Model selection
- **`gemini-3.5-live-translate-preview`** — selected at startup by querying the API.
- Uses `response_modalities=["AUDIO"]` + `translation_config`; translation text arrives via `server_content.output_transcription.text`.
- Korean source arrives via `server_content.input_transcription.text` (log only, never shown to attendees).
- `gemini-3.1-flash-live-preview` crashed after ~30s of continuous audio (error 1011) in Phase 12 Round 3 — unsuitable for a 60–90 min service.
- `system_instruction` is accepted by the translate model but ignored by its internal engine (Phase 12 Round 2).

### SSE for captions, binary WebSocket for audio
- Caption events (update, commit, unavailable, ping, paused, resumed) travel over SSE (`/stream`).
- Translated audio PCM16 chunks travel over a separate binary WebSocket (`WS /audio-stream`).
- Phones that have audio disabled generate zero audio traffic.
- SSE provides native auto-reconnect in iOS Safari on flaky venue WiFi.

### Single session per service
- One Gemini session for the entire 60–90 min service.
- `SessionResumptionConfig` mandatory — without it the WebSocket drops every ~10 min.
- `SlidingWindow` context compression mandatory — audio sessions cap at ~15 min otherwise.
- **GoAway session cycles (~9-minute and ~27-minute boundaries)**: Gemini Live translate sessions issue a `GoAway` signal approximately every 9 minutes (commonly observed at ~8-10m and ~27m boundaries). This is now handled transparently by the resumption/retry logic, resetting the retry count on each successful reconnect to avoid budget exhaustion.
- **Retry reset on success**: The session reconnection attempt counter (`self._attempt`) is reset to 0 upon successful connection. This prevents regular GoAway reconnects from exhausting the retry budget.
- **Bounded auto-restart pipeline**: If the Gemini session fails permanently, `server.py` runs a bounded recovery loop (3 attempts: 2s, 5s, 15s) with a frontend warning status chime/flash before entering a terminal FAILED state.

### Caption UX
- Current line replaced in-place as tokens stream in (no flickering append).
- Line committed to scrollback after 1.5s of no new tokens.
- `MAX_LINE_CHARS = 150` force-commit safety net — prevents screen freeze during long continuous speech.
- `_find_split()` searches the last 60 chars for a natural boundary before falling back to the last space.

### Voice pinning
- Translated audio voice is pinned to **`orus`** (deep male) via `SpeechConfig → VoiceConfig → PrebuiltVoiceConfig`.
- Without this, Gemini picks a random voice on every session and GoAway reconnect — audible mid-sermon.

### Cost model
- Gemini 3.5 Live Translate Paid Tier: Input $3.50/1M tokens, Output $21.00/1M tokens.
- Combined rate: **~$0.0368/min** → ~$2.21 per 60-min service.
- Single server session billed regardless of how many attendee devices are connected.

### Visual Design & UX
- **Presbyterian bulletin theme**: cream background (`#faf8f5`), navy headers (`#1a2a42`), gold accents (`#b8923e`).
- **Bottom-aligned captions**: new lines push up from bottom — minimises eye travel.
- **Typography**: `Source Serif 4` / `Inter` (English), `Noto Serif KR` / `Noto Sans KR` (Korean).
- **Branded QR code**: navy rounded modules, gold finder patterns, PCA logo with quiet-zone buffer, `ERROR_CORRECT_H`.

---

## 3. Tech Stack Choices & Alternatives Comparison

| Component | Chosen | Alternatives | Why |
| :--- | :--- | :--- | :--- |
| **App runtime** | Python 3.10+ / FastAPI / Uvicorn | Node.js, Go, Rust | First-class Gemini Live SDK; PyAudio integration; FastAPI async SSE/WS fanout |
| **Audio capture** | PyAudio (PortAudio) | sounddevice, Pygame | Direct Windows WASAPI access; pure bytes — no NumPy required at capture |
| **Translation** | Gemini Live (`gemini-3.5-live-translate-preview`) | OpenAI Realtime, Whisper+DeepL+ElevenLabs | Single-pass STT+translate+TTS, ~0.5s latency; ~85% cheaper than OpenAI Realtime |
| **Caption streaming** | SSE + binary WebSocket hybrid | WebSocket only, WebRTC/LiveKit | SSE native auto-reconnect (iOS Safari); audio-only WS keeps caption-only devices traffic-free |
| **Browser audio** | Web Audio API (PCM16 queue) | HTML5 `<audio>`, HLS/DASH | Eliminates 5–10s HLS buffer; raw 24kHz PCM16 → sub-200ms latency synced with captions |

---

## 4. File Map

| File | Role |
|------|------|
| `main.py` | Entry point — uvicorn run, browser auto-open, port conflict detection |
| `config.yaml` | Runtime config (device index, port, log path, model) |
| `.env` | `GEMINI_API_KEY` — never committed |
| `requirements.txt` | Python dependencies |
| `SKC_start.bat` | One-click server launcher (activates conda `agent` env) |
| `SKC_translation.spec` | PyInstaller build spec — produces single ~70MB exe |
| `build_exe.bat` | One-click exe build script with environment setup instructions |
| `app/config.py` | Config loader + `save_audio_device()`, `save_gemini_model()`, `admin_cfg()` |
| `app/events.py` | `OperatorEventLog` — thread-safe ring buffer (50 events), 7 categories, `since(last_id)` API |
| `app/logger.py` | Rotating file + console logger |
| `app/audio.py` | PyAudio capture, PCM16 resampling, RMS metering, disconnect detection |
| `app/gemini_session.py` | Gemini Live session, auto model selection, reconnection, GoAway |
| `app/broadcast.py` | SSE caption fanout + binary PCM audio fanout (`_audio_clients`) |
| `app/server.py` | FastAPI routes + embedded HTML (operator, attendee pages) + logo route |
| `app/glossary.py` | Post-translation glossary correction pass (PCA terminology enforcement) |
| `config/glossary.yaml` | Glossary definitions (direct substitution entries + review-only entries) |
| `docs/HOW_TO_USE.md` | Language selector index → `.en.md` / `.ko.md` |
| `docs/HOW_TO_USE.en.md` | Volunteer operator manual (English) |
| `docs/HOW_TO_USE.ko.md` | Volunteer operator manual (Korean) |
| `docs/PLAN.md` | Architecture plan and key decisions (this file) |
| `docs/WORKTHROUGH.md` | Chronological build and session history (bilingual) |
| `docs/TECHNICAL.md` | Code-level technical reference (bilingual) |
| `docs/BUILD_EXE.md` | Single exe build attempt log (bilingual) |
| `logs/ops.log` | Operational log: server start/stop, audio device events, reconnects (INFO+) |
| `logs/session.log` | Gemini session log: connect, `[KO]` source, `[EN turn]` translation (DEBUG+) |
| `logs/sessions/YYYYMMDD_HHMMSS/` | Per-session folder: `ko.txt`, `en.txt`, `aligned.txt`, `summary.txt` |

---

## 5. Reliability Requirements

| Scenario | Behaviour |
|----------|-----------|
| Normal 10-min WebSocket boundary | GoAway → auto-reconnect → captions resume in ~2–3s |
| Internet outage | Exponential backoff (2s, 4s, 8s); FAILED state after 3 failures |
| FAILED state | "Translation unavailable" pushed to all attendee SSE clients |
| Audio device disconnect | `AudioStatus.DISCONNECTED` on stream read error |
| Prolonged silence (>10s) | `AudioStatus.NO_SIGNAL` — distinguished from disconnect |
| Attendee phone WiFi drop | SSE auto-reconnects; phone shows "Reconnecting…" during gap |

---

## 6. Phase Breakdown

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Audio capture: device enum, PCM16, level metering, WAV test | ✅ Done |
| 1 | Gemini Live session: TEXT mode, resumption, compression | ✅ Done |
| 2 | FastAPI + SSE fanout + attendee/operator pages | ✅ Done |
| 3 | Reliability: GoAway, retry, FAILED state | ✅ Done |
| 4 | Operator status API + QR code | ✅ Done |
| 5 | Visual/UX revamp: Presbyterian bulletin theme, English UI, local logo, branded QR | ✅ Done |
| 6 | Operator enhancements: pause, runtime, cost estimate, session log, transcript export | ✅ Done |
| 7 | Translated audio playback: Web Audio API, 24kHz PCM16, binary WS, pinned `orus` voice | ✅ Done |
| 8 | Post-service transcript export: per-session folder with `ko.txt`, `en.txt`, `aligned.txt`, `summary.txt` | ✅ Done |
| 9 | Audio pipeline overhaul: DirectSound rejection, native 16kHz, USB hot-plug, SciPy resampling, audioop removal | ✅ Done |
| 10 | Caption commit refinement: `MAX_LINE_CHARS=150`, `_find_split()`, `ko`/`en` language hints | ✅ Done |
| 11 | Operator console UX overhaul: Korean+English pairs, 4-column status grid, layout reorder | ✅ Done |
| 12 | Translation model 3-round benchmark: `gemini-3.5-live-translate-preview` confirmed optimal | ✅ Done |
| 13 | Single executable: PyInstaller ~70MB exe, `SKC_translation.spec`, `build_exe.bat` | ✅ Done |
| 14 | Operator event log (`app/events.py`), status strip, `/api/events`, `/admin/logs` developer diagnostics | ✅ Done |
| 15 | Bounded auto-recovery loop, detailed close reason logging, operator warning alerts, and 27-min GoAway root cause resolution | ✅ Done |
| 16 | mDNS hostname advertisement (`python-zeroconf`), dynamic URL resolver, primary/fallback display on operator console | ✅ Done |
| 17 | UI refactoring to external templates: `attendee.html` and `operator.html` separated from `server.py`, with dynamic loader enabling hot-reload in development | ✅ Done |
| V0–V5, V14–V19 | Verification protocol | ✅ All passed |

---

## 7. Configuration Reference

```yaml
audio:
  device_index: 2       # set by operator; run `python -m app.audio --list` to find index
  sample_rate: 16000
  channels: 1
  chunk_ms: 100

gemini:
  model: gemini-3.5-live-translate-preview  # auto-updated on startup

network:
  host: 0.0.0.0   # bind all interfaces (localhost + WiFi attendees)
  hostname: skc-live.local
  port: 8080
  # public_url: "http://192.168.1.x:8080"  # override if auto-detect picks wrong interface

logging:
  log_dir: logs
  max_bytes: 10485760   # 10 MB
  backup_count: 5
```

---

## 8. Future Phases

### Phase 18 — Multi-language simultaneous interpretation (Chinese, etc.)
- The Gemini Live translate model currently exposes one `target_language_code` per session.
- Supporting two languages simultaneously requires two parallel `GeminiSession` instances — one targeting `"en"`, one targeting `"zh"`.
- Each session receives the same microphone audio (duplicate the `_pipe` coroutine).
- The attendee page (`/live`) would need a language selector switching between `/stream?lang=en` and `/stream?lang=zh`.

### Phase 19 — Cloud deployment for remote attendees
- Deploy `main.py` to a small cloud VM (Google Cloud Run, Railway, or a VPS).
- Audio cannot be captured in the cloud — the PC captures audio and POSTs PCM chunks to the cloud server via a lightweight WebSocket.
- The cloud server pipes audio into Gemini Live and fans SSE captions out to all attendees globally.

### Phase 20 — Parallel session handoff on GoAway (Lever 2 reconnect optimization)
- Overlap the old session with the new session to achieve a near-zero reconnect gap.
- Upon receiving a `GoAway` warning (utilizing `time_left` if available in the SDK response), spin up a new parallel `GeminiSession` in the background.
- Keep feeding audio to the old session until the new session's connection is fully established.
- Instantly swap the active session reference and teardown the old session, reducing caption delivery latency during reconnects to effectively zero.
