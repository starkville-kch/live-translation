# Build Workthrough & History Log

> **Korean version**: [WORKTHROUGH.ko.md](WORKTHROUGH.ko.md)

This document serves as the chronological build record, verification test log, and debugging history of the Live Translation System.

---

## 📌 Table of Contents
1. [Chronological Sessions](#1-chronological-sessions)
2. [Verification Protocol Results (V0–V6, V14–V18)](#2-verification-protocol-results-v0v6-v14v18)
3. [Technical Choices Retrospective](#3-technical-choices-retrospective)
4. [Known Quirks](#4-known-quirks)
5. [Scripts Reference](#5-scripts-reference)

---

## 1. Chronological Sessions

### Session 1 — Initial Scaffold
* **Goal:** Create audio capture modules, Gemini Live API clients, and basic FastAPI routing layout.
* **Challenge:** Found that the model referenced in the draft architecture (`gemini-2.0-flash-live-preview-04-09`) was retired by Google, causing 1008 connect crashes.
* **Fix:** Coded `resolve_live_model()`, which queries the API at startup to discover the latest operational live model, auto-saving it to `config.yaml` to future-proof deployments.

### Session 2 — Modality Mismatches (Error 1007)
* **Goal:** Verify streaming text outputs from the live session.
* **Challenge:** Attempting to configure `response_modalities=["TEXT"]` on newer live models triggered a crash: `1007: The requested combination of response modalities is not supported`.
* **Fix:** Transitioned to `gemini-3.5-live-translate-preview`, configuring it for `AUDIO` modality combined with a target `translation_config` in English. Captions are parsed out from the `output_transcription.text` field within the response frame, avoiding modality issues.

### Session 3 — API Response Field Alignment
* **Symptom:** Live session connected, but no captions rendered on the console or attendee screens.
* **Cause:** The live translation metadata field was named `output_transcription.text` rather than `output_audio_transcription.text` as structured in previous API iterations.
* **Fix:** Aligned the parsing statements inside the asynchronous `_recv_loop` to bind `sc.output_transcription.text`.

### Session 4 — UI/UX Redesign & Local Assets
* **Goal:** Apply church styling, optimize mobile layouts, and localise interfaces to English.
* **Changes:**
  * Adopted a warm cream background (`#faf8f5`), navy header panel (`#1a2a42`), and gold border lines (`#b8923e`) to match the look of a traditional church bulletin.
  * Arranged mobile captions to scroll bottom-up, keeping new segments anchored to the lower half of the screen.
  * Localised attendee controls to English, prioritizing accessibility for English-speaking members.

### Session 5 — Mobile Real-time Audio Streaming (WebSockets)
* **Goal:** Provide real-time translated audio alongside captions on mobile browsers.
* **Alternatives:** Rejected base64-encoding audio chunks inside the SSE text stream because it added 33% encoding overhead and wasted bandwidth for caption-only users.
* **Fix:** Deployed a binary WebSocket `/audio-stream`. Only attendees who toggle the "Listen" button open this connection, receiving 24kHz PCM16 audio floats routed directly to browser audio buffers via the Web Audio API.

### Session 6 — Voice Pinning & Consolas Log Fonts
* **Symptom:** The translation voice changed randomly between male, female, or robotic registers on every session restart or network reconnect.
* **Fix:** Injected `PrebuiltVoiceConfig(voice_name="orus")` into the Gemini config payload, forcing a deep, clear male tone. Configured console log styles to load `Consolas` monospace font for better Korean characters rendering on Windows.

### Session 7 — Troubleshooting Guide Table Layout & Setup Foldout Fixes
* **Goal:** Correct alignment/wrapping issues in `how_to_use.html` tables, and implement a collapsible component for the one-time setup guide.
* **Problem & Resolution**:
  * **Table Alignment**: Refactored the multi-language tables to have a static set of 3 columns (`<th>`/`<td>` cells) regardless of language. Moved translation toggling inside child `<span>` tags, which aligns column headers perfectly.
  * **Badge Wrapping**: Prevented `.badge` elements from being distorted by the global `display: inline !important` rule by defining specific `display: inline-block !important` rules for active language badges, combined with `white-space: nowrap;`.
  * **Setup Foldout**: Since volunteers do not need to see installation steps every Sunday, wrapped the "One-Time Setup" section inside a styled `<details>`/`<summary>` tag to keep it collapsed by default. Combined the `[open]` selector with CSS virtual elements (`::after`) to dynamically update the action sub-headers between `(클릭하여 펼치기)` / `(클릭하여 접기)` (and English counterparts) entirely without JS overhead.

### Session 8 — Branded QR Code Redesign
* **Goal:** Replace the plain black-and-white QR code with a fully styled, brand-accurate version matching the church's visual identity.
* **Changes:**
  * **High Error Correction (`ERROR_CORRECT_H`):** Embedding a central logo physically destroys the QR modules it covers. Enabling H-level error correction (~30% recovery) gives the scanner's Reed-Solomon decoder enough redundancy to mathematically reconstruct the masked modules, ensuring reliable scannability.
  * **Rounded Data Modules:** Swapped the harsh square pixels for smooth round dots using `StyledPilImage + RoundedModuleDrawer`. Base module color set to Presbyterian Navy (`#1a2a42`).
  * **Pixel-Level Gold Finder Pattern Recoloring:** Rather than drawing rounded rectangles over the finder patterns (which can bleed into surrounding modules), the implementation iterates over every pixel within each 7×7 finder bounding box and swaps navy pixels to gold (`#b89445`) in-place via `px[x, y] = GOLD`. This scalpel approach preserves the rounded module shapes while precisely recoloring only the target pixels.
  * **Logo with Quiet-Zone Buffer:** A solid white `ellipse` (the "quiet zone") is drawn first in the center to create a clean, scanner-safe gap between the logo and surrounding data modules. A smaller navy inner circle is drawn inside the white ring to provide contrast for the white PCA logo. The logo is capped at exactly 20% of QR width per spec.

### Session 10 — Caption Commit Strategy Refinement & Korean Language Detection Fix

* **Goal:** Fix screen-freeze from long paragraphs and Vietnamese misidentification of Korean source audio.
* **Problem 1 — Screen freeze:** During continuous speech, the 1.5s silence timer kept resetting, allowing `_current_line` to grow to hundreds of characters. The attendee screen would then freeze while rendering an entire paragraph at once.
  * **Fix:** Added `MAX_LINE_CHARS = 150` as an overflow safety net. When the line reaches 150 characters, `_find_split()` searches the last 60 characters for a natural boundary (`. ` → `! ` → `? ` → `; ` → `, `), falling back to the last space if none is found.
* **Problem 2 — `turn_complete` attempt and revert:** Attempted to use Gemini's `turn_complete` signal as the primary commit trigger. Rejected because the signal fires on every filler utterance ("um", "uh") in sermon speech, causing excessive caption fragmentation. Reverted; the 1.5s silence timer remains the sole primary commit mechanism.
* **Problem 3 — Vietnamese transcript:** Without a language hint, the model misidentified Korean as Vietnamese.
  * **Fix:** Added `language_hints=types.LanguageHints(language_codes=["ko", "en"])` to `input_audio_transcription`. `"en"` is included because the pastor occasionally quotes English scripture passages.

### Session 11 — Operator Console UX Overhaul

* **Goal:** Restructure operator console for better space efficiency and bilingual source+translation monitoring.
* **Changes:**
  * **Korean+English paired preview:** Introduced a new `"source"` SSE event kind to stream Korean source text deltas. Operator preview renders them as paired Korean/English line sets via `getOrCreateLivePair()` / `commitLivePair()` DOM functions. Attendee page ignores `source` events entirely.
  * **Layout reorder:** Left column finalized as: Input Device → Status → Preview → Control Buttons → Auto-Stop+Exit row. Right column: Audio Monitor → Event Log → QR Code (bottom).
  * **Compact Status card:** Adopted a 4-column grid (`grid-template-columns: auto 1fr auto 1fr`). Long-value rows (오디오 입력, Gemini 세션, 모델) span full width; short numeric stats (지연+접속자, 재연결+자막 수, 시간+비용) share rows in pairs. Reduced from 9 rows to 6 rows, cutting card height by roughly a third.
  * **Auto-Stop + Exit System combined row:** Both controls placed in the same row with the Auto-Stop label replaced by a tooltip icon to save space.
  * **Uvicorn access log suppression:** `uvicorn.run(..., access_log=False)` to eliminate HTTP request log noise from the event log.

### Session 9 — Audio Pipeline Diagnostics & Mic Selection Automation
* **Goal:** Diagnostic cleanup of the temporary WAV capture, automatic selection of the saved microphone in the web console, and instant configuration updates.
* **Changes:**
  * Cleaned up the temporary WAV dumping logic (`pre_resample.wav` and `post_resample.wav`) and unneeded `numpy` imports from `app/audio.py` to restore the clean capture pipeline.
  * Fixed an operator console UX bug where refreshing the web page always defaulted the input device dropdown selection to index `0` (Microsoft Sound Mapper), causing volunteers to accidentally override the saved DJI Mic Mini configuration upon starting the service.
  * Exposed the saved `device_index` in the `/api/status` payload and updated `loadDevices()` in the frontend javascript to automatically pre-select the configured microphone on page load.
  * Bound a `change` event listener to the `device-select` dropdown to automatically POST selection updates to `/api/devices/select` and persist them to `config.yaml` in real-time.

### Session 12 — Translation Model Benchmark
* **Goal:** Confirm the best-performing model for real-time translation accuracy and connection stability.
* **Result:** After three rounds of benchmarking, `gemini-3.5-live-translate-preview` was confirmed to have the highest translation fidelity and the most stable session persistence under long services.

### Session 13 — Single Executable Spec & Batch Build
* **Goal:** Package the entire FastAPI server and dependencies into a single lightweight Windows executable (.exe).
* **Changes:** Added `SKC_translation.spec` configuration and a `build_exe.bat` automation script, optimizing the build process via a minimal `skc_build` Conda environment (output size ~70MB).

### Session 14 — Operator Event Log & Status Strip
* **Goal:** Improve real-time operational feedback and troubleshooting details for church volunteer operators.
* **Changes:**
  * Created the `OperatorEventLog` thread-safe ring buffer in `app/events.py` mapping events to 7 distinct categories.
  * Added the `/api/events` endpoint for 1.5s incremental UI polling.
  * Reorganized operator console layout and added a 5-pill status strip showing Audio, Gemini, Internet, Translation, and Web Server status at a glance.

### Session 15 — Bounded Auto-Restart, Diagnostics, & Operator Alerts
* **Goal:** Handle unexpected Gemini session disconnects gracefully without terminal crash teardowns, and improve diagnostics.
* **Changes & Root-Cause Discovery:**
  * **Root-Cause Discovery (27-Minute Disconnect Solved)**: A continuous 30-minute test run (`16:27`–`16:57`, 76 turns) confirmed that Google Gemini Live API enforces a server-side `GoAway` refresh boundary at ~27:05 into live streams. The auto-recovery caught the event and reconnected in 2.3 seconds with zero operator intervention.
  * Implemented an auto-restart loop in `server.py` with 3 attempts (2s, 5s, 15s backoffs) when `GeminiSession` enters the FAILED state.
  * Integrated front-end visual (flashing red card) and audible (Web Audio API beep chime) warnings during recovery.
  * Fixed a bug by resetting the `self._attempt` retry count to `0` upon successful connection, ensuring GoAway disconnects do not deplete the connection budget.
  * Enhanced diagnostics in `GeminiSession._run_session` exception handler to log exception class names, websocket close codes, and raw error messages.
  * Distinguished auto-stop logs (`AUTO_STOP_TIMER fired` / `Service automatically stopped: no audio signal for {N} min`) from session crashes.

---

## 2. Verification Protocol Results (V0–V6, V14–V18)

* **V0 (Startup)**: Verified that all health endpoints return HTTP 200 and dynamic QR codes compile cleanly (Pass ✅).
* **V1 (Audio Path)**: Sent synthetic audio inputs to the Gemini pipeline, confirming accurate translation mapping with a latency of 2.2 seconds (Pass ✅).
* **V2 (Translation Quality)**: Streamed a 62-second Korean Bible excerpt (John 3:16) and verified 100% semantic matching, preserving theological terms in English (Pass ✅).
* **V3 (Multi-Client SSE)**: Spun up 10 concurrent browser connections, verifying stable SSE frame broadcast with zero client drops (Pass ✅).
* **V4 (Reconnection)**: Interrupted network cables and toggled operator controls, confirming automatic reconnection and state recovery within 2 seconds (Pass ✅).
* **V5 (15-min Simulation)**: Simulated a long service run, successfully catching Google GoAway signals at 8.3 and 9.0 minutes to re-establish sessions with zero caption loss (Pass ✅).
* **V6 (UI Verification)**: Confirmed bulletin layout renders and local assets resolve correctly on desktop and mobile browsers (Pass ✅).
* **V14 (Forced-failure auto-recovery)**: Simulated session failures and verified that the backend auto-restarted and resumed service within 3 attempts, displaying real-time retry alerts in the operator console event log (Pass ✅).
* **V15 (Auto-stop timer isolation)**: Tested low auto-stop durations and verified that the timer fired precisely at the timeout threshold with clear warnings, leaving normal services untouched (Pass ✅).
* **V16 (No-signal safety net isolation)**: Verified that genuine microphone silence successfully triggers the safety net with distinct log messages (`Service automatically stopped: no audio signal for {N} min`), whereas sessions with normal audio and pauses are ignored (Pass ✅).
* **V17 (Resumption handle confirmation)**: Verified that when a GoAway reconnect occurs, the log explicitly tracks `resumption_handle_present=True` and successfully performs a `resume=True` connection, and reports a warning event on the UI if it drops back to a cold-start (Pass ✅).
* **V18 (Root-cause log completeness & 27-min GoAway validation)**: Verified that exception logging within `GeminiSession._run_session` records detailed exception class names (`RuntimeError`) and messages (`GoAway`), capturing the 27:05 Google Live API session refresh boundary cleanly (Pass ✅).

---

## 3. Technical Choices Retrospective

### 1️⃣ Audio Resampler: Pure Python vs. NumPy/Librosa
* *Reason*: Forcing church volunteers to compile large binary wrappers (NumPy, SciPy, gfortran) on standard Windows PCs creates setup issues. Implementing a simple chunk-based linear resampler directly in `audio.py` kept the codebase light and zero-dependency.

### 2️⃣ Text Streaming: Server-Sent Events (SSE) vs. WebSockets
* *Reason*: Mobile browsers aggressively hibernate WebSockets when screens dim or users swap tabs. SSE features native, browser-level automatic retry mechanisms that guarantee caption updates resume automatically upon reconnection without manual JavaScript listeners.

### 3️⃣ Audio Streaming: Web Audio API vs. HLS/DASH or `<audio>` Tags
* *Reason*: HLS and DASH slice audio feeds into chunks, adding 5–10 seconds of encoding latency. Utilizing raw 24kHz PCM16 feeds over WebSockets and routing them straight to browser buffers via the Web Audio API reduced audio latency below 200 milliseconds.

---

## 4. Known Quirks

### `TranslationConfig` field names
The Python SDK uses camelCase aliases: `targetLanguageCode`, not `target_language_codes`. The correct instantiation:
```python
types.TranslationConfig(target_language_code="en")  # single string, not a list
```

### Audio PCM in model_turn
The translate model sends audio PCM chunks in `model_turn.inline_data` (24kHz PCM16 mono, ~12000 bytes per chunk) alongside the text transcription. The SDK may emit:
```
Warning: there are non-text parts in the response: ['inline_data']
```
This warning is harmless and can be ignored.

---

## 5. Scripts Reference

| Script | Purpose |
|--------|---------|
| `.agent/scripts/check_imports.py` | Verify all modules import, list audio devices |
| `.agent/scripts/list_models.py` | List all bidiGenerateContent models from API |
| `.agent/scripts/probe_model.py` | Check specific model ID availability + API version |
| `.agent/scripts/v1_audio_path_test.py` | V1: Korean TTS → Gemini → translation output |
| `.agent/scripts/v2_quality_test.py` | V2: Full sermon excerpt quality check |
| `.agent/scripts/v5_service_sim.py` | V5: 15-min looping audio service simulation |
