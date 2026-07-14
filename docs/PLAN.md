# 기술 유지보수 및 아키텍처 플랜 / Technical Maintainer & Architecture Plan
### 실시간 예배 번역 시스템 / Live Translation System

이 문서는 실시간 한영 자막 및 음성 통역 시스템의 개발자, 시스템 관리자 및 기술 봉사자를 위한 시스템 설계 및 아키텍처 정보를 제공합니다.  
This document serves as the Technical Maintainer & Architecture Plan for the Live Translation System, providing developer and system specifications for maintenance and future development.

---

언어 선택 / Select Language:
- 🇰🇷 [한국어 — 기술 유지보수 및 아키텍처 플랜](#korean-section)
- 🇺🇸 [English — Technical Maintainer & Architecture Plan](#english-section)

***

<details open>
<summary><b>🇰🇷 한국어 버전 (클릭하여 접기/펼치기)</b></summary>
<a name="korean-section"></a>

## 📌 목차
1. [시스템 다이어그램](#1-시스템-다이어그램)
2. [핵심 설계 의사결정](#2-핵심-설계-의사결정)
3. [기술 스택 선정 및 대안 비교](#3-기술-스택-선정-및-대안-비교)
4. [파일 구조 (File Map)](#4-파일-구조-file-map)
5. [신뢰성 요구사항](#5-신뢰성-요구사항)
6. [단계별 개발 현황](#6-단계별-개발-현황)
7. [설정 가이드 (Configuration)](#7-설정-가이드-configuration)
8. [향후 확장 계획 (Future Phases)](#8-향후-확장-계획-future-phases)

---

## 1. 시스템 다이어그램

```
USB 믹서
    │ (오디오 케이블)
    ▼
Windows PC (본 애플리케이션)
    │
    ├─ app/audio.py ──────────── 16kHz 모노 PCM16 데이터 캡처
    │                            오디오 장치 기본 주파수에서 리샘플링
    │                            RMS 데시벨 레벨 미터링 (10Hz 주기)
    │                            신호 차단 및 장치 연결 해제 자동 감지
    │
    ├─ app/gemini_session.py ─── Gemini Live API로 실시간 오디오 스트리밍
    │                            한국어 소스 전사 텍스트 수신 (로그 전용)
    │                            영어 번역 텍스트 수신
    │                            세션 자동 복구(Resumption) 및 컨텍스트 압축
    │                            GoAway 서버 종료 신호 감지 시 지수 백오프 재연결
    │
    ├─ app/broadcast.py ─────── 메모리 내 SSE 자막 전송 + 오디오 웹소켓 큐 관리
    │                            실시간 커밋 자막 처리 (1.5초 대기 임계값)
    │                            오디오 출력용 WebSocket 클라이언트 팬아웃
    │
    └─ app/server.py ─────────── FastAPI 웹 서버
          │
          ├─ GET  /                  관리자 콘솔 (입력 설정, 시작/종료, QR 코드, 프리뷰)
          ├─ GET  /live              참석자 페이지 (자막 창, 글꼴 크기 제어, 영문 UI)
          ├─ GET  /stream            SSE 자막 스트림 (참석자 모바일 기기용)
          ├─ WS   /audio-stream      바이너리 WebSocket (오디오 활성화 기기 전용 PCM 피드)
          ├─ GET  /api/status        시스템 상태 JSON API
          ├─ GET  /api/devices       오디오 장치 목록 조회
          ├─ POST /api/devices/select 오디오 장치 인덱스 config.yaml 저장
          ├─ POST /api/start         번역 파이프라인 시작
          ├─ POST /api/stop          번역 파이프라인 종료 및 텍스트 파일 저장
          ├─ POST /api/pause         마이크 캡처 및 API 세션 일시 중지
          ├─ POST /api/resume        마이크 캡처 및 API 세션 재개
          ├─ GET  /logo.webp         로컬 저장된 PCA 로고 이미지 서비스
          └─ GET  /api/qr.png        참석자 페이지 접속용 QR 코드 생성
```

---

## 2. 핵심 설계 의사결정

### 모델 선정 (Model Selection)
* **`gemini-3.5-live-translate-preview`**를 사용합니다. 서버 시작 시 API를 조회하여 최신 버전을 자동으로 탐색합니다.
* API 연동 시 `response_modalities=["AUDIO"]` 설정과 `translation_config`를 결합하여 텍스트 자막(`output_transcription.text`)과 합성 오디오를 한 번에 가져옵니다.
* `gemini-3.1-flash-live-preview`는 `response_modalities=["TEXT"]`로 동작하며 `system_instruction`을 지원합니다. 현재 `_build_config()`의 `else` 브랜치로 구현되어 있으나, 전용 번역 모델 대비 번역 품질이 낮아 기본값으로 사용하지 않습니다. 비용 절감 목적의 대안으로 Phase 12에서 검토합니다.

### 자막용 SSE & 오디오용 이진 웹소켓 분리
* 실시간 자막 이벤트는 HTTP SSE(Server-Sent Events) 프로토콜(`/stream`)을 사용하여 전송합니다. SSE는 모바일 브라우저(특히 iOS Safari)가 백그라운드로 전환되거나 WiFi 신호가 끊어졌을 때 브라우저 수준에서 자동으로 재연결을 시도하므로 네트워크 불안정을 메워줍니다.
* 오디오 재생을 활성화한 일부 참석자만 바이너리 웹소켓(`/audio-stream`)을 생성하므로, 단순 자막 수신 기기는 고대역 오디오 트래픽을 아예 소모하지 않아 무선 공유기 부하가 적습니다.

### 장시간 예배를 위한 세션 복구 및 압축
* 60~90분간 지속되는 장시간 예배 상황에 맞춰 `SessionResumptionConfig`와 컨텍스트 압축(`SlidingWindow`)을 강제 적용합니다. 이 설정이 없으면 구글 라이브 세션은 약 10~15분 내에 컨텍스트 용량 초과 또는 대기 타임아웃으로 강제 종료됩니다.

### 오디오 보이스 고정 (Voice Pinning)
* 번역 음성을 남성 목소리인 **`orus`**로 강제 고정합니다. 고정하지 않으면 세션이 재설정되거나 네트워크가 끊겨 재접속될 때마다 모델 보이스가 무작위로 바뀌어 청취자에게 혼란을 줍니다.

### 시각적 디자인 및 UX 시스템 (Branding & UX UI Design)
* **예배 주보 메타포 테마**: 따뜻한 크림 배경색(`#faf8f5`), 고급스러운 네이비 블루 헤더(`#1a2a42`), 골드 데코레이션 선(`#b8923e`)을 조합하여 실제 종이 주보 같은 정갈하고 학구적인 디자인 톤앤매너를 일관되게 적용했습니다.
* **하단 정렬 자막 레이아웃**: 자막 페이지(`/live`) 하단에 자막 영역을 고정하고 텍스트가 아래에서 위로(bottom-up) 밀려 올라가는 레이아웃으로 설계했습니다. 기존 상단 자막 대비 빈 영역이 생기는 문제를 없애 시선 이동을 최소화하고 가독성을 높였습니다.
* **타이포그래피 및 서체 체계**: 다국어 가독성을 최우선으로 고려해 구글 웹 폰트인 `Source Serif 4`(영문 헤더), `Inter`(영문 본문), `Noto Serif KR`(한글 헤더), `Noto Sans KR`(한글 본문)을 혼합 사용하여 단어 간격과 가독성을 극대화했습니다.
* **교회 브랜딩 요소 통합**: 공식 PCA(미국장로교) 로고 자산(`logo.webp`)을 탑재하고 교회의 존엄한 시각 정체성을 반영했습니다.
* **통합 제어 및 일관된 상태 배지**:
  * 참석자용 페이지에는 `● Live`, `● Reconnecting` 등 한눈에 상태를 확인 가능한 명확한 상태 배지를 도입했습니다.
  * 관리자용 페이지는 스피커 아이콘과 볼륨 슬라이더가 상태에 따라 유동적으로 노출되는 스마트 통합 볼륨 컨트롤을 도입하여 복잡도를 낮추었습니다.
* **도움말 가이드 접이식(Foldout) 설계**: 매주 참고할 필요가 없는 1회성 최초 설정 안내는 `<details>`와 `<summary>` 태그를 활용해 접어두어, 일상적인 예배 운용 시의 시각적 노이즈를 최소화했습니다.

---

## 3. 기술 스택 선정 및 대안 비교

| 기술 구분 | 선택한 스택 | 고려된 대안 | 선정 이유 및 기술적 비교 |
| :--- | :--- | :--- | :--- |
| **애플리케이션 런타임** | **Python 3.10+ / FastAPI / Uvicorn** | Node.js, Go, Rust | 구글 제미나이 Live API의 양방향 SDK 지원이 Python에 가장 최적화되어 있습니다. 또한 PyAudio 오디오 캡처 라이브러리와의 연동이 용이하며, FastAPI의 비동기 이벤트 루프는 다수의 모바일 기기로 향하는 SSE 및 WebSocket 팬아웃을 초경량으로 처리합니다. |
| **오디오 캡처** | **PyAudio (PortAudio 래퍼)** | `sounddevice` (NumPy 기반), Pygame | PyAudio는 Windows WASAPI 오디오 드라이버에 직접 연결됩니다. 대안인 `sounddevice`는 Windows 환경에서 컴파일 및 로딩 시 거대한 NumPy 라이브러리가 강제되므로, 순수 바이트 연산만을 사용하여 의존성 크기를 크게 덜어낸 PyAudio를 최종 선택했습니다. |
| **번역 엔진** | **Google Gemini Live API (`gemini-3.5-live-translate-preview`)** | OpenAI Realtime API, Whisper STT + DeepL + ElevenLabs | 제미나이 Live는 번역과 음성 합성을 단일 인코딩 패스로 처리해 지연 시간을 **0.5초** 내외로 보장합니다. 전통적인 STT->텍스트번역->TTS 적층형 구조는 3~5초의 지연이 발생하며 API 호출 비용도 훨씬 높습니다. OpenAI Realtime 대비 비용이 **약 85% 저렴**합니다. |
| **클라이언트 오디오 재생** | **Web Audio API (PCM16 큐)** | HTML5 `<audio>` 태그 (MP3/AAC), HLS/DASH 스트리밍 | HTML5 오디오 태그나 HLS 스트리밍은 인코딩 버퍼 크기로 인해 5~10초의 오디오 지연이 불가피합니다. Web Audio API를 활용해 웹소켓으로 들어오는 24kHz PCM16 오디오 생동(raw floats) 데이터를 직접 오디오 버퍼 큐에 주입하여 자막과 완벽히 동기화된 초저지연 오디오 재생을 달성했습니다. |

---

## 4. 파일 구조 (File Map)

* **`main.py`**: Uvicorn 구동 진입 파일
* **`config.yaml`**: 오디오 장치 인덱스, 포트, 볼륨 설정값 파일
* **`app/audio.py`**: 오디오 입력 캡처, 데시벨 계산 및 리샘플러
* **`app/gemini_session.py`**: Gemini Live 양방향 API 연결, 에러 처리, 보이스 설정
* **`app/broadcast.py`**: 자막 및 오디오 데이터 동시 브로드캐스터
* **`app/server.py`**: FastAPI 웹 엔드포인트 및 모바일 웹페이지 서빙
* **`how_to_use.html`**: 한국어/영어 전환 탭이 탑재된 봉사자용 설명서
* **`logs/sessions/`**: 매 세션 종료 시 시간대별로 정리되어 자동 저장되는 한영 대조 전사록 저장 폴더

---

## 5. 신뢰성 요구사항

* **10분 세션 만료 및 GoAway 신호**: 구글 서버가 주기적으로 연결을 차단하는 GoAway 이벤트를 수신하면, 클라이언트 측 자막 흐름의 끊김 없이 2초 내에 새로운 세션을 맺어 번역을 유지합니다.
* **장치 신호 끊김 (NO_SIGNAL)**: 마이크 신호 입력 강도(RMS)가 10초 이상 무음 상태가 되면 콘솔에 경고를 출력하여 봉사자가 케이블 연결을 확인하도록 유도합니다.

---

## 6. 단계별 개발 현황

* **Phase 0**: 오디오 장치 탐색 및 로컬 마이크 캡처 모듈 구축 (완료 ✅)
* **Phase 1**: Gemini Live API 연동 및 세션 복구 설계 (완료 ✅)
* **Phase 2**: FastAPI 다인용 자막 브로드캐스터 구현 (완료 ✅)
* **Phase 3**: GoAway 복구 및 인터넷 일시 장애 자동 백오프 복구 (완료 ✅)
* **Phase 4**: 모바일 참석용 동적 QR 코드 생성 API 구현 (완료 ✅)
* **Phase 5**: 클래식 예배 주보 스타일 UI 및 폰트 변경 (완료 ✅)
* **Phase 6**: 실시간 요금 계산기, 로그 관리기, 정지 시 로컬 저장 기능 (완료 ✅)
* **Phase 7**: Web Audio API 기반 모바일 실시간 음성 통역 스트리밍 구현 (완료 ✅)
* **Phase 8**: 한국어 원문 및 영어 번역 타임스탬프 기반 자동 매핑 전사록 내보내기 (완료 ✅)

---

## 7. 설정 가이드 (Configuration)

### `config.yaml` 예시
```yaml
audio:
  device_index: 2         # 설정된 입력 믹서 장치 번호
  sample_rate: 16000      # Gemini 전송용 기본 주파수
  channels: 1
  chunk_ms: 100

gemini:
  model: gemini-3.5-live-translate-preview

network:
  host: 0.0.0.0
  port: 8000
```

---

## 8. 향후 확장 계획 (Future Phases)

* **Phase 10 — 다국어 동시 통역**: 하나의 마이크 신호를 분기하여 중국어 등 타 언어 세션을 동시 가동하는 설계 구조 구현.
* **Phase 11 — 원격 참석자용 클라우드 브리지**: 로컬 믹서 오디오 신호를 경량 프로토콜로 클라우드 가상 서버에 쏘고, 클라우드가 전세계 온라인 시청자폰으로 번역 자막을 전송하는 클라우드 연동 구현.
* **Phase 12 — 번역 모델 비용/품질 최적화 (3라운드 벤치마크 완료 ✅)**: `logs/sermon_2min_new.m4a`(2분 클립), CaptionKit 결과, 한국어 원문 기준으로 세 차례 벤치마크 완료. 결과물: `.agent/scratch/benchmark_results/`.
  * **1라운드**: 현재 운영 vs. `TURN_INCLUDES_ONLY_ACTIVITY` → 차이 없음. 번역 모델은 `translation_config` 내부 VAD로 전환 경계 자체 관리, `realtime_input_config` 무효.
  * **2라운드**: `translation_config` 전용 / 하이브리드 / `system_instruction` 전용+`TURN_INCLUDES_ONLY_ACTIVITY` 3가지 비교 → 차이 없음. `system_instruction`은 수락되나 번역 모델 내부 엔진이 무시.
  * **3라운드 (진짜 모델 비교)**: `gemini-3.5-live-translate-preview`+`translation_config`(A) vs. `gemini-3.1-flash-live-preview`+`system_instruction`(B). A: 2분 완주, 825ms 지연, 1882자 ✅. B: **약 30초 후 세션 충돌** (WebSocket keepalive timeout, 오류 1011), 43자만 출력 ❌.
  * **결론**: `gemini-3.1-flash-live-preview`는 지속적 오디오 스트림에서 불안정 — 60~90분 예배에 부적합. **현재 운영 설정(`gemini-3.5-live-translate-preview`+`translation_config`)이 최적**. 오디오 입력 품질이 번역 품질의 주요 변수.
* **Phase 13 — 무설치 Windows 실행 파일 생성**: PyInstaller를 활용해 Python이 없는 PC에서도 더블 클릭하여 실행할 수 있는 독립형 `.exe` 런처 팩킹.

</details>

***

<details>
<summary><b>🇺🇸 English Version (Click to Collapse/Expand)</b></summary>
<a name="english-section"></a>

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
          ├─ GET  /logo.webp         local PCA logo (served from app/)
          └─ GET  /api/qr.png        QR code PNG (links to /live)
```

---

## 2. Key Design Decisions

### Model selection
- **`gemini-3.5-live-translate-preview`** — selected at startup by querying the API.
- Uses `response_modalities=["AUDIO"]` + `translation_config`; translation text arrives via `server_content.output_transcription.text`.
- Korean source arrives via `server_content.input_transcription.text` (log only, never shown to attendees).
- `gemini-3.1-flash-live-preview` supports `response_modalities=["TEXT"]` with `system_instruction`; implemented as the `else` branch in `_build_config()`. Not the default — translation quality is lower than the dedicated translate model. Evaluated as a cost-reduction option in Phase 12.

### SSE for captions, binary WebSocket for audio
- Caption events (update, commit, unavailable, ping, paused, resumed) travel over SSE (`/stream`).
- Translated audio PCM16 chunks travel over a separate binary WebSocket (`WS /audio-stream`).
- Phones that have audio disabled generate zero audio traffic — the WS connection is never opened.
- Better iOS Safari reconnect behaviour over flaky venue WiFi for SSE.
- Phones reconnect automatically on network drop; no app state to restore.

### Single session per service
- One Gemini session for the entire 60–90 min service.
- Session resumption (`SessionResumptionConfig`) mandatory — without it the WebSocket drops every ~10 min.
- Context window compression (`SlidingWindow`) mandatory — audio sessions cap at 15 min otherwise.
- GoAway messages trigger immediate reconnect before the connection actually drops.

### Caption UX
- Current line replaced in-place as tokens stream in (no flickering append).
- Line committed to scrollback after 1.5s of no new tokens.
- Attendee page: Wake Lock API keeps screen on, font size slider, English-only light Presbyterian bulletin theme.

### Voice pinning
- Translated audio voice is pinned to **`orus`** (deep male) via `SpeechConfig → VoiceConfig → PrebuiltVoiceConfig`.
- Without this, Gemini picks a random voice on every session and GoAway reconnect — audible mid-sermon.
- `orus` was chosen for our pastor (over 70 years old): clear, authoritative, deep male register.
- Voice name is a free-form string in `PrebuiltVoiceConfig`; available names include `orus`, `charon`, `puck`, `kore`, `leda`.

### Cost model
- Billed under the Gemini 3.5 Live Translate Paid Tier rates:
  - Input (Audio): $3.50/1M tokens or $0.0053/min
  - Output (Audio): $21.00/1M tokens or $0.0315/min
  - Total combined audio stream billing rate: $0.0368/min (~$0.00061333/sec).
- Audio output PCM chunks (24kHz PCM16 mono) arrive in `model_turn.inline_data` and are **broadcast to audio-enabled clients** via `broadcaster.on_audio_chunk()` → `WS /audio-stream`. The session is billed at audio input/output rates regardless of whether the client plays the audio.

### Visual Design & UX System
* **Presbyterian Bulletin Theme**: Applied a warm cream background (`#faf8f5`), dignified navy blue headers (`#1a2a42`), and gold accent borders (`#b8923e`) to match the style of traditional printed church bulletins.
* **Bottom-Aligned Caption Flow**: Captions on `/live` align to the bottom of the screen, scrolling upwards. This eliminates empty whitespace gaps and minimizes the reader's eye travel distance.
* **Typography System**: Loaded specialized Google Fonts—`Source Serif 4` for serif headers, `Inter` for clean sans-serif caption bodies, `Noto Serif KR` for Korean headings, and `Noto Sans KR` for Korean body elements—to maximize readability.
* **Church Branding**: Embedded the official PCA logo (`logo.webp`) to preserve visual integrity.
* **Unified Control Console & Status Badges**:
  * Attendee views feature clear pill-shaped status status badges (`● Live`, `● Reconnecting`).
  * The operator console hides/reveals volume sliders dynamically within a single, unified audio button (`🔇 Audio Off` / `🔊 Audio On`) to reduce visual clutter.
* **Setup Guide Foldout**: Wrapped the one-time installation guide inside a collapsible `<details>`/`<summary>` tag to keep it out of sight for routine weekly operations, focusing attention entirely on the Sunday workflow.

---

## 3. Tech Stack Choices & Alternatives Comparison

| Component | Chosen Technology | Alternatives Considered | Why Chosen / Comparison |
| :--- | :--- | :--- | :--- |
| **Application Runtime** | **Python 3.10+ / FastAPI / Uvicorn** | Node.js (Express), Go, Rust | Python provides first-class, official SDK support for the Gemini Live API (`bidiGenerateContent`) and PyAudio integration. FastAPI's async event loop (via Uvicorn) handles high-concurrency Server-Sent Events (SSE) and WebSockets with extremely low overhead, keeping the server resource-light. |
| **Audio Capture** | **PyAudio (PortAudio wrapper)** | `sounddevice` (NumPy-based), Pygame, standard wave capture | PyAudio interfaces directly with Windows WASAPI and MME sound drivers. It captures raw bytes from the USB mixer and allows mono downsampling and RMS computation without pulling in heavy math libraries like NumPy, keeping the bundle size small for easier deployment. |
| **Translation Engine** | **Google Gemini Live API (`gemini-3.5-live-translate-preview`)** | OpenAI Realtime API, Whisper STT + DeepL + ElevenLabs | Gemini Live is a native multimodal speech-to-speech engine. By performing transcription, translation, and voice synthesis in a single unified model pass, it achieves sub-second latency (~0.5s). In comparison, cascade setups (STT -> Text -> TTS) incur 3–5 seconds of latency and cost much more. OpenAI Realtime is also ~85% more expensive ($0.24/min vs $0.0368/min combined). |
| **Streaming Protocol** | **Hybrid SSE (Captions) + WS (Audio)** | WebSockets for all, WebRTC / LiveKit, HTTP Long Polling | Server-Sent Events (SSE) has native auto-reconnection and buffering built into mobile web browsers (especially iOS Safari), making it highly resilient to flaky sanctuary WiFi. Using a separate WebSocket (`/audio-stream`) for binary audio ensures that attendees who only want text captions do not consume high-bandwidth audio streams. WebRTC/LiveKit would require deploying a heavy media server (SFU), adding excessive setup complexity. |
| **Browser Audio Playback** | **Web Audio API (PCM16 Queue)** | HTML5 `<audio>` tag (MP3/AAC), HLS/DASH streaming | HTML5 audio tags and HLS streaming require containerized media files and manifests, introducing 5–10 seconds of buffering latency. The Web Audio API allows the browser to feed raw, uncompressed 24kHz PCM16 audio floats directly into the output stream, achieving real-time playback synced perfectly with the captions and the speaker. |

---

## 4. File Map

| File | Role |
|------|------|
| `main.py` | Entry point — `uvicorn main:app` |
| `config.yaml` | Runtime config (device index, port, log path, model) |
| `.env` | `GEMINI_API_KEY` — never committed |
| `requirements.txt` | Python dependencies |
| `app/config.py` | Config loader + `save_audio_device()`, `save_gemini_model()` |
| `app/logger.py` | Rotating file + console logger |
| `app/audio.py` | PyAudio capture, PCM16 resampling, RMS metering, disconnect detection |
| `app/gemini_session.py` | Gemini Live session, auto model selection, reconnection, GoAway |
| `app/broadcast.py` | SSE caption fanout + binary PCM audio fanout (`_audio_clients`) |
| `app/server.py` | FastAPI routes + embedded HTML (operator, attendee pages) + local logo route |
| `README.md` | Setup and operator guide |
| `how_to_use.html` | Visual volunteer-facing guide |
| `.agent/agent.md` | AI assistant reference |
| `.agent/scripts/` | Test and validation scripts |
| `.agent/scratch/` | Temp files, test WAVs, V2 quality results |
| `logs/ops.log` | Operational log: server start/stop, audio device events, pause/resume, reconnects (INFO+) |
| `logs/session.log` | Gemini session log: connect events, `[KO]` source, `[EN turn]` translation, debug deltas (DEBUG+) |
| `logs/sessions/YYYYMMDD_HHMMSS/summary.txt` | Per-session runtime, cost, model summary |
| `logs/sessions/YYYYMMDD_HHMMSS/ko.txt` | Korean source turns with timestamps |
| `logs/sessions/YYYYMMDD_HHMMSS/en.txt` | English translation turns with timestamps |
| `logs/sessions/YYYYMMDD_HHMMSS/aligned.txt` | Korean + English interleaved, human-readable |

---

## 5. Reliability Requirements

| Scenario | Behaviour |
|----------|-----------|
| Normal 10-min WebSocket boundary | GoAway → auto-reconnect → captions resume in ~2-3s |
| Internet outage | Exponential backoff (2s, 4s, 8s), then FAILED state if 3 attempts fail |
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
| 5 | Visual and UX Revamp: Presbyterian bulletin aesthetic, English UI, local logo | ✅ Done |
| 6 | Operator enhancements: pause, runtime, cost estimate, session log, TSV export | ✅ Done |
| 7 | Translated audio playback in browser (Web Audio API, 24kHz PCM16, binary WS, pinned `orus` voice) | ✅ Done |
| 8 | Post-service transcript export: per-session folder with `ko.txt`, `en.txt`, `aligned.txt`, `summary.txt` | ✅ Done |
| V0–V5 | Verification protocol | ✅ All passed |

---

## 7. Configuration Reference

```yaml
# config.yaml
audio:
  device_index: 2       # set by operator; run `python -m app.audio --list` to find index
  sample_rate: 16000
  channels: 1
  chunk_ms: 100

gemini:
  model: gemini-3.5-live-translate-preview  # auto-updated on startup

network:
  host: 0.0.0.0
  port: 8000
  # public_url: "http://192.168.1.x:8000"  # override if auto-detect picks wrong interface

logging:
  log_dir: logs
  max_bytes: 10485760   # 10 MB
  backup_count: 5
```

---

## 8. Future Phases (not scoped)

### Phase 10 — Multi-language: add Chinese target alongside English
- The Gemini Live translate model currently exposes one `target_language_code` per session. To support two simultaneous output languages, two parallel `GeminiSession` instances would be needed — one targeting `"en"`, one targeting `"zh"`.
- Each session would get the same microphone audio (duplicate the `_pipe` coroutine).
- The attendee page (`/live`) would need a language selector that switches which SSE stream it subscribes to (e.g. `/stream?lang=en` vs `/stream?lang=zh`).

### Phase 12 — Translation model cost/quality evaluation (3 rounds benchmarked ✅)

All benchmarks used `logs/sermon_2min_new.m4a` (2 min from `sermon_6_24_2026.mp4` @ 24:33), with CaptionKit output and Korean ground truth as reference. Artifacts: `.agent/scratch/benchmark_results/`.

**Round 1 — `TURN_INCLUDES_ONLY_ACTIVITY` on the translate model (`config_a.txt`, `config_b.txt`)**
- Config A: current production (`translation_config`, no `realtime_input_config`)
- Config B: same + `TURN_INCLUDES_ONLY_ACTIVITY`
- **Result**: No difference. The translate model manages turn boundaries internally via `translation_config` VAD; `realtime_input_config` is ignored.

**Round 2 — `system_instruction` on the translate model (`v2_report.txt`)**
Probe confirmed `gemini-3.5-live-translate-preview` accepts `system_instruction`. Tested:
- Config A: `translation_config` only — 606ms latency, 1939 EN chars
- Config B: `translation_config` + `system_instruction` — 619ms, 1991 chars
- Config C: `system_instruction` only + `TURN_INCLUDES_ONLY_ACTIVITY` — 587ms, 1989 chars
- **Result**: No difference. `system_instruction` is accepted but ignored — the translate model's internal engine dominates regardless.

**Round 3 — `gemini-3.1-flash-live-preview` + `system_instruction` vs `gemini-3.5-live-translate-preview` (`v3_report.txt`)**
The genuine model comparison: example-code model vs. production model.
- Config A: `gemini-3.5-live-translate-preview` + `translation_config` — completed full 2 min, 825ms latency, 1882 EN chars ✅
- Config B: `gemini-3.1-flash-live-preview` + `system_instruction` + `TURN_INCLUDES_ONLY_ACTIVITY` — **session crashed** after ~30s (WebSocket keepalive timeout, error 1011), only 43 chars captured ❌
- **Result**: `gemini-3.1-flash-live-preview` is not stable for continuous 2-minute audio streams. `gemini-3.5-live-translate-preview` is the correct choice for a 60–90 minute church service.

**Key findings across all rounds:**
- **Current production config (`gemini-3.5-live-translate-preview` + `translation_config`) is optimal.**
- `gemini-3.1-flash-live-preview` drops sessions under sustained audio load — unsuitable for live services.
- `system_instruction` has no effect on the translate model; the internal translation engine overrides it.
- Audio input quality is the dominant factor for translation accuracy.
- `gemini-3.1-flash-live-preview` TEXT modality (cost-reduction path): returns error 1007 — not available.

### Phase 11 — Cloud deployment for remote attendees
1. Deploy `main.py` to a small cloud VM (e.g. Google Cloud Run, Railway, or a VPS).
2. Audio cannot be captured in the cloud — the PC still captures audio and POSTs PCM chunks to the cloud server via a lightweight WebSocket or HTTP stream.
3. The cloud server pipes audio into Gemini Live and fans SSE captions out to all attendees globally.

### Phase 13 — Single all-in-one executable (Windows `.exe`)
- PyInstaller bundles the Python interpreter, all pip packages, and native DLLs into a single self-extracting executable. A volunteer should be able to double-click one file to start the translation service — no Python knowledge needed.

</details>
