# Changelog

All notable changes to the Starkville Korean Church Live Translation System will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-08-31

### Added
- **Named Cloudflare Tunnel Support (`app/tunnel.py`, `app/cloudflared_service.py`, `check_skc_live.bat`)**:
  - Non-destructive background health monitoring and readiness check for named Cloudflare tunnel `live.starkvillekoreanchurch.org`.
  - Sunday pre-flight script `check_skc_live.bat` verifying local port binding, Windows `cloudflared` service state, and public HTTPS endpoint reachability.
  - Safe reconnect endpoint `POST /api/reconnect-public`.
  - Windows elevation handling and status detection in setup wizard.
- **Public Host Guard Boundary (`app/server.py`, `PublicHostGuardMiddleware`)**:
  - Default-deny boundary for all requests arriving via the public Cloudflare tunnel hostname.
  - Strictly limits public access to attendee-facing routes (`/`, `/live`, `/stream`, `/audio-stream`, `/ws/telemetry`, `/logo*`, `/static/*`).
  - Rewrites public root `/` requests to `/live` so external attendees automatically receive the attendee client interface.
  - Blocks all administrative endpoints, mutating APIs, and operator pages with `404 Not Found` when accessed over the public domain.
- **Operator Authentication Boundary (`app/operator_auth.py`, `app/server.py`)**:
  - Cryptographic session token verification (HMAC-SHA256) with `HttpOnly` and `SameSite=Strict` cookie enforcement.
  - Environment-driven activation (`SKC_OPERATOR_PASSWORD` set = enabled; unset = disabled with startup/operator warning banner).
  - Auth endpoints `GET /api/auth/status`, `POST /api/auth/login`, and `POST /api/auth/logout`.
  - Operator password modal in `app/templates/operator.html`.
- **Network Telemetry & Real-Time RTT Monitoring (`app/broadcast.py`, `app/templates/attendee.html`, `app/templates/operator.html`)**:
  - Attendee client ping-pong telemetry over `/ws/telemetry` tracking real-time Round Trip Time (RTT).
  - Categorization and aggregation of local Wi-Fi vs. public cloud listeners and latency percentiles.
  - Live public HTTPS status badge and RTT diagnostics displayed in operator console header and QR access card.
- **Operator Console UI Consolidation & Latency Telemetry Accordion (`app/templates/operator.html`)**:
  - Folded detailed Gemini processing and network RTT telemetry into a collapsible accordion (`📊 네트워크 & 지연 세부 분석 (Telemetry Details) ▼`, collapsed by default).
  - Reorganized session metrics into a compact 3x2 grid featuring overall public latency (`공용 체감 지연 (Est. Delay)`) right before runtime.
  - Reduced status monitor card vertical footprint to ensure the live caption preview remains prominently visible on standard desktop screens.
- **Two-Column Desktop Setup GUI (`setup_gui.py`, `SKC_setup.exe`)**:
  - Redesigned setup wizard into an efficient 2-column layout (1060×670 px): Left Card: Church & Local Network; Right Card: Google Gemini API (with masked key row and compact validation); Bottom Card: Public HTTPS / Cloudflare Named Tunnel.
  - Added fixed bottom footer with dynamic readiness status pills (`🟢 Local Network`, `🟢 Gemini API`, `🟢 Public HTTPS`) and primary action buttons.
- **Parallel Multi-Process PyInstaller Builder (`build_parallel.py`, `build_exe.bat`)**:
  - Added multi-process parallel build runner with `-j/--workers` flags and automatic CPU core detection.
  - Streams real-time milestone logs (`[Translation]` and `[Setup]`) with elapsed timestamps.
  - Reports wall-clock time vs sequential compute time and parallel speedup factor.
- **Cloudflare WAF Bot Protection User-Agent (`app/tunnel.py`)**:
  - Configured standard browser User-Agent in probe requests (`_public_check()`) to prevent false-positive HTTP 403 blocks from Cloudflare WAF bot management.

### Changed
- **Port Standardization (`config.yaml`, `app/config.py`, `app/tunnel.py`, `check_skc_live.bat`)**:
  - Standardized local runtime on port `8080` to eliminate collisions with port 80 web servers (IIS, Apache, Docker, CAVS-MIP).
- **Attendee Access & QR Security Pipeline (`app/server.py`, `app/templates/operator.html`)**:
  - Primary QR code encodes stable public HTTPS attendee URL (`https://live.starkvillekoreanchurch.org/live`) without mutating on tunnel disconnects.
  - Added direct `Open Attendee Page` and `Copy Public Link` buttons.
  - Public `/api/qr.png` blocked (404) on public host while remaining accessible without authentication on local operator LAN.
- **PyInstaller Build Specs (`SKC_translation.spec`, `SKC_setup.spec`)**:
  - Added hidden imports for `app.tunnel`, `app.cloudflared_service`, `app.operator_auth`, and `app.config`.


## [2.5.0] - 2026-08-30

### Added
- **Vector SVG UI Icon Ecosystem (`app/templates/operator.html`)**:
  - Replaced legacy text emojis across the operator console with crisp, scalable vector SVG icons: Test Flask (`🧪` ➔ SVG Beaker), Playback Soundwave & Muted Speaker (`🔊`/`🔇` ➔ SVG Soundwave/Mute), Lightning Bolt (`⚡` ➔ SVG Bolt), Power Icon (`⏻` ➔ SVG Power), Stopwatch Timer (`⏱` ➔ SVG Timer), and Audio Refresh (`🔄` ➔ SVG Rescan).
- **Language Drift Recovery FAQ (`how_to_use.html`)**:
  - Added dedicated guidance in the Volunteer Help Guide detailing both manual (`Pause` ➔ `Resume`) and automated (`⚡ Auto`) drift recovery methods when AI encounters background noise or hallucinated languages.

### Changed
- **Operator Dashboard Header Status Strip (`app/templates/operator.html`)**:
  - Streamlined status pills to 3 core pipeline health badges: `🎙️ 오디오 (Audio)`, `🤖 제미나이 AI (Gemini)`, and `🔤 실시간 자막 (Translation)`.
  - Removed redundant `Web Server` and `Internet` badges.
  - Localized all status text and tooltips cleanly in Korean and English.
  - Adjusted Gemini AI status indicator during pause to reflect standby/paused state (`🟡 제미나이 AI`) instead of "Service not started".
- **Segmented Control for Auto Drift Correction (`app/templates/operator.html`)**:
  - Redesigned the auto drift toggle into an intuitive segmented pill (`[ Manual | ⚡ Auto ]`) with dynamic status captions explaining the active recovery behavior.
- **Auto-Stop Label & Tooltip (`app/templates/operator.html`)**:
  - Updated auto-stop timer label to `음성 미감지 시 자동종료` with explanatory tooltip: *"설정한 시간 동안 음성(설교/말씀)이 감지되지 않으면 서비스를 자동으로 안전하게 종료합니다."*
- **Model Row Label (`app/templates/operator.html`)**:
  - Renamed `모델 (Model)` row to `실시간 통역 모델`.
- **Help Guide Standalone Alignment (`how_to_use.html`)**:
  - Purged obsolete Conda/Python terminal commands and manual `config.yaml` edits.
  - Concealed network port numbers in all URL references (`http://skc.local/admin` for operator, `http://skc.local` for attendees).
  - Streamlined every-service workflow into 4 straightforward steps for executable distribution (`SKC_Live_Translation.exe`).

## [2.4.0] - 2026-08-30

### Changed
- **Admin Console Top Navigation Bar (`app/templates/operator.html`)**:
  - Removed `🔵 Internet` status pill from the header status strip to streamline horizontal spacing, retaining `Audio`, `Gemini`, `Translation`, and `Web Server`.
- **Status Monitor Card (`상태 모니터 (STATUS)`)**:
  - Removed the `Gemini 세션` row from the status grid.
  - Fixed audio input device string truncation (`#stat-audio`) with flexible wrapping (`white-space: normal; word-break: break-word;`) to show full device and host API strings without clipping.
  - Inlined the `모델` control row so that the model `<select>` dropdown, `🔒 Locked` status badge, and `[Test]` button sit neatly side-by-side in one compact row.
- **Dynamic Audio Input Device Selection (`app/templates/operator.html`, `app/audio.py`, `app/server.py`)**:
  - Added a compact refresh button (`🔄`) next to the input device selector.
  - Implemented `GET /api/devices?rescan=true` to dynamically re-enumerate PortAudio devices on demand, with active stream protection preventing PyAudio re-initialization while a service is capturing.
  - Added device name caching across refreshes to reliably restore device selection across hardware plug/unplug events even when PortAudio device indices shift.
  - Added `navigator.mediaDevices.ondevicechange` listener to automatically trigger device list refresh when audio hardware is plugged or unplugged.

## [2.3.0] - 2026-08-23

### Added
- **Anti-Contamination & Clean Session Reset Architecture (`app/gemini_session.py`, `app/audio.py`, `app/server.py`, `app/broadcast.py`)**:
  - **Developer API Live Translate Configuration**: Uses official Google Developer API schema with standard `AudioTranscriptionConfig()` (omitting Enterprise-only `language_codes` parameter).
  - **Non-Retryable Configuration Error Handling**: Distinguishes deterministic configuration errors (`ValueError`, schema errors) from transient network failures; halts gracefully without entering reconnect loops or triggering cascading auto-restarts.
  - **Immediate Audio Frame Dropping on Pause**: When service is paused during worship praise/music, the microphone device remains open for level metering but PCM frames are dropped immediately before entering downstream queues.
  - **Clean Pause/Resume Context Boundary & Audio Queue Purging**:
    - `Pause`: Closes active Gemini Live WebSocket connection, discards session resumption token, drains server and broadcaster client audio queues, and signals attendee clients to clear playback buffers. Attendee web/phone SSE connections remain active displaying `— Paused —`.
    - `Resume`: Increments session epoch, drains residual queues, and starts a fresh Gemini Live session with clean context on the same locked model without model re-resolution.
  - **Session Epoch Isolation**: Tagged all inbound server events and client states with session epoch to guarantee stale async responses from prior sessions are discarded immediately.
  - **Conservative Server-Side Activity Detection**: Configured `RealtimeInputConfig` with `START_SENSITIVITY_LOW`, `silence_duration_ms=700`, and `prefix_padding_ms=200` to prevent post-praise ambient sounds and mic handling from triggering false speech starts.
  - **Completed-Turn Asymmetric Language Drift Watchdog & Rolling Window**:
    - Evaluates Gemini's native `input_transcription.language_code` (`ko`/`en` expected $\to 0$; `ja`/`vi`/`zh`/`th` flagged $\to +1$) and `output_transcription.language_code` (non-`en` $\to +2$) strictly on completed utterances/turns.
    - Employs a bounded rolling deque (`maxlen=3`), where two consecutive clean turns reset the drift score to 0.
  - **Switchable Automatic Drift Correction (Default: OFF, Runtime-Only)**:
    - Default mode is safe manual recovery (`Pause` $\to$ `Resume`).
    - Operator can toggle `자동 언어 오류 복구` (Auto Drift Reset) ON or OFF dynamically from the Status card during a running service (`POST /api/config/auto-drift-correction`).
    - Toggling the setting clears drift history instantly without persisting to disk.
    - When OFF, drift detection remains active for diagnostic logging and operator warnings without automatic intervention.
    - When ON, triggers an automatic clean session reset, typically recovering within a few seconds (15s cooldown).
- **Gemini Live Translation Model Selection Engine (`app/model_resolver.py`)**:
  - Direct `preferred_model` configuration with automated two-tier fallback:
    $$\text{preferred\_model} \xrightarrow{\text{fail}} \text{Last Verified Model} \xrightarrow{\text{fail}} \text{fallback\_model}$$
  - Selecting another model from the operator dropdown immediately sets it as `preferred_model` for subsequent services.
- **Strict Live Translate Candidate Classification**: Excludes general Flash/Pro models (e.g. `gemini-2.5-flash`, `gemini-1.5-pro`) and conversational dialogue models (`gemini-3.1-flash-live-preview`); only models with dedicated Live Translation capabilities are eligible.
- **Separate Runtime State Isolation (`var/runtime/model_state.json`)**: `config.yaml` stores administrator intent only (`preferred_model`, `fallback_model`, `voice`), while runtime learned state (`last_known_good_model`, `last_verified_at`, `seen_models`, `dismissed_alerts`) is maintained separately without mutating configuration files.
- **Session Model Locking & Long-Running Resumption**:
  - Operational model is locked upon initial session connection; all in-session reconnects strictly reuse the locked model.
  - The application uses `SessionResumptionConfig` to survive Live connection rotation and `ContextWindowCompressionConfig` with `SlidingWindow` to manage context during long-running worship services.
- **Operator Console Control Bar Separation & Pause Reminder (`app/templates/operator.html`, `app/server.py`)**:
  - Separated non-clickable service status indicator from action buttons into a fixed 3-column layout (`Status Pill` | `Primary Action` | `Stop Button`):
    - **Stopped**: `○ 대기 중 (STOPPED)` + `[ ▶ 번역 시작 (Start) ]` (green) + `[ ■ 서비스 종료 (Stop) ]` (neutral disabled gray, never red when disabled).
    - **Starting / Resuming / Resetting**: `⟳ 번역 연결 중...` (spinner) + `[ ⏳ 연결 중… ]` + `[ ■ 서비스 종료 (Stop) ]`.
    - **Running**: `● 번역 중 (RUNNING)` (static calm green dot, no distracting pulse) + `[ ⏸ 번역 일시정지 (Pause) ]` (amber) + `[ ■ 서비스 종료 (Stop) ]` (active red).
    - **Paused**: `⏸ 일시정지 MM:SS` (gentle slow amber pulse glow + live pause duration timer) + `Resume 필요` (reminder subtitle, with `prefers-reduced-motion` accessibility support) + `[ ▶ 번역 다시 시작 (Resume) ]` (green) + `[ ■ 서비스 종료 (Stop) ]` (active red).
    - **Browser Tab Title Notification**: While paused, browser tab title displays `⏸ [MM:SS] 일시정지 — ...` for off-tab operator awareness.
    - **Failed**: `⚠ 번역 연결 오류 (Failed)` + `[ ▶ 다시 시도 (Retry) ]` + `[ ■ 서비스 종료 (Stop) ]`.
  - Fixed button positions eliminate operator confusion and allow reliable muscle-memory operation.
- **API Endpoints in `app/server.py`**: Added `/api/models`, `/api/models/select`, `/api/models/test`, `/api/models/dismiss-alert`, and `/api/config/auto-drift-correction`.
- **Comprehensive Unit & Integration Test Suite**: 44 automated tests passing across model resolution, configuration persistence, frame dropping on pause, rolling drift scoring on completed turns, session epoch isolation, non-retryable fatal error classification, runtime drift toggling, and operator UI accessibility (`tests/test_model_resolver.py`, `tests/test_setup_config.py`, `tests/test_anti_contamination.py`, `tests/test_operator_ui.py`).

---

## [2.2.0] - 2026-08-23

### Added
- **Two-Executable Distribution Architecture**: Introduced a clean separation between daily Sunday volunteer operation (`SKC_translation.exe`) and initial/occasional church configuration (`SKC_setup.exe`), eliminating any Python or batch script dependency for recipient churches.
- **Standalone Setup Wizard (`SKC_setup.exe`)**: Windowed desktop GUI built with Tkinter:
  - **Church Identity**: Configures Church Name, Short Name, Local URL (`http://<hostname>.local`), and copies/normalizes custom church logos to `branding/church-logo.*`.
  - **Google Gemini Onboarding**: Step-by-step guidance for Google AI Studio API key creation (Auth key migration notice) and billing setup ($10 minimum prepaid note).
  - **Decoupled Key & Model Validation**: Threaded non-blocking validation testing API key authentication and confirming configured model availability (`gemini-3.5-live-translate-preview`).
  - **Defensive & Atomic Persistence**: Atomic updates for `config.yaml` and `.env` using temporary files; preserves unrelated `.env` variables and comments; sanitizes all logs to never leak raw API keys.
  - **Existing Key Security**: Masks configured keys (`AIzaSy••••••••4xQ9`) with dedicated `[ Test Existing Key ]` and `[ Replace Key ]` workflows.
- **Main Application Fallback**: `main.py` / `SKC_translation.exe` detects missing API keys at startup and automatically offers to launch `SKC_setup.exe`.
- **Dynamic Church Identity in Web UI**: Operator console, attendee page, and QR code dynamically reflect the configured church name, short name, and custom branding logo.

---

## [2.1.3] - 2026-08-23

### Fixed
- **QR code camera recognition at close distance**: Reverted the outer decorative rounded navy bounding box frame from `_build_qr()` in `app/server.py` and restored `border=2`. The outer frame was disrupting quiet-zone edge detection on smartphone cameras when scanning at close range.

---

## [2.1.2] - 2026-08-17

### Fixed
- **Attendee voice playback on mobile browsers**: Added `AudioContext` constructor fallback for mobile browsers (iOS Safari in particular) that reject custom sample rates, added silent buffer unlocking for iOS audio sessions, and added automatic context resume on touch/click.
- **WebSocket audio stream keepalive**: Updated `/audio-stream` WebSocket handler in `server.py` to send empty keepalive frames during silence periods instead of terminating on 30s timeout.
- **Subscriber queue overflow handling**: Updated `broadcast.py` to drop oldest audio chunks instead of dropping client queues on network jitter.

### Changed
- **Operator and attendee URL display updated**: startup banner now shows the attendee URL and operator console URL using the configured hostname/port, with a fallback IP line that respects the active port.
- **Automatic browser launch adjusted**: the launcher now opens the operator console at the local localhost admin URL instead of the mDNS-based address.
- **QR card labels clarified**: the QR-code links under the operator page now display as “Operator (this page)” and “Attendee”.

---

## [2.1.0] - 2026-07-26

### Added
- **Korean source reveal on attendee page**: tap/click any committed English paragraph to toggle the original Korean text beneath it, displayed in a smaller muted font with a gold left border. Korean text is now sent as a `ko` field in the SSE `commit` payload from the backend (`broadcast.py` + `server.py`), replacing the previous unreliable client-side delta accumulation.
- **Operator console sticky header**: header (`<header>`) is now `position: sticky; top: 0` so the title bar and status strip scroll-lock together while reviewing long caption previews.
- **Responsive title**: header shows "Starkville Korean Church (PCA)" at normal widths; switches to "SKC (PCA)" below 760px when the status strip would start wrapping.
- **Tooltips on all operator console elements**: every card heading, stat label, control button (Start, Pause, Stop, Exit System), and right-panel card now has a bilingual Korean/English hover tooltip explaining its function.

### Changed
- **Status strip merged into header**: the 5 status pill indicators (Audio, Gemini, Internet, Translation, Web Server) moved from a separate row below the header into the header row itself, centered between logo and right edge. Saves one full row of vertical space.
- **Operator console header simplified**: removed "Live Translation Console" label and the `#hdr-badge` (Stopped/Live/Paused/Error) badge — the status strip indicators convey state sufficiently.
- **사용 가이드 link**: moved from header to a dedicated card in the right panel, styled consistently with other cards.
- **상태 모니터 grid compacted to 6 columns**: numeric stats (지연, 접속자, 재연결, 자막 수, 시간, 비용) now pack into 2 rows of 3 pairs instead of 3 rows of 2 pairs. Full-width rows (오디오 입력, Gemini 세션, 모델) span all 6 columns.
- **Operator preview duplicate text fix**: `commit` handler in operator preview now sets `enEl.textContent = msg.text` before finalizing, preventing repeated phrases after force-commits at the 150-char boundary.
- **Attendee page — timestamps removed**: committed caption lines no longer show `[MM:SS]` timestamps.
- **Attendee page — paragraph flow**: consecutive caption commits append to the same `<div>` until a sentence-ending punctuation (`.` `!` `?`) is reached, then start a new paragraph — reducing visual fragmentation.
- **Attendee page — font size range**: reduced from 20–56px to 20–40px; default changed from 28px to 20px. Live `Font XXpx` label added next to the slider.
- **Attendee page — "Tap sentence for Korean" hint**: added as a small label in the control bar between the font slider and the Audio button.
- **GoAway log level demoted**: `SESSION_FAILURE` log entry in `gemini_session.py` now logs at `INFO` (not `ERROR`) when the exception is a GoAway, eliminating misleading error noise in `ops.log`.

---

## [2.0.0] - 2026-07-19

### Changed
- **UI Architecture Refactoring to External Templates**:
  - Extracted `_ATTENDEE_HTML` from `app/server.py` into `app/templates/attendee.html`.
  - Extracted `_OPERATOR_HTML` from `app/server.py` into `app/templates/operator.html`.
  - Replaced inline route responses in `app/server.py` with a dynamic template reader (`_read_template`) that uses a file-reading mechanism.
  - Implemented dynamic hot-reloading for template files in development and caching in production (`sys.frozen`), optimizing AI token overhead and development efficiency.
- **PyInstaller Specification Update**:
  - Updated `SKC_translation.spec` to bundle the `app/templates` folder in PyInstaller's `datas` list, ensuring templates are correctly packaged inside the single-file executable.
- **UTF-8 Console Output Support**:
  - Configured `sys.stdout` and `sys.stderr` to enforce UTF-8 encoding in `main.py` when running on Windows to prevent `UnicodeEncodeError` when rendering unicode box-drawing console layouts.

## [1.9.0] - 2026-07-19

### Added
- **mDNS LAN Hostname Advertising via Zeroconf**:
  - Dynamically registers the `network.hostname` (defaults to `skc.live`) on service startup using `python-zeroconf` and unregisters on teardown.
  - Automatically maps to the host's current DHCP-assigned IP address on start, ensuring stable mDNS discovery across both home and church networks.
- **Dual-URL Display on Operator Console**:
  - Embedded dynamic URL information card under the QR code on the operator console displaying both the primary hostname URL (`http://skc.live:8080/live`) and the fallback raw IP URL.
  - Updated status poll endpoint `/api/status` to return both `live_url_primary` and `live_url_fallback` so the console updates in real time.
- **Dependencies**:
  - Added `zeroconf>=0.150.0` to `requirements.txt` to enable mDNS LAN service advertising.

## [1.8.0] - 2026-07-19

### Added
- **Auto-restart recovery loop for session failures:**
  - Bounded auto-restart sequence (3 attempts with backoffs 2s, 5s, 15s) in `server.py` replacing the terminal session teardown.
  - Front-end operator notifications: flashing status card red and playing a synthesized Web Audio beep chime on the console during auto-recovery attempts.
- **Detailed session failure diagnostics:**
  - Explicit logging of specific exception types, WebSocket close codes, and error messages (`type`, `close_code`, `message`) in `GeminiSession._run_session`.
  - Incremental reconnect attempt logging with resumption handle status (`resumption_handle_present` and its raw value).
- **Session resumption monitoring:**
  - Structured warnings on operator event log (`warning` event) when reconnection falls back to a cold-start (`resume=False`), signifying context loss.
  - Corrected `self._attempt` count to reset to `0` upon every successful connection, preventing GoAway disconnects from exhausting the retry budget and crashing the session after 3 GoAways (approx. 27-30 minutes).

### Changed
- **Immediate GoAway reconnect optimization (Lever 1)**: Special-cased `GoAway` exceptions in `GeminiSession._run_with_retry` to execute an immediate 0.2s reconnect without exponential backoff, reducing the reconnect window from ~2.4s to ~0.5s.
- **Unambiguous safety net logs:**
  - Distinct log messages distinguishing mic silence auto-stop (`Service automatically stopped: no audio signal for {N} min` / `AUTO_STOP_TIMER fired`) from API session failures (`Service automatically stopped: session failure ({close_code})`).

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
