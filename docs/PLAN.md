# 기술 유지보수 및 아키텍처 플랜 / Technical Maintainer & Architecture Plan
### 실시간 예배 번역 시스템 / Live Translation System

이 문서는 실시간 한영 자막 및 음성 통역 시스템의 개발자, 시스템 관리자 및 기술 봉사자를 위한 시스템 설계 및 아키텍처 정보를 제공합니다.

---

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
    │                            커밋 이벤트에 매칭된 한국어 원문 (`ko` 필드) 포함
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
          ├─ GET  /logo.webp              로컬 저장된 PCA 로고 이미지 서비스
          ├─ GET  /api/qr.png             참석자 페이지 접속용 QR 코드 생성
          └─ GET  /api/events?since=N     운영자 이벤트 증분 폴링
```

---

## 2. 핵심 설계 의사결정

### 모델 선정 (Model Selection)
- **`gemini-3.5-live-translate-preview`** 사용. 서버 시작 시 API를 조회하여 최신 버전을 자동 탐색.
- `response_modalities=["AUDIO"]` + `translation_config` 조합으로 텍스트 자막(`output_transcription.text`)과 합성 오디오를 동시에 수신.
- `gemini-3.1-flash-live-preview`는 Phase 12 벤치마크에서 30초 후 세션 충돌(오류 1011) — 60~90분 예배에 부적합.
- `system_instruction`은 번역 모델에서 수락되지만 내부 엔진이 무시함 (Phase 12 Round 2에서 확인).
- **`language_codes` 언어 힌트 설정 금지 (Developer API)**: `AudioTranscriptionConfig(language_codes=["ko", "en"])`는 Enterprise 모드 전용이며, Developer API 모드에서는 `ValueError`가 발생하여 연결이 차단됩니다. 반드시 빈 `AudioTranscriptionConfig()`를 전달해야 하며, 언어 이탈은 수신된 전사본의 롤링 감시로 처리합니다.

### 자막용 SSE & 오디오용 이진 웹소켓 분리
- 실시간 자막은 SSE(`/stream`) — iOS Safari 백그라운드 전환 시 브라우저 수준 자동 재연결.
- 번역 오디오는 바이너리 웹소켓(`/audio-stream`) — 오디오 미사용 기기는 트래픽 소모 없음.

- **GoAway 세션 주기 (~9분 및 ~27분 단절 경계)**: Gemini Live 번역 세션은 대략 9분 주기로(주로 ~8-10분 및 ~27분 경계에서 관찰됨) `GoAway` 연결 교체 신호를 보냅니다. 이는 세션 복구 및 재시도 로직을 통해 백그라운드에서 완전히 투명하게 처리되며, 매번 재연결에 성공할 때마다 시도 횟수를 리셋하여 오류 한도 초과를 예방합니다.
- **성공 시 재시도 횟수 초기화**: 성공적으로 연결되면 재시도 횟수(`self._attempt`)를 0으로 리셋하여 GoAway 재연결이 누적되어 한도를 초과하지 않도록 합니다.
- **제한된 자동 재시작 루프**: Gemini 세션이 완전히 실패하면 `server.py`에서 제한된 복구 루프(3회 시도: 2초, 5초, 15초 대기)가 동작하며, 복구 중에는 프런트엔드에서 경고 알림(차임벨 소리 및 상태 카드 깜빡임)을 제공합니다.

### 오디오 보이스 고정 (Voice Pinning)
- 번역 음성을 **`orus`**(딥 남성 보이스)로 강제 고정 — 세션 재연결 시 무작위 보이스 변경 방지.

### 시각적 디자인 및 UX
- 예배 주보 메타포 테마: 크림 배경(`#faf8f5`), 네이비 헤더(`#1a2a42`), 골드 악센트(`#b8923e`).
- 하단 정렬 자막: 새 자막이 아래에서 위로 밀어 올라가는 레이아웃.
- 타이포그래피: `Source Serif 4` / `Inter` (영문), `Noto Serif KR` / `Noto Sans KR` (한글).

### 비용 모델
- Gemini 3.5 Live Translate 유료 티어: 입력 오디오 $3.50/1M 토큰, 출력 오디오 $21.00/1M 토큰.
- 합산 분당 요율: **약 $0.0368/분** → 60분 예배 약 $2.21.
- 참석자 수에 관계없이 서버 단일 세션 비용만 청구.

---

## 3. 기술 스택 선정 및 대안 비교

| 기술 구분 | 선택 스택 | 고려된 대안 | 선정 이유 |
| :--- | :--- | :--- | :--- |
| **애플리케이션 런타임** | Python 3.10+ / FastAPI / Uvicorn | Node.js, Go, Rust | Gemini Live API Python SDK 최적화, PyAudio 연동, FastAPI 비동기 SSE/WS 팬아웃 |
| **오디오 캡처** | PyAudio (PortAudio 래퍼) | sounddevice, Pygame | Windows WASAPI 직접 연결, 순수 바이트 연산으로 의존성 최소화 |
| **번역 엔진** | Gemini Live API (`gemini-3.5-live-translate-preview`) | OpenAI Realtime, Whisper+DeepL+ElevenLabs | 단일 패스 번역·음성합성, 지연 ~0.5초. OpenAI Realtime 대비 약 85% 저렴 |
| **자막 스트리밍** | SSE + 이진 WebSocket 혼합 | WebSocket 단일, WebRTC/LiveKit | SSE 자동 재연결(iOS Safari 포함), 오디오 미사용 기기 트래픽 절감 |
| **브라우저 오디오 재생** | Web Audio API (PCM16 큐) | HTML5 `<audio>`, HLS/DASH | HLS 5~10초 버퍼링 지연 제거, 자막과 완벽 동기화된 실시간 재생 |

---

## 4. 파일 구조 (File Map)

| 파일 | 역할 |
|------|------|
| `main.py` | 서버 진입점 — uvicorn 구동, 브라우저 자동 열기, 포트 충돌 감지 |
| `config.yaml` | 런타임 설정 (장치 인덱스, 포트, 로그 경로, 모델) |
| `.env` | `GEMINI_API_KEY` — git에 커밋하지 않음 |
| `requirements.txt` | Python 의존성 패키지 목록 |
| `SKC_start.bat` | 원클릭 서버 실행 스크립트 (conda 환경 활성화) |
| `SKC_translation.spec` | PyInstaller 빌드 스펙 — 단일 exe 생성 |
| `build_exe.bat` | 원클릭 exe 빌드 스크립트 |
| `app/config.py` | 설정 로더 + `save_audio_device()`, `save_gemini_model()`, `admin_cfg()` |
| `app/events.py` | `OperatorEventLog` — 스레드 안전 링 버퍼(50개), 7개 카테고리, `since(last_id)` API |
| `app/logger.py` | 회전 파일 + 콘솔 로거 |
| `app/audio.py` | PyAudio 캡처, PCM16 리샘플링, RMS 미터링, 연결 해제 감지 |
| `app/gemini_session.py` | Gemini Live 세션, 모델 자동 선택, 재연결, GoAway 처리 |
| `app/broadcast.py` | SSE 자막 팬아웃 + 바이너리 PCM 오디오 팬아웃 |
| `app/server.py` | FastAPI 라우트 + 운영자·참석자 페이지 HTML + 로고 라우트 |
| `app/glossary.py` | 번역 후처리 용어집 교정 패스 (PCA 고유 용어 강제 적용) |
| `config/glossary.yaml` | 용어집 정의 파일 (직접 치환 항목 + 검토 전용 항목) |
| `docs/HOW_TO_USE.md` | 언어 선택 인덱스 → `.en.md` / `.ko.md` |
| `docs/HOW_TO_USE.en.md` | 봉사자 운영 매뉴얼 (영어) |
| `docs/HOW_TO_USE.ko.md` | 봉사자 운영 매뉴얼 (한국어) |
| `docs/PLAN.md` | 아키텍처 플랜 및 기술 결정 (이 파일) |
| `docs/WALKTHROUGH.md` | 세션별 개발 빌드 히스토리 (이중 언어) |
| `docs/TECHNICAL.md` | 코드 레벨 기술 참고서 (이중 언어) |
| `docs/BUILD_EXE.md` | 단일 exe 빌드 시도 기록 (이중 언어) |
| `logs/ops.log` | 운영 로그: 서버 시작/종료, 오디오, 일시정지/재개, 재연결 (INFO+) |
| `logs/session.log` | Gemini 세션 로그: 연결, `[KO]` 원문, `[EN turn]` 번역 (DEBUG+) |
| `logs/sessions/YYYYMMDD_HHMMSS/` | 세션별 전사본 폴더 (`ko.txt`, `en.txt`, `aligned.txt`, `summary.txt`) |

---

## 5. 신뢰성 요구사항

| 시나리오 | 동작 |
|----------|------|
| 정상 10분 WebSocket 경계 | GoAway → 자동 재연결 → 2~3초 내 자막 재개 |
| 인터넷 장애 | 지수 백오프 (2초, 4초, 8초), 3회 실패 시 FAILED 상태 |
| FAILED 상태 | 모든 참석자 SSE에 "번역 불가" 이벤트 전송 |
| 오디오 장치 연결 해제 | 스트림 읽기 오류 시 `AudioStatus.DISCONNECTED` |
| 장시간 묵음 (>10초) | `AudioStatus.NO_SIGNAL` — 장치 해제와 구별 |
| 참석자 WiFi 끊김 | SSE 자동 재연결; 기기에 "재연결 중..." 표시 |

---

## 6. 단계별 개발 현황

| Phase | 범위 | 상태 |
|-------|------|------|
| 0 | 오디오 캡처: 장치 열거, PCM16, 레벨 미터링, WAV 테스트 | ✅ 완료 |
| 1 | Gemini Live 세션: TEXT 모드, 세션 복구, 컨텍스트 압축 | ✅ 완료 |
| 2 | FastAPI + SSE 팬아웃 + 참석자/운영자 페이지 | ✅ 완료 |
| 3 | 신뢰성: GoAway, 재시도, FAILED 상태 | ✅ 완료 |
| 4 | 운영자 상태 API + QR 코드 | ✅ 완료 |
| 5 | UI/UX 개편: 장로교 주보 테마, 영문 UI, 로컬 로고, 브랜딩 QR | ✅ 완료 |
| 6 | 운영자 기능 강화: 일시정지, 런타임, 비용 추정, 세션 로그, 전사록 내보내기 | ✅ 완료 |
| 7 | 번역 음성 재생: Web Audio API, 24kHz PCM16, 이진 WS, `orus` 보이스 고정 | ✅ 완료 |
| 8 | 예배 후 전사록 내보내기: 세션 폴더, `ko.txt`, `en.txt`, `aligned.txt`, `summary.txt` | ✅ 완료 |
| 9 | 오디오 파이프라인 진단 및 마이크 선택 자동화: DirectSound 거부, 네이티브 16kHz, USB 핫플러그, SciPy 리샘플링 | ✅ 완료 |
| 10 | 자막 커밋 전략 정교화: `MAX_LINE_CHARS=150` 안전망, `_find_split()`, 언어 힌트 (`ko`/`en`) | ✅ 완료 |
| 11 | 운영자 화면 UX 전면 개편: 한국어+영어 쌍 표시, 상태 카드 4열 압축, 레이아웃 재정렬 | ✅ 완료 |
| 12 | 번역 모델 비용/품질 3라운드 벤치마크: `gemini-3.5-live-translate-preview` 최적 확인 | ✅ 완료 |
| 13 | 단일 실행 파일(.exe): PyInstaller 70MB, `SKC_translation.spec`, `build_exe.bat` | ✅ 완료 |
| 14 | 운영자 이벤트 로그(`app/events.py`), 상태 스트립, `/api/events`, `/admin/logs` 개발자 진단 | ✅ 완료 |
| 15 | 제한된 자동 복구(Auto-Restart) 루프, 예외 수신 상세화, 운영자 경고 알림 및 27분 GoAway 근본 원인 규명 완료 | ✅ 완료 |
| 16 | mDNS 호스트네임 광고 (`python-zeroconf`), 동적 URL 리졸버, 운영자 콘솔 주/비상용 접속 주소 표시 | ✅ 완료 |
| 17 | UI 외부 템플릿 리팩토링: `attendee.html` 및 `operator.html`을 `server.py`에서 분리하고, 개발 환경 실시간 핫 리로드를 위한 동적 로더 구현 | ✅ 완료 |
| 18 | 운영자 콘솔 UX: 고정 헤더, 상태 스트립 헤더 통합, 반응형 타이틀, 6열 통계 그리드, 전체 요소 툴팁, 사용 가이드 카드. 참석자 화면: 한국어 탭 보기 (백엔드 `ko` 필드), 단락 그루핑, 타임스탬프 제거, 폰트 범위 20–40px. GoAway 로그 INFO 레벨 수정. 운영자 미리보기 중복 텍스트 버그 수정. | ✅ 완료 |
| 19 | 독립 설정 마법사 GUI (`setup_gui.py` / `SKC_setup.exe`) 및 2개 실행 파일 배포 아키텍처 구현 | ✅ 완료 |
| 20 | Gemini Live 실시간 번역 모델 선택 엔진 (`app/model_resolver.py`), 직접 `preferred_model` 설정, LKG 폴백, 검증 세션 저장 및 상태 카드 UI 연동 | ✅ 완료 |
| 21 | 오염 방지 및 무결 세션 리셋 아키텍처: 일시정지 시 오디오 프레임 즉시 폐기, Pause → Resume 컨텍스트 완전 분리 및 재개 토큰 초기화, 세션 세대(Epoch) 격리, 완료 턴 기반 롤링 언어 이탈 감시 및 오디오 버퍼 플러시 | ✅ 완료 |
| 22 | 운영자 콘솔 컨트롤 바 분리 및 일시정지 알림: 3열 고정 레이아웃, `role="status"` 및 `aria-live="polite"` 접근성 상태 배지, 일시정지 경과 타이머, 3분 경과 주황색 강조 알림, `prefers-reduced-motion` 지원 및 비활성 중립 회색 종료 버튼 | ✅ 완료 |
| V0–V22 | 검증 프로토콜 및 자동화 테스트 스위트 (44개 테스트) | ✅ 전체 통과 |

---

## 7. 설정 가이드 (Configuration)

```yaml
audio:
  device_index: 2         # 설정된 입력 믹서 장치 번호
  sample_rate: 16000      # Gemini 전송용 기본 주파수
  channels: 1
  chunk_ms: 100
  auto_stop_timeout_min: 10

gemini:
  preferred_model: gemini-3.5-live-translate-preview
  fallback_model: gemini-3.5-live-translate-preview
  voice: "orus"
  auto_drift_correction: false

network:
  host: 0.0.0.0   # 모든 인터페이스 수신 (로컬 + WiFi 참석자)
  hostname: skc.live
  port: 8080
  # public_url: "http://192.168.1.x:8080"  # override if auto-detect picks wrong interface

logging:
  log_dir: logs
  max_bytes: 10485760   # 10 MB
  backup_count: 5
```

---

## 8. 향후 확장 계획 (Future Phases)

### Phase 23 — `develop` HTTPS / Cloudflare / 관리자 보안 통합 및 `main` 승격 (Pre-Multilingual Integration)
- **상세 아키텍처 및 구현 계획**: [`.agent/https_implementation_plan.md`](file:///d:/Desktop/church/live_translation/SKC_live_translation_B/.agent/https_implementation_plan.md) 참조.
- **배경 및 원칙**:
  - `main`과 `develop` 브랜치가 크게 분기되었으므로 단순 `git merge develop`을 피하고, `develop`의 검증된 HTTPS/보안 파이프라인을 `main` 코드베이스로 **선택적 포팅(Selective Porting)**.
  - HTTPS / 공인 접속 / 관리자 보안 기능은 상용 필수 인프라이므로, 검증 완료 후 `main`에 머지하여 **새로운 공식 `main`**으로 확립.
- **가장 단순한 Git 브랜치 워크플로우**:
  1. `main`에서 통합 브랜치 생성: `git switch main && git switch -c feature/pre-multilingual-https-security`
  2. HTTPS, PublicHostGuard, Telemetry, Admin Password 구현 및 테스트.
  3. 완료 후 `main`에 머지: `git switch main && git merge --ff-only feature/pre-multilingual-https-security`
  4. `develop` 브랜치를 새로운 `main`으로 즉시 덮어쓰기:
     - `git switch develop && git reset --hard main`
     - `git push --force-with-lease origin develop`
  5. 새로운 `develop`에서 Phase 24 다국어 기능 브랜치 생성 후 작업 시작.
- **핵심 구현 목표**:
  1. **Cloudflare Named Tunnel & HTTPS 지원**: SSL/HTTPS 암호화 스트리밍 및 공인 도메인 연결.
  2. **Public-Host Guard Middleware**: 공개 호스트 유입 시 `/admin` 및 API 제어 라우트 차단, 오직 참석자 라우트만 허용.
  3. **관리자 비밀번호 보호**: `.env` 기반 `ADMIN_PASSWORD` 인증 세션 지원.
  4. **WebSocket 텔레메트리 (`/ws/telemetry`)**: 30초 하트비트 기반 동시 접속자 수 및 로컬/퍼블릭 지연 시간 추적.
  5. **통합 베이스라인 구축**: 다국어 기능 브랜치는 이 HTTPS 보안 통합 베이스라인 위에서 생성.


### Phase 24 — 다국어 / 다중 타겟 동시 통역 (Multilingual / Multi-Target Translation)
- **상세 아키텍처 및 구현 계획**: [`.agent/SKC Live Translation — Multilingual - Multi-Target Implementation Plan.md`](file:///d:/Desktop/church/live_translation/SKC_live_translation_B/.agent/SKC%20Live%20Translation%20%E2%80%94%20Multilingual%20-%20Multi-Target%20Implementation%20Plan.md) 참조.
- **핵심 아키텍처 불변 원칙**:
  - **단일 코드베이스 (One Codebase)**: 언어별 별도 레포/브랜치/exe 복제 금지.
  - **단일 오디오 캡처**: 단일 `AudioCapture`에서 캡처한 PCM 청크를 모든 활성 세션에 브로드캐스트.
  - **독립 타겟 세션**: 활성 타겟 언어마다 1개의 독립 `GeminiSession[lang]` 가동 (예: English `en`, Ukrainian `uk`, Chinese `zh`).
  - **참석자 라우팅**: 단일 QR 코드 ➔ 언어 선택 허브 (`/`) ➔ 언어별 스트림 (`/live?lang=uk` / `/stream?lang=uk` / `/audio-stream?lang=uk`).
- **1차 검증 목표**:
  1. 한국어 → 영어 (`ko` → `en`) : 기존 SKC 회귀 테스트.
  2. 영어 → 우크라이나어 (`en` → `uk`).
  3. 영어 → 중국어 (`en` → `zh`).
  4. 영어 → 우크라이나어 + 중국어 (`en` → `uk` + `zh`) 동시 통역.






