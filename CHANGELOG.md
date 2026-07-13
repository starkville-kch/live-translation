# Changelog

All notable changes to the Starkville Korean Church Live Translation System will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.4.0] - 2026-07-13

### Added
- **Audio Capture Rate-Limiting, Resampling Upgrade, and Host API Clarification:**
  - Diagnosed a critical driver bug where Windows DirectSound input devices fail to block on `stream.read()`, returning instantly and causing the capture loop to run at warp speed. This flooded the Gemini session with duplicate buffers and resulted in 500,000ms latency and choppy/meaningless audio.
  - **DirectSound rejection:** Devices under the Windows DirectSound host API are now refused at startup with a clear error message, preventing the non-blocking read bug from ever reaching production.
  - **Native 16kHz mono capture:** When the selected device supports 16kHz mono natively (e.g. MME devices), the stream is opened directly at the target format, completely bypassing software downmixing and resampling — matching Google's own reference implementation pattern.
  - **USB hot-plug reconnection:** If the USB mic disconnects mid-capture, the capture thread now retries with exponential backoff (2s → 30s cap), re-initializing PyAudio's device list on each attempt so that re-plugged USB devices are discoverable. The retry loop is interruptible by `stop()`.
  - Implemented an automatic rate-limiting fallback sleep as a safety net for any remaining non-blocking driver behaviors.
  - Upgraded the resampling fallback pipeline to use a SciPy-based 4th-order Butterworth low-pass filter (cutoff at 7.5 kHz) combined with a phase-tracking linear interpolator for proper anti-aliasing.
  - Completely replaced the deprecated standard library `audioop` module with NumPy/SciPy-based algorithms (downmixing, anti-aliased resampling, and RMS calculation), prepping the project for Python 3.13.
  - Fixed the queue overflow behavior in `_enqueue` to evict the oldest chunk first on `QueueFull` (FIFO), ensuring the pipeline prioritizes live, recent audio when backpressure occurs.
  - Updated the device listing logic to append the Host API name (e.g. `[MME]`, `[Windows DirectSound]`, `[Windows WASAPI]`) to each device in the operator configuration dropdown.
- **Auto-Commit Silence Segmentation:**
  - Implemented an asynchronous auto-commit silence detection task in `GeminiSession` that automatically splits long turns into distinct transcripts with separate relative timestamps after 1.5s of silence (matching the broadcast pause threshold).
  - Pre-empts `turn_complete` limitations when speakers talk continuously or the model loops, preventing a single giant line in exported logs (`aligned.txt`, `ko.txt`, `en.txt`).
- **UI Caption and Preview Timestamping:**
  - Injected relative timestamps (`[MM:SS]`) into Server-Sent Events (SSE) commit payloads.
  - Added visual timestamping to committed captions in both the attendee caption page and the operator console preview area.
  - Added a premium gold-toned (`var(--color-gold-500)`) CSS styling for the `<span>` wrapped timestamps on the attendee screen to maintain high-end editorial aesthetics.
- **Concurrency Hardening:**
  - Implemented `ServiceState` state machine (`STOPPED`, `STARTING`, `RUNNING`, `STOPPING`, `FAILED`) and a global async lock `_state_lock` in `app/server.py` to prevent duplicate concurrent audio/session threads.
  - Added unified `_teardown()` function to handle all thread joining, queue draining, and session termination cleanup.
  - Added automatic shutdown/recovery `_auto_stop_on_failure` callback when the connection enters the `FAILED` state.
- **Button and Badge Synchronization:**
  - Synchronized operator dashboard controls (`btnStart`, `btnPause`, `btnStop`) and badge state indicators in real-time across multiple tabs.
- **Audio Device Selection Auto-Sync:**
  - Added saved `device_index` to `/api/status` response to expose the currently configured audio device index.
  - Updated the operator console's `loadDevices` javascript logic to automatically pre-select the saved audio device index from `config.yaml` on page load, rather than defaulting to index `0` (Microsoft Sound Mapper).
  - Added an instant `change` listener to `device-select` in the operator console UI to persist any device selection changes immediately to `config.yaml` via `/api/devices/select`.

### Changed
- **Retry Loop Safety:**
  - Cleanly handle and re-raise `asyncio.CancelledError` inside `_run_with_retry` to prevent orphan tasks.
  - Reset reconnection attempt counter on successful runs and capped backoff delay at 60s.
- **Turn-Onset Latency Tracking:**
  - Replaced continuously growing latency calculation with turn-onset latency computed once at the start of each spoken turn.
- **Diagnostic Cleanups:**
  - Cleaned up temporary audio pipeline diagnostic codes and WAV dumping logic in `app/audio.py` after resolving DJI Mic Mini audio pipeline checks.

## [1.3.0] - 2026-07-12

### Added
- **Graceful Web Shutdown:**
  - Implemented a secure localhost-only endpoint `/api/shutdown` that stops active sessions and terminates the Python server process via `SIGINT`.
  - Added a distinct red `🔴 프로그램 완전 종료 (Exit System)` button on the operator console with a bilingual confirmation dialog to prevent accidental triggers.
  - Replaces the console page with a clean, friendly "System Successfully Terminated" guidance screen once the server goes offline.
- **Collapsible Configuration Guide:**
  - Added a comprehensive, bilingual guide explaining all `config.yaml` options (audio devices, Gemini settings, network, and logging defaults) inside `docs/HOW_TO_USE.md` and the browser helper page (`/help`).
  - Wrapped the guide in a collapsible `<details>` panel to keep day-to-day documentation clean.

### Changed
- **Operator Guidance Updates:**
  - Updated the Stop Service workflow documentation to prioritize the Web Shutdown button over command-line key combos (reducing the risk of orphaned background zombie processes locking port 8000).
- **UI & Alignment Enhancements:**
  - Fixed vertical alignment of the collapsible details arrow marker using a custom flexbox-based CSS pseudo-element in `how_to_use.html`.
  - Brightened the helper page hero description text to pure white (#ffffff) to maximize readability against the dark navy background.

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
