# Agent Context — SKC Live Translation

## Project Summary
Real-time Korean→English captioning appliance for church services.
Audio captured from USB mixer → streamed to Gemini Live API → English captions fanned out to attendee phones via SSE; translated audio delivered via binary WebSocket.

## Architecture
```
[USB Mixer] → app/audio.py (PCM16 16kHz) → app/gemini_session.py (Gemini Live)
                                                       ↓
                                              app/broadcast.py
                                              ├── SSE caption fanout (_clients)
                                              └── PCM audio fanout (_audio_clients)
                                                       ↓
                                              app/server.py (FastAPI)
                                              ├── GET  /                 ← operator page
                                              ├── GET  /live             ← attendee phone page
                                              ├── GET  /stream           ← SSE caption stream
                                              ├── WS   /audio-stream     ← binary PCM16 audio
                                              ├── GET  /api/status       ← JSON status
                                              ├── GET  /api/devices      ← device list
                                              ├── POST /api/devices/select
                                              ├── POST /api/start
                                              ├── POST /api/stop         ← also writes transcript files
                                              ├── POST /api/pause
                                              ├── POST /api/resume
                                              ├── GET  /api/qr.png
                                              └── GET  /logo.webp
```

## Key Design Decisions
- **Model**: `gemini-3.5-live-translate-preview` — auto-selected at startup via `resolve_live_model()`. Uses `translation_config` (not system prompt). Translation text in `server_content.output_transcription.text`; Korean source in `server_content.input_transcription.text`.
- **Session persistence**: One session per service run. Session resumption handle stored; reconnects automatically on GoAway (every ~10 min) with exponential backoff.
- **Caption UX**: Current line replaced in-place as tokens arrive; committed to scrollback after 1.5s pause (`PAUSE_THRESHOLD_S`). `turn_complete` signal was tested as a commit trigger but rejected — it fires on filler utterances in sermon speech, causing excessive fragmentation. Do not re-introduce.
- **Overflow protection**: `MAX_LINE_CHARS = 150` force-commits in `CaptionBroadcaster` when no silence pause occurs. `_find_split()` searches the last 60 chars for `. ` / `! ` / `? ` / `; ` / `, ` boundaries before falling back to last space.
- **Korean source streaming**: `"source"` SSE event kind delivers Korean input transcription deltas. Attendee page ignores it. Operator preview pairs Korean+English using `getOrCreateLivePair()` / `commitLivePair()`.
- **Language detection**: `language_hints=types.LanguageHints(language_codes=["ko", "en"])` on `input_audio_transcription` prevents Korean→Vietnamese misidentification. `"en"` included for English scripture quotations.
- **Audio transport**: Captions over SSE; translated audio PCM16 (24kHz) over binary WebSocket (`WS /audio-stream`). Audio clients that have playback disabled generate zero traffic.
- **Voice pinning**: Translated audio pinned to `orus` (deep male) via `SpeechConfig → PrebuiltVoiceConfig`. Without this, Gemini picks a random voice on every reconnect.
- **Transcript export**: On stop, `flush_current_turn()` commits any in-progress turn, then per-session folder written to `logs/sessions/YYYYMMDD_HHMMSS/`.
- **Two log files**: `ops.log` (server/audio, INFO+) and `session.log` (Gemini session, DEBUG+). `propagate=False` on each logger.
- **Security**: Gemini API key in `.env` only — never hardcoded, never shown in any UI.

## Files
| File | Purpose |
|------|---------|
| `main.py` | Entry point (`uvicorn main:app`) |
| `config.yaml` | Runtime config (device, port, log path, model) |
| `.env` | `GEMINI_API_KEY` (never committed) |
| `app/config.py` | Config loader + `save_audio_device()`, `save_gemini_model()` |
| `app/logger.py` | Two rotating handlers: `ops.log` and `session.log` |
| `app/audio.py` | PyAudio capture, PCM16 resampling, RMS level metering, disconnect detection |
| `app/gemini_session.py` | Gemini Live session, reconnection, GoAway, transcript buffers, `flush_current_turn()` |
| `app/broadcast.py` | SSE caption fanout + binary PCM audio fanout; accumulates `_current_ko` for glossary |
| `app/server.py` | FastAPI routes + embedded HTML + transcript export |
| `logs/ops.log` | Server/audio operational log (INFO+) |
| `logs/session.log` | Gemini session log with `[KO]`, `[EN delta]`, `[EN turn]` entries (DEBUG+) |
| `app/glossary.py` | `GlossaryCorrector` — loads `config/glossary.yaml`, corrects committed lines |
| `config/glossary.yaml` | Terminology glossary: direct (auto-apply) + review_only (log only) tiers |
| `logs/sessions/YYYYMMDD_HHMMSS/` | Per-session transcript: `summary.txt`, `ko.txt`, `en.txt`, `aligned.txt` |

## Running
```bash
conda activate agent
python -m app.audio --list          # enumerate audio devices
python main.py                      # start the server
```
Then open `http://localhost:8000` in a browser.

## Environment
- Python env: `agent` (conda)
- OS: Windows 11
- Key packages: `google-genai`, `fastapi`, `uvicorn`, `pyaudio`, `qrcode`

## Scratch / Testing
- `.agent/scratch/` — temporary test files, WAV captures, experiment outputs
- `.agent/scripts/` — helper scripts (device test, model probe, end-to-end tests)
- `.agent/skills/` — reusable agent skill definitions

## `.claude/` vs `.agent/`
- `.claude/settings.json` — Claude Code's own config (fixed path, cannot be renamed)
- `.agent/` — this project's AI context, scripts, and skills (portable)

## Phase Status
- [x] Phase 0: Audio capture + PCM16 pipeline
- [x] Phase 1: Gemini Live session (translation_config, resumption, compression)
- [x] Phase 2: FastAPI + SSE + attendee/operator pages
- [x] Phase 3: Reliability (GoAway, retry, FAILED state)
- [x] Phase 4: Operator status, QR code
- [x] Phase 5: Visual and UX revamp (Presbyterian bulletin aesthetic, English attendee page, local PCA logo)
- [x] Phase 6: Pause/resume, runtime/cost display, session log, split logging
- [x] Phase 7: Translated audio playback (binary WebSocket, Web Audio API, pinned `orus` voice)
- [x] Phase 8: Post-service transcript export (per-session folder, ko/en/aligned files)
- [x] Phase 9: Audio pipeline hardening, auto-commit silence segmentation, timestamp injection, concurrency state machine, button sync (v1.4.0)
- [x] Phase 10: Caption overflow protection, Korean language hint fix, turn_complete revert, Korean+English paired operator preview, operator console UX overhaul (v1.5.0)
- [x] Phase 11: Terminology glossary correction pass + Part A context-seed probe (v1.6.0)
- [x] V0–V6 verification protocol passed
