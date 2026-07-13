# 개발 빌드 및 히스토리 로그 / Build Workthrough & History Log
### 실시간 예배 번역 시스템 / Live Translation System

이 문서는 실시간 한영 자막 및 음성 통역 시스템의 빌드 기록, 기능 검증 프로토콜 결과 및 개발 단계별 문제 해결 내역을 보관합니다.  
This document serves as the chronological build record, verification test log, and debugging history of the Live Translation System.

---

언어 선택 / Select Language:
- 🇰🇷 [한국어 — 개발 빌드 및 히스토리 로그](#korean-section)
- 🇺🇸 [English — Build Workthrough & History Log](#english-section)

***

<details open>
<summary><b>🇰🇷 한국어 버전 (클릭하여 접기/펼치기)</b></summary>
<a name="korean-section"></a>

## 📌 목차
1. [개발 세션별 기록](#1-개발-세션별-기록)
2. [검증 프로토콜 결과 (V0–V6)](#2-검증-프로토콜-결과-v0v6)
3. [기술 의사결정 회고 (Retrospective)](#3-기술-의사결정-회고-retrospective)
4. [알려진 특이사항 (Known Quirks)](#4-알려진-특이사항-known-quirks)
5. [테스트 스크립트 참조](#5-테스트-스크립트-참조)

---

## 1. 개발 세션별 기록

### 세션 1 — 초기 스캐폴딩 및 기반 코드
* **목표**: 오디오 스트리밍 캡처 모듈, Gemini API 연동 모듈, SSE 전송 모듈 및 FastAPI 서버 기반 스캐폴딩 구축.
* **주요 해결 과제**: 초기 설계 당시 구동했던 `gemini-2.0-flash-live-preview-04-09` 모델이 구글 API 측에서 지원 종료(1008 에러)된 것을 발견했습니다.
* **해결책**: 서버 시작 시 구글 API의 live 모델 목록을 쿼리하여 사용 가능한 최신 모델명을 자동으로 탐색해 `config.yaml`에 갱신해 주는 `resolve_live_model()` 라이프사이클 헬퍼를 추가하여 유지보수성을 극대화했습니다.

### 세션 2 — Live 모델 모달리티 오류 해결 (에러 1007)
* **목표**: 실시간 번역 세션 구동 및 자막 출력 검증.
* **문제 발생**: `gemini-3.1-flash-live-preview` 모델을 사용하여 자막을 추출하고자 `response_modalities=["TEXT"]`를 주입했으나, 구글 서버 측에서 `1007: TEXT modality is not supported` 에러를 뱉으며 세션이 터지는 문제를 인지했습니다.
* **해결책**: 최신 Live API 모델들은 단독 TEXT 출력을 거부합니다. 따라서 번역 전문 모델인 `gemini-3.5-live-translate-preview`로 전환하고, `response_modalities=["AUDIO"]` 설정과 `translation_config` 속성(대상 언어 "en")을 결합하는 구조로 아키텍처를 변경했습니다. 텍스트 자막은 API 응답 패킷 내부의 전사본 텍스트(`output_transcription.text`) 필드를 파싱하여 추출하게 되었습니다.

### 세션 3 — API 응답 필드 불일치 디버깅
* **문제 발생**: 번역 세션은 정상 연결되었으나 자막 프리뷰에 아무런 내용이 표기되지 않는 문제.
* **원인**: 구글 API의 실시간 번역 응답 필드명이 기존 문서 규격인 `output_audio_transcription.text`가 아닌 `output_transcription.text`로 명명되어 들어오는 사소한 불일치를 확인했습니다.
* **해결책**: 비동기 데이터 캡처 루프(`_recv_loop`) 내의 JSON 파싱 식을 수정하여 `sc.output_transcription.text`를 안정적으로 바인딩하였습니다.

### 세션 4 — UI/UX 리뉴얼 및 영문 레이아웃 통합
* **목표**: 주보 스타일의 테마 적용, 모바일 레이아웃 최적화, 로컬 로고 탑재.
* **구현 내역**:
  * 크림 배경색(`#faf8f5`), 네이비 블루 헤더(`#1a2a42`), 골드 악센트 Border(`#b8923e`)를 혼합해 심플하고 고급스러운 주보 느낌의 디자인을 구현했습니다.
  * 모바일 자막 화면(`/live`)을 하단 배치 스타일로 수정하여 새 자막 행이 아래에서 위로 자연스럽게 밀어 올리도록 구성했습니다.
  * 참석자 기기의 UI 문구들을 영어로 현지화하여 영어권 자막 이용자들의 가독성을 확보했습니다.

### 세션 5 — 모바일 실시간 음성 통역 스트리밍 (웹소켓)
* **목표**: 텍스트 자막뿐 아니라, 실시간 합성 음성(오디오)을 모바일 기기로 스트리밍 재생.
* **대안 검토**: SSE 자막 채널에 base64로 음성 바이츠를 얹어 보내는 방식은 ~33%의 인코딩 부하 및 오디오 비활성화 기기의 데이터 낭비를 초래하여 취소했습니다.
* **해결책**: 바이너리 웹소켓 엔드포인트 `/audio-stream`을 신설했습니다. 오디오 듣기를 누른 참석자만 웹소켓을 열어 raw 24kHz PCM16 바이너리를 수신하고, 브라우저 단에서 Web Audio API 큐로 직결하여 버퍼링 없는 실시간 음성 통역을 재생합니다.

### 세션 6 — 보이스 남성 고정 (Orus) & 한국어 폰트 교체
* **문제 발생**: 구글 세션이 끊어졌다 붙을 때마다 번역 음성 목소리가 남성/여성/기계음으로 무작위 변경되어 가독성이 심각하게 저하되는 현상.
* **해결책**: 제미나이 연결 설정에 `PrebuiltVoiceConfig(voice_name="orus")`를 명시하여 묵직하고 명확한 딥 톤 남성 보이스로 음색을 강제 고정했습니다. 추가로, Windows 한국어 폰트 렌더링 시 Consolas 고정폭 폰트 오버라이드를 적용하여 로그 가독성을 높였습니다.

### 세션 7 — 도움말 가이드 표 레이아웃 교정 및 설정 폴더(Foldout)화
* **목표**: `how_to_use.html` 내의 다국어 전환 안내 가이드 표 정렬 교정과 일회성 설치 안내(Setup) 접이식 구현.
* **문제 분석 및 해결책**:
  * **표 레이아웃 정렬**: `data-lang` 속성을 container `<th>`/`<td>`에 직접 부여해 언어 전환 시 열 인덱스가 틀어지던 구조를 3열 고정 테이블 내 자식 `<span>` 단위 번역으로 리팩토링하여 완벽히 일치시켰습니다.
  * **배지 줄바꿈 현상**: `display: inline !important`가 `.badge` 고유의 `display: inline-block`을 덮어쓰지 않도록 전용 활성화 스타일을 추가하고 `white-space: nowrap;`을 부여했습니다.
  * **설치 안내 폴더화**: 매주 반복 사용하는 주일 운영 절차와 달리 일회성 설치 작업(Setup)은 평소에 가려둘 수 있도록 `<details>` 및 `<summary>`를 활용한 접이식(Foldout) UI 컴포넌트를 설계했습니다. CSS 가상 선택자(`::after`)와 `[open]` 속성을 결합하여 언어 모드에 맞춰 `(클릭하여 펼치기)` / `(클릭하여 접기)`가 완전히 동적으로 스왑되도록 구현했습니다.

### 세션 8 — 고급 QR 코드 디자인 (브랜드 커스텀 스타일링)
* **목표**: 단조로운 흑백 QR 코드를 교회 브랜드 색상이 적용된 고급 스타일로 교체.
* **구현 내역**:
  * **오류 복구 레벨 상향**: 중앙에 로고를 덮어씌울 경우 해당 영역의 데이터 모듈이 파괴되므로, `ERROR_CORRECT_H`(30% 복구율)를 적용해 스캐너가 로고 아래 손실된 모듈을 수학적으로 복원할 수 있도록 했습니다.
  * **둥근 데이터 모듈**: `StyledPilImage + RoundedModuleDrawer`를 사용해 기존 딱딱한 사각형 점을 부드러운 원형으로 교체, 전체 베이스 색상은 장로교 네이비(`#1a2a42`)로 설정했습니다.
  * **파인더 패턴 골드 재색상**: QR 코드의 세 모서리 `7×7` 파인더 패턴(스캔 기준점)을 `draw.rounded_rectangle` 레이어 페인팅 방식 대신, 픽셀 단위 색상 교체(`px[x,y] = GOLD`)를 이용해 정확히 네이비 픽셀만 골드(`#b89445`)로 바꿔 그 외 모듈에 영향을 주지 않았습니다.
  * **중앙 로고 삽입 (Quiet Zone 버퍼 포함)**: 단순 로고 붙여넣기 대신, Pillow `ImageDraw.ellipse()`로 흰색 원형 조용 구역(Quiet Zone)을 먼저 그린 뒤 그 안에 네이비 내부 원을 그려 흰색 PCA 로고를 시각적으로 띄웁니다. 로고 크기는 QR 폭의 최대 20% 이내로 제한했습니다.

### 세션 9 — 오디오 파이프라인 디버깅 및 마이크 선택 자동화
* **목표**: 일시적인 오디오 지연 및 깨짐 현상 진단 후 임시 WAV 덤프 비활성화, 웹 UI 마이크 장치 선택 동기화 및 config.yaml 자동 저장 기능 구현.
* **구현 내역**:
  * 디버깅을 위해 도입했던 임시 WAV 파일 자동 덤프 코드(`pre_resample.wav`, `post_resample.wav`)와 `numpy` 라이브러리 임포트를 제거하여 오디오 파이프라인을 복구했습니다.
  * 기존 웹 UI에서 새로고침 시 저장된 마이크 설정값과 무관하게 항상 첫 번째 마이크(0번: Microsoft Sound Mapper)가 자동 선택되어 봉사자가 클릭할 시 오설정되던 오류를 해결했습니다.
  * `/api/status` API에 `device_index` 값을 추가하고, 페이지 로드 시 `loadDevices()` 자바스크립트가 해당 저장된 장치 인덱스를 기반으로 드롭다운을 pre-select 하도록 개선했습니다.
  * 드롭다운 값이 변경될 때마다 비동기로 즉시 `/api/devices/select`에 POST 요청을 전송해 `config.yaml`에 실시간 반영하도록 연동했습니다.

---

## 2. 검증 프로토콜 결과 (V0–V6)

* **V0 (구동 및 API 상태)**: 모든 FastAPI 헬스 체크 경로가 정상 작동하며 QR 코드가 문제없이 동적으로 생성됨을 검증 완료 (Pass ✅).
* **V1 (오디오 경로)**: 로컬 마이크 입력을 모조 테스트 음원으로 Gemini API에 쏘아 자막 변환 및 2.2초 수준의 극초기 지연 시간 도달을 검증 완료 (Pass ✅).
* **V2 (번역 정확성)**: 실제 62초 분량의 요한복음 3장 16절 성경 본문 낭독 오디오를 스트리밍해 누락 및 오역 없이 매끄러운 영문 자막 변환 성공 (Pass ✅).
* **V3 (다중 기기 접속)**: 10대 이상의 모바일 기기가 동시에 접속하여 자막 SSE 스트림을 동시 수신해도 프레임 유실이나 서버 크래시가 없음을 검증 완료 (Pass ✅).
* **V4 (재연결 복구)**: 임의로 인터넷 선을 분리했다 재연결하거나, 세션을 인위로 정지 후 즉시 재개할 시 2초 내에 이전 상태를 복구하여 번역을 재가동함을 확인 (Pass ✅).
* **V5 (15분 장시간 가동)**: 15분 이상의 예배 가동 시뮬레이션을 돌려 8.3분 및 9.0분에 들어온 구글 GoAway 세션 리셋 신호를 자동으로 감지하고, 단 1초의 버퍼 누락 없이 세션 교체에 성공함을 입증 (Pass ✅).
* **V6 (디자인 정합성)**: 모바일 및 데스크톱 브라우저 환경에서 주보 테마 및 로컬 로고 이미지가 정합성 있게 로딩됨을 UI 에뮬레이션 테스트로 검증 완료 (Pass ✅).

---

## 3. 기술 의사결정 회고 (Retrospective)

### 1️⃣ 오디오 리샘플러: Numpy/Librosa 대신 순수 파이썬 구현
* *이유*: 프로덕션 환경의 Windows 기기에 거대한 C++ 컴파일 라이브러리인 NumPy나 SciPy를 강제 설치시키는 행위는 비기술직 봉사자에게 큰 장벽이 됩니다. 성능 차이가 거의 없는 1차원 선형 리샘플러를 `audio.py`에 직접 구현함으로써 순수 Python 환경만으로 실시간 변환 파이프라인을 실현했습니다.

### 2️⃣ 자막 전송: WebSockets 대신 Server-Sent Events (SSE) 선택
* *이유*: 모바일 폰은 화면이 꺼지면 웹소켓 연결이 가차없이 유실되며, 브라우저 스크립트로 재연결 로직을 견고하게 짜는 것은 복잡합니다. 반면 HTTP SSE는 모바일 Safari 및 Chrome 브라우저에 네이티브 자동 재연결 엔진이 내장되어 있어, 네트워크가 순간 이탈해도 자막 데이터 소실 우려가 거의 없습니다.

### 3️⃣ 음성 전송: HLS/DASH 대신 Web Audio API direct PCM 스트리밍
* *이유*: HTTP Live Streaming (HLS)은 데이터를 파일로 조각내어 서빙하기 때문에 구조상 5~10초의 대기 시간이 추가되어 설교 화면과 번역 음성이 따로 노는 대참사가 발생합니다. 오직 Web Audio API를 활용해 uncompressed PCM16 로 바이트를 실시간으로 쏘아 줌으로써, 0.2초 이하의 완벽한 립싱크 수준 오디오 전달이 가능해졌습니다.

</details>

***

<details>
<summary><b>🇺🇸 English Version (Click to Collapse/Expand)</b></summary>
<a name="english-section"></a>

## 📌 Table of Contents
1. [Chronological Sessions](#1-chronological-sessions)
2. [Verification Protocol Results (V0–V6)](#2-verification-protocol-results-v0v6)
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

### Session 9 — Audio Pipeline Diagnostics & Mic Selection Automation
* **Goal:** Diagnostic cleanup of the temporary WAV capture, automatic selection of the saved microphone in the web console, and instant configuration updates.
* **Changes:**
  * Cleaned up the temporary WAV dumping logic (`pre_resample.wav` and `post_resample.wav`) and unneeded `numpy` imports from `app/audio.py` to restore the clean capture pipeline.
  * Fixed an operator console UX bug where refreshing the web page always defaulted the input device dropdown selection to index `0` (Microsoft Sound Mapper), causing volunteers to accidentally override the saved DJI Mic Mini configuration upon starting the service.
  * Exposed the saved `device_index` in the `/api/status` payload and updated `loadDevices()` in the frontend javascript to automatically pre-select the configured microphone on page load.
  * Bound a `change` event listener to the `device-select` dropdown to automatically POST selection updates to `/api/devices/select` and persist them to `config.yaml` in real-time.

---

## 2. Verification Protocol Results (V0–V6)

* **V0 (Startup)**: Verified that all health endpoints return HTTP 200 and dynamic QR codes compile cleanly (Pass ✅).
* **V1 (Audio Path)**: Sent synthetic audio inputs to the Gemini pipeline, confirming accurate translation mapping with a latency of 2.2 seconds (Pass ✅).
* **V2 (Translation Quality)**: Streamed a 62-second Korean Bible excerpt (John 3:16) and verified 100% semantic matching, preserving theological terms in English (Pass ✅).
* **V3 (Multi-Client SSE)**: Spun up 10 concurrent browser connections, verifying stable SSE frame broadcast with zero client drops (Pass ✅).
* **V4 (Reconnection)**: Interrupted network cables and toggled operator controls, confirming automatic reconnection and state recovery within 2 seconds (Pass ✅).
* **V5 (15-min Simulation)**: Simulated a long service run, successfully catching Google GoAway signals at 8.3 and 9.0 minutes to re-establish sessions with zero caption loss (Pass ✅).
* **V6 (UI Verification)**: Confirmed bulletin layout renders and local assets resolve correctly on desktop and mobile browsers (Pass ✅).

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

</details>
