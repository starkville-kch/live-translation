# Changelog

All notable changes to the Starkville Korean Church Live Translation System will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.2.0] - 2026-07-11

### Added
- **Adjustable Auto-Stop Timeout:**
  - Implemented an asynchronous background thread `_auto_stop_check()` in the server backend to monitor microphone signals.
  - Automatically terminates the Gemini Live session and stops audio capture when input is silent (`NO_SIGNAL` / RMS below 50) or disconnected (`DISCONNECTED`) for a user-specified duration.
- **Console Setting Interface:**
  - Added a visual settings dropdown for "Auto-Stop Timeout" directly inside the **Input Device Settings** card on the operator console.
  - Options include: *Disabled (0 min)*, *1 min (test)*, *5 min*, *10 min (default)*, *15 min*, *20 min*, and *30 min*.
  - Settings are dynamically synchronized with the server via AJAX and persist across server restarts via updates to `config.yaml`.
- **System Logging:**
  - Added operational logging notices when the automatic shutdown triggers to save API costs.

### Changed
- Configured `.gitignore` to ignore the entire `.agent/` directory to prevent committing local AI configurations.

---

## [1.1.0] - 2026-07-10

### Added
- **Translated Audio Playback:**
  - Integrated translated English audio playback using binary WebSockets (`/audio-stream`) and the Web Audio API on the client side.
  - Pinned the translated audio voice to `orus` (deep male) via `SpeechConfig` to ensure voice stability across session reconnects.
- **Transcript Export:**
  - Added post-service transcript exporters that create session directories under `logs/sessions/YYYYMMDD_HHMMSS/` containing aligned and raw (`ko.txt`, `en.txt`, `aligned.txt`, `summary.txt`) transcript files.
- **Service controls:**
  - Added visual buttons on the operator console to pause and resume the live stream.

### Changed
- Refactored server logging into a structured/rotating dual-file layout: `ops.log` (operational logs, INFO+) and `session.log` (Gemini WebSocket frames, DEBUG+).

---

## [1.0.0] - 2026-07-09

### Added
- **Core Translation Pipeline:**
  - Real-time PCM16 audio capture and mono downsampling pipeline.
  - Google GenAI Live API integration using `gemini-3.5-live-translate-preview`.
- **Operator Console:**
  - Premium Presbyterian bulletin-styled dashboard with input device selection, real-time audio volume level meters, session status badges, latency tracker, cost tracker, and live event log.
- **Attendee Page:**
  - Minimalist, mobile-friendly live translation screen (`/live`) streaming translation captions in real-time over Server-Sent Events (SSE).
- **Session Recovery:**
  - Implemented automated session resumption logic to handle Google API GoAway connection terminations.
