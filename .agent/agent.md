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
- **Session retry attempt reset**: The reconnect attempt count (`self._attempt`) in `GeminiSession` is reset to 0 on every successful connection. Without this, GoAway reconnects (every ~10m) accumulate and crash the session after 30 mins.
- **Pipeline auto-restart loop**: If the session fails completely, `server.py` runs a bounded recovery loop (3 attempts with backoffs 2s, 5s, 15s) flashing status card red and chiming to warn operators before stopping.
- **Root-cause of 27-minute session disconnect**: A 30-minute continuous run (`16:27`–`16:57`, 76 turns) confirmed Google Gemini Live API enforces a server-side `GoAway` boundary at ~27:05. The auto-recovery reconnected in 2.3s seamlessly with zero manual intervention required.
- **mDNS LAN Hostname via Zeroconf**: registered the network.hostname (default: `skc.live`) dynamically on startup using `python-zeroconf` and unregistered on shutdown. This resolves DHCP reassignment issues across networks (home vs church) without requiring static IP configuration or router reservations.
- **Dual-URL operator fallback**: provided both primary (configured hostname) and fallback (raw IP) live URLs beneath the QR code image on the operator console. If mDNS resolution fails due to network/multicast blocking, the operator can provide the raw IP address immediately.
- **External HTML UI Templates**: Extracted the previously embedded HTML strings from `app/server.py` into separate template files (`app/templates/attendee.html` and `app/templates/operator.html`). Created a dynamic loader (`_read_template`) that caches templates in production (`sys.frozen`) but re-reads them from disk in development, enabling real-time hot-reloading for UI edits without restarting the server. Added both templates to the PyInstaller `.spec` file datas list to ensure they package correctly in single-file executables.
- **SSE `commit` payload includes `ko` field**: `CaptionBroadcaster` now attaches `self._current_ko` as the `ko` field on every `CaptionEvent(kind="commit")`. `server.py` forwards it in the SSE JSON. The attendee page uses it directly for the Korean tap-reveal feature. This is not a 1:1 sentence match (Gemini streams KO and EN on independent timers) but is the closest alignment achievable without API-side alignment data.
- **Operator preview duplicate text bug**: on force-commits (150-char boundary), the operator's `enEl` held the full `_current_line` including the remainder that would start the next live pair. Fix: set `enEl.textContent = msg.text` inside the `commit` handler before calling `commitLivePair()`. The attendee page was immune because it reads `msg.text` directly.
- **GoAway is INFO not ERROR**: `SESSION_FAILURE` in `gemini_session.py._run_session` is logged at `ERROR` for real failures but demoted to `INFO` when `"GoAway"` is in the exception message. GoAway is a normal server-side session refresh, not an error. Do not revert this to blanket `ERROR`.
- **Attendee paragraph grouping**: commits append to the current `<div>` until the last committed text ends with `.`, `!`, or `?`, then a new paragraph begins. This prevents visual fragmentation from the 1.5s silence-based commit cadence. The `data-last` attribute on each paragraph tracks the last appended text for the punctuation check.
- **Two-Executable Distribution (`SKC_translation.exe` + `SKC_setup.exe`)**: Normal Sunday volunteer operation is completely isolated in `SKC_translation.exe` (console logs + browser console), while initial and occasional configuration (church identity, hostname, Google AI Studio key/billing setup, and connection test) is handled via `SKC_setup.exe` (windowed Tkinter wizard). Eliminates Python and batch script runtime dependencies for recipient churches.
- **Defensive & Atomic Config Persistence**: `update_gemini_api_key` modifies only `GEMINI_API_KEY` in `.env` while preserving all other environment variables and comments. All YAML and `.env` writes use atomic temporary file replacement (`os.replace`) to prevent corruption. Raw API keys are never exposed in log outputs, error strings, or visible entry fields.
- **Decoupled API Key & Model Validation**: Setup wizard tests authentication with `google.genai.Client` first, then validates availability of the configured model in `config.yaml` (`gemini.model`) without hard-coded model assumptions.
- **Dynamic Church Identity**: Church name, short name, local URL (`http://<hostname>.local`), and custom logo in `branding/` are dynamically loaded from `config.yaml` by the backend, status API, operator console, attendee page, and QR code generator.
- **Gemini Live Translation Model Selection Engine (`app/model_resolver.py`)**:
  - Direct `preferred_model` configuration with automated two-tier fallback: `preferred_model` $\to$ `Last Known Good (LKG)` $\to$ `fallback_model`. Selecting another model from the operator dropdown immediately sets it as `preferred_model` for subsequent sessions.
  - Strict Live Translate filtering: filters out general Flash/Pro non-translation models (`gemini-2.5-flash`, `gemini-1.5-pro`) and conversational models (`gemini-3.1-flash-live-preview`); only models matching Live Translation capabilities are eligible candidates.
  - 5-Tier lifecycle: Discovered Candidate $\to$ Compatible (handshake passed) $\to$ Verified (real translated output delivered in active session) $\to$ Last Known Good (LKG) $\to$ Locked (fixed for duration of service).
  - LKG persistence isolation: `config.yaml` stores administrator intent only (`preferred_model`, `fallback_model`, `voice`); runtime learned state (`last_known_good_model`, `last_verified_at`, `seen_models`, `dismissed_alerts`) is stored separately in `var/runtime/model_state.json`.
- **Gemini Developer API language hints are unsupported**: Do not reintroduce `AudioTranscriptionConfig(language_codes=["ko", "en"])` for Live Translate. The Google GenAI SDK rejects this field in Developer API mode (`ValueError: language_codes parameter is only supported in Gemini Enterprise Agent Platform mode`); use empty `AudioTranscriptionConfig()`. Bilingual KO/EN handling uses automatic language detection plus clean Pause → Resume session resets. See `docs/STACK.md`.
- **Anti-Contamination & Clean Session Reset Architecture (`app/gemini_session.py`, `app/audio.py`, `app/server.py`, `app/broadcast.py`)**:
  - Why Stop → Start originally appeared to fix language drift: it destroyed the contaminated Gemini session context.
  - Pause → Resume forms a hard clean session boundary: Pause drops audio frames, closes Gemini WebSocket, discards session resumption tokens, drains server/client queues, and preserves model lock; Resume increments `_session_epoch`, drains residual queues, and starts a fresh Gemini Live session on the locked model with clean context.
  - Non-Retryable Error Handling: Distinguishes fatal configuration/schema errors (`ValueError`, `TypeError`) from transient network disconnects (GoAway/timeout), halting immediately without entering infinite restart cascades.
  - Session Epoch Isolation: All inbound server events and client states are tagged with `session_epoch`; responses from prior/stale sessions are dropped immediately to prevent async race conditions.
  - Completed-Turn Language Drift Scoring & Rolling Window: Primary signal is Gemini's `input_transcription.language_code` (`ko`/`en` $\to 0$; `ja`/`vi`/`zh`/`th` $\to +1$) and `output_transcription.language_code` (non-`en` $\to +2$) scored strictly on completed utterances using a rolling `deque(maxlen=3)` (2 consecutive clean turns reset score to 0).
- **Operator Console Control Bar Separation & Pause Reminders (`app/templates/operator.html`, `app/server.py`)**:
  - Separated non-clickable service status indicator from action buttons into a fixed 3-column layout (`Status Pill` | `Primary Action` | `Stop Action`):
    - **Stopped**: `○ 대기 중 (STOPPED)` + `[ ▶ 번역 시작 (Start) ]` (green) + `[ ■ 서비스 종료 (Stop) ]` (neutral disabled gray, never red when disabled).
    - **Starting / Resuming / Resetting**: `⟳ 번역 연결 중...` (spinner) + `[ ⏳ 연결 중… ]` + `[ ■ 서비스 종료 (Stop) ]`.
    - **Running**: `● 번역 중 (RUNNING)` (static calm green dot, no distracting pulse) + `[ ⏸ 번역 일시정지 (Pause) ]` (amber) + `[ ■ 서비스 종료 (Stop) ]` (active red).
    - **Paused**: `⏸ 일시정지 MM:SS` (gentle slow amber pulse glow + authoritative server pause timer) + `Resume 필요` (subtitle, escalating to `⚠ 번역 재시작을 확인하세요` in amber at > 3 min) + `[ ▶ 번역 다시 시작 (Resume) ]` (green) + `[ ■ 서비스 종료 (Stop) ]` (active red).
    - **Browser Tab Title Notification**: While paused, browser tab title displays `⏸ [MM:SS] 일시정지 — ...` for off-tab operator awareness.
    - **Accessibility Safeguards**: Added `role="status"` and `aria-live="polite"` to the status pill for screen readers, and `@media (prefers-reduced-motion: reduce)` to disable pulse animations for sensitive operators.
    - **Failed**: `⚠ 번역 연결 오류 (Failed)` + `[ ▶ 다시 시도 (Retry) ]` + `[ ■ 서비스 종료 (Stop) ]`.

---

## Document Index

| Document | Content | Read when |
|---|---|---|
| `docs/STACK.md` | 기술적 의사결정 및 영구 불변 원칙 (Language hints, Clean reset, LKG cascade) | 아키텍처 원칙 및 실패 경험 재발 방지 확인 시 |
| `app/events.py` | `OperatorEventLog` — thread-safe ring buffer, 7 categories, `since(last_id)` API | Understanding operator event plumbing |
| `app/model_resolver.py` | Model discovery, candidate classification, 5-tier lifecycle, fallback cascade | Understanding model selection and fallback |
| `app/gemini_session.py` | Gemini Live WebSocket session runner, anti-contamination boundaries, drift watchdog | Understanding Gemini Live integration |
| `docs/PLAN.md` | 시스템 개요, 파일 맵, 단계별 개발 현황(0–22), 신뢰성 요구사항, 설정 참조 | 아키텍처 및 시스템 사양 확인 시 |
| `docs/TECHNICAL.md` | 코드 레벨: FastAPI 라우트, Gemini 세션, 오디오 파이프라인, asyncio 패턴 | 코드 수정 및 디버깅 시 |
| `docs/WALKTHROUGH.md` | 세션별 빌드 기록, 검증 프로토콜(V0–V22) 결과, 기술적 회고 | 과거 기술 의사결정 및 이슈 추적 시 |
| `docs/BUILD_EXE.md` | PyInstaller 빌드 기록, spec 설정, 단일 실행 파일 패키징 | 독립 실행 파일 재빌드 시 |
| `CHANGELOG.md` | 릴리즈 버전 히스토리 (Version history) | 버전별 변경점 확인 시 |
| `tests/` | 모델 리졸버, 설정, 오염 방지, UI 접근성 등 44개 자동화 테스트 스위트 | 테스트 실행 및 코드 검증 시 |


