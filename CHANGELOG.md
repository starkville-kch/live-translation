# Changelog

All notable changes to the Starkville Korean Church Live Translation System will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.7.0] - 2026-07-15

### Added
- **Structured operator event log (`app/events.py`):**
  - New `OperatorEventLog` class — thread-safe ring buffer (50 events, `deque` + `threading.Lock`) with 7 categories: success, audio, gemini, network, user, warning, error.
  - `/api/events?since=N` endpoint for incremental polling; frontend polls every 1.5s and appends new entries with auto-scroll and manual-override scroll tracking.
  - Expandable event details panel (click any row to show key/value detail dict).
  - DOM trimmed to 50 entries; newest events scroll into view automatically.
- **Status strip on operator console:**
  - 5 colored pill badges beneath the title bar: Audio, Gemini, Internet, Translation, Web Server.
  - Three states: green (ok), amber (warn), red (error), each with bilingual hover tooltips.
  - Pills updated every second from the existing `/api/status` poll.
- **Event instrumentation across all modules:**
  - `app/audio.py`: fires events on device connect, signal-lost/restored transitions, and OSError disconnect.
  - `app/gemini_session.py`: fires events on connect, reconnect (with attempt count), GoAway, and max-retries failure.
  - `app/broadcast.py`: fires events on attendee join/leave with current count.
  - `app/server.py`: fires events on system start, service start/stop/pause/resume, and auto-stop changes.

### Changed
- Operator event log replaced the old `lastEvent` diff-based log entry with a proper structured event stream.
- Status strip pills are centered in the strip; control buttons layout unchanged.

---

## [1.6.0] - 2026-07-14

### Added
- **Single executable (.exe) support:**
  - Added `SKC_translation.spec` — PyInstaller build spec to package the server into a ~70 MB single Windows executable.
  - Added `build_exe.bat` — one-click build script with environment setup instructions.
  - Build artifacts (`build/`, `dist/`) output to `.agent/scratch/exe/` and gitignored automatically.
  - Full build attempt log documented in `docs/BUILD_EXE.md`.

### Changed
- **`main.py` — frozen exe compatibility:**
  - `uvicorn.run("main:app", ...)` → `uvicorn.run(app, ...)`. String-based import fails inside a frozen exe (no `main.py` on disk).
  - Added browser auto-open — opens `http://localhost:{port}/` in the default browser 2 seconds after server starts.
  - Added port-conflict detection — if port is already in use, prints a message, opens the browser to the running service, and exits cleanly.
- **`app/config.py` — frozen path fix:**
  - Added `getattr(sys, "frozen", False)` check. When frozen, looks for `config.yaml` and `.env` next to the exe instead of inside the temp extraction folder.
- **`app/logger.py` — frozen log directory fix:**
  - When frozen, logs are written to `logs/` relative to the exe location. Without this fix, logs would go to the temp folder and be lost on exit.
- **`SKC_start.bat` — removed duplicate browser open:**
  - Removed the `timeout /t 4` browser-open logic since `main.py` now handles it directly.

---

## [1.5.0] - 2026-07-13

### Added
- **Korean source text streaming on operator preview:**
  - Introduced `"source"` SSE event kind to stream Korean input transcription deltas in real-time.
  - Operator preview now renders Korean+English caption pairs using `getOrCreateLivePair()` / `commitLivePair()` DOM helpers. Attendee page ignores `source` events entirely.
- **Max-line-length overflow protection:**
  - Added `MAX_LINE_CHARS = 150` force-commit safety net in `CaptionBroadcaster` to prevent screen freeze during long continuous speech.
  - `_find_split()` searches the last 60 characters for a natural boundary (`. `, `! `, `? `, `; `, `, `) before falling back to the last word boundary.
- **Korean language hint for audio transcription:**
  - Added `language_hints=types.LanguageHints(language_codes=["ko", "en"])` to prevent the model from misidentifying Korean as Vietnamese. `"en"` included to handle English scripture quotations.

### Changed
- **Operator console layout reorganized:**
  - Left column order finalized: Input Device → Status → Preview → Control Buttons → Auto-Stop+Exit row.
  - Right column order finalized: Audio Monitor → Event Log → QR Code (bottom).
  - Auto-Stop timeout selector and Exit System button consolidated into a single row; Auto-Stop label replaced by tooltip icon.
  - Exit System button expanded to half the row width for easier access.
- **Status card compacted to 4-column grid:**
  - Long-value rows (오디오 입력, Gemini 세션, 모델) span full width; short numeric stats share rows in pairs. Height reduced from 9 rows to 6 rows (~33% shorter).
- **Uvicorn access logs suppressed:**
  - `access_log=False` to eliminate HTTP request noise from the operator event log.
- **Caption commit strategy reverted to silence-only:**
  - `turn_complete`-based commit tested and rejected — fires on filler utterances causing excessive fragmentation. 1.5s silence timer remains the sole primary commit trigger.

---

## [1.4.0] - 2026-07-13

### Added
- **Audio capture rate-limiting, resampling upgrade, and host API clarification:**
  - Diagnosed a critical driver bug where Windows DirectSound input devices fail to block on `stream.read()`, returning instantly and flooding the Gemini session with duplicate buffers (500,000ms latency, choppy audio).
  - **DirectSound rejection:** Devices under the Windows DirectSound host API are refused at startup with a clear error message.
  - **Native 16kHz mono capture:** When the device supports 16kHz mono natively, the stream opens directly at that format, bypassing all software resampling.
  - **USB hot-plug reconnection:** If the USB mic disconnects mid-capture, the capture thread retries with exponential backoff (2s → 30s cap).
  - Upgraded resampling pipeline to SciPy-based 4th-order Butterworth LPF (7.5 kHz cutoff) + phase-tracking linear interpolator for proper anti-aliasing.
  - Replaced deprecated `audioop` module with NumPy/SciPy (Python 3.13 ready).
  - Fixed queue overflow to evict the oldest chunk first on `QueueFull` (FIFO).
  - Added Host API name (`[MME]`, `[Windows DirectSound]`, `[Windows WASAPI]`) to device listing in the operator dropdown.
- **Auto-commit silence segmentation:**
  - Async auto-commit silence detection task in `GeminiSession` — splits long turns after 1.5s of silence.
- **UI caption and preview timestamping:**
  - Injected relative timestamps (`[MM:SS]`) into SSE commit payloads.
  - Visual timestamps in both attendee caption page and operator preview.
  - Gold CSS styling (`var(--color-gold-500)`) for timestamps on the attendee screen.
- **Concurrency hardening:**
  - `ServiceState` state machine (`STOPPED`, `STARTING`, `RUNNING`, `STOPPING`, `FAILED`) + global async lock `_state_lock` to prevent duplicate concurrent sessions.
  - Unified `_teardown()` for all cleanup.
  - `_auto_stop_on_failure` callback on `FAILED` state.
- **Button and badge synchronization:**
  - Real-time sync of operator dashboard controls and badges across multiple tabs.
- **Audio device selection auto-sync:**
  - Saved `device_index` exposed in `/api/status`.
  - `loadDevices()` JS auto-selects the saved device on page load.
  - Instant `change` listener on `device-select` to persist changes to `config.yaml` immediately.

### Changed
- **Retry loop safety:** Clean `asyncio.CancelledError` handling; reconnect counter reset on success; backoff capped at 60s.
- **Turn-onset latency tracking:** Replaced continuously growing calculation with per-turn onset measurement.
- **Diagnostic cleanups:** Removed temporary WAV dump logic from `app/audio.py`.

---

## [1.3.0] - 2026-07-12

### Added
- **Graceful web shutdown:**
  - Secure localhost-only `/api/shutdown` endpoint — stops sessions and terminates via `SIGINT`.
  - Red `🔴 프로그램 완전 종료 (Exit System)` button on operator console with bilingual confirmation dialog.
  - Replaces the console with a clean "System Successfully Terminated" guidance screen on shutdown.
- **Collapsible configuration guide:**
  - Comprehensive bilingual `config.yaml` guide added to `docs/HOW_TO_USE.md` and `/help` page, wrapped in a collapsible `<details>` panel.

### Changed
- **Operator guidance:** Updated Stop Service workflow to prioritize the Web Shutdown button over command-line key combos.
- **UI fixes:** Fixed collapsible details arrow marker vertical alignment; brightened help page hero text to pure white (`#ffffff`).

---

## [1.2.0] - 2026-07-11

### Added
- **Adjustable auto-stop timeout:**
  - Async background `_auto_stop_check()` thread monitors microphone signals.
  - Automatically stops the Gemini Live session when input is silent (`NO_SIGNAL`) or disconnected for a user-specified duration.
- **Console setting interface:**
  - Auto-Stop Timeout dropdown added to the Input Device Settings card.
  - Options: Disabled (0 min), 1 min (test), 5 min, 10 min (default), 15 min, 20 min, 30 min.
  - Synced via AJAX and persisted to `config.yaml`.
- **System logging:** Operational log notices when auto-shutdown triggers.

### Changed
- Added `.agent/` directory to `.gitignore`.

---

## [1.1.0] - 2026-07-10

### Added
- **Translated audio playback:** Binary WebSocket `/audio-stream` + Web Audio API. Voice pinned to `orus` (deep male) via `SpeechConfig`.
- **Transcript export:** Post-service session directories under `logs/sessions/YYYYMMDD_HHMMSS/` with `ko.txt`, `en.txt`, `aligned.txt`, `summary.txt`.
- **Service controls:** Pause and resume buttons on the operator console.

### Changed
- Refactored server logging to dual rotating files: `ops.log` (INFO+) and `session.log` (DEBUG+).

---

## [1.0.0] - 2026-07-09

### Added
- **Core translation pipeline:** Real-time PCM16 audio capture, mono downsampling, Google GenAI Live API integration with `gemini-3.5-live-translate-preview`.
- **Operator console:** Presbyterian bulletin-styled dashboard with device selection, audio level meters, session status badges, latency tracker, cost tracker, and live event log.
- **Attendee page:** Mobile-friendly live caption screen (`/live`) streaming over SSE.
- **Session recovery:** Automated session resumption for Google API GoAway terminations.
