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
    ├─ app/audio.py ──────────── 장치가 지원하는 포맷으로 PCM16 단일 캡처
    │                            원시 입력을 MP3로 분기한 뒤 번역용 16kHz 모노로 변환
    │                            RMS 데시벨 레벨 미터링 (10Hz 주기)
    │                            신호 차단 및 장치 연결 해제 자동 감지
    │
    ├─ app/recording.py ───────── 원시 입력 MP3 백업 (약 128 kbps, 일시정지 중 유지)
    │
    ├─ app/translation_manager.py ─ 다중 타겟 세션 및 브로드캐스터 중앙 오케스트레이션
    │    │                         단일 오디오 청크를 각 타겟 세션으로 비동기 팬아웃
    │    │                         타겟별 장애 격리 (Directional Failure Isolation)
    │    │
    │    ├─ app/gemini_session.py [target: en, zh, es, ...] (타겟별 독립 세션)
    │    │    한국어/영어 소스 전사 수신 + 타겟 번역 텍스트/합성 오디오 수신
    │    │    GoAway 서버 종료 신호 감지 시 자동 복구 및 재연결
    │    │
    │    └─ app/broadcast.py [target: en, zh, es, ...] (타겟별 독립 브로드캐스터)
    │         메모리 내 SSE 자막 전송 (`/stream?lang=<target>`)
    │         바이너리 WebSocket 오디오 전송 (`/audio-stream?lang=<target>`)
    │
    └─ app/server.py ─────────── FastAPI 웹 서버
          │
          ├─ GET  /admin                운영자 콘솔 (인증 보호, 타겟 언어 다중 선택, 상태 제어)
          ├─ GET  /live                 참석자 페이지 (지속형 언어 선택 드롭다운, 헤더 오디오 버튼)
          ├─ GET  /stream               SSE 자막 스트림 (타겟 언어 파라미터 지원)
          ├─ WS   /audio-stream         바이너리 WebSocket 오디오 피드
          ├─ WS   /ws/telemetry         실시간 RTT 측정 및 지연 분리 텔레메트리
          ├─ GET  /api/status           시스템 상태 및 모델 정보 JSON API
          ├─ GET  /api/languages        실시간 활성/지원 타겟 언어 카탈로그
          ├─ GET  /api/devices          오디오 장치 목록 조회 및 동적 리스캔
          ├─ POST /api/start|stop|pause|resume  번역 파이프라인 수명주기 제어
          ├─ GET  /api/qr.png           참석자 페이지 접속용 QR 코드 생성
          └─ GET  /api/events?since=N   운영자 이벤트 증분 폴링
```

---

## 2. 핵심 설계 의사결정

### 모델 선정 (Model Selection)
- `config.yaml`의 **`gemini.model`을 그대로 사용**합니다. 모델 탐색, 자동 전환, LKG 저장은 없습니다. 지정 모델을 사용할 수 없으면 해당 모델명을 포함한 오류를 표시합니다.
- `response_modalities=["AUDIO"]` + `translation_config` 조합으로 텍스트 자막(`output_transcription.text`)과 합성 오디오를 동시에 수신.
- `gemini-3.1-flash-live-preview`는 Phase 12 벤치마크에서 30초 후 세션 충돌(오류 1011) — 60~90분 예배에 부적합.
- `system_instruction`은 번역 모델에서 수락되지만 내부 엔진이 무시함 (Phase 12 Round 2에서 확인).
- 입력 및 출력 전사는 빈 `AudioTranscriptionConfig()`로 켭니다. 한국어 예배의 영어 통역을 주로 사용하되 원문 언어별로 출력을 차단하지 않습니다. 기존 `translation.expected_source_languages`는 무시하며 Google에 언어 힌트를 보내지 않습니다. 판단 근거와 CER 한계는 [STACK.md](STACK.md), [evaluation.md](evaluation.md)를 참고하세요.

### 자막용 SSE & 오디오용 이진 웹소켓 분리
- 실시간 자막은 SSE(`/stream`) — iOS Safari 백그라운드 전환 시 브라우저 수준 자동 재연결.
- 번역 오디오는 바이너리 웹소켓(`/audio-stream`) — 오디오 미사용 기기는 트래픽 소모 없음.

- **GoAway 세션 주기 (연결당 9분)**: Gemini Live는 각 연결을 연 뒤 9분 후 `GoAway` 연결 교체 신호를 보냅니다 (근거: `/docs/TECHNICAL.md`). 이는 세션 복구 및 재시도 로직을 통해 백그라운드에서 완전히 투명하게 처리되며, 매번 재연결에 성공할 때마다 시도 횟수를 리셋하여 오류 한도 초과를 예방합니다.
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
- 출력 언어마다 독립 API 세션 비용이 발생합니다. 같은 출력 언어의 참석자 수가 늘어도 API 세션은 추가되지 않습니다.

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
| `SKC_start.bat` | 개발용 실행 도구, EXE 배포에서는 제외 |
| `SKC_translation.spec` | PyInstaller 빌드 스펙 — 단일 exe 생성 |
| `build_exe.bat` | 원클릭 exe 빌드 스크립트 |
| `build_parallel.py` | 멀티스레드 병렬 exe 빌드 러너 |
| `setup_gui.py` | 2열 데스크톱 설정 마법사 GUI (`SKC_setup.exe`) |
| `app/config.py` | 설정 로더 + 장치·출력 언어 설정 저장, 주석 보존 YAML 수정 |
| `app/events.py` | `OperatorEventLog` — 스레드 안전 링 버퍼(50개), 7개 카테고리, `since(last_id)` API |
| `app/logger.py` | 회전 파일 + 콘솔 로거 |
| `app/audio.py` | PyAudio 캡처, PCM16 리샘플링, RMS 미터링, 연결 해제 감지 |
| `app/recording.py` | 네이티브 캡처 PCM의 MP3 백업, 연속 시간축과 누락 메타데이터, 21일 초과 파일 정리 |
| `app/translation_manager.py` | 다중 타겟 번역 매니저 — 세션 팬아웃, 장애 격리, 세션 수명주기 오케스트레이션 |
| `app/languages.py` | 지원 언어 카탈로그 (70+ 언어, 네이티브 명칭, 유효성 검증) |
| `app/gemini_session.py` | 고정 모델의 Gemini Live 세션, 입력 언어 기반 출력 보호, 재연결, GoAway 처리 |
| `app/broadcast.py` | SSE 자막 팬아웃 + 바이너리 PCM 오디오 팬아웃 |
| `app/server.py` | FastAPI 라우트 + 수명주기 셧다운 이벤트 + 템플릿 렌더링 + 미들웨어 |
| `app/templates/operator/` | 모듈식 운영자 콘솔 템플릿 (`_auth_modal.html`, `_language_targets.html` 등) |
| `app/templates/attendee.html` | 지속형 언어 선택 드롭다운 + 헤더 오디오 제어 참석자 화면 |
| `app/text_corrections.py` | 확정 자막의 이름 치환과 과도한 반복 축약 |
| `config/name_corrections.yaml` | 수동으로 관리하는 정확 일치 이름 교정표 |
| `how_to_use.html` | 봉사자 운영 매뉴얼 (한국어·영어 전환) |
| `tools/eval/` | 제공 전사본과 라이브 전사의 오프라인 CER 평가 자료 |
| `docs/PLAN.md` | 아키텍처 플랜 및 기술 결정 (이 파일) |
| `docs/WALKTHROUGH.md` | 세션별 개발 빌드 히스토리 (한국어) |
| `docs/TECHNICAL.md` | 코드 레벨 기술 참고서 (한국어) |
| `docs/BUILD_EXE.md` | 단일 exe 빌드 시도 기록 (한국어) |
| `logs/ops.log` | 운영 로그: 서버 시작/종료, 오디오, 일시정지/재개, 재연결 (INFO+) |
| `logs/session.log` | Gemini 세션 로그: 연결, `[KO]` 원문, `[EN turn]` 번역 (DEBUG+) |
| `logs/sessions/YYYYMMDD_HHMMSS/` | 세션별 전사본 (`transcript.md`, `transcript.jsonl`, `summary.txt`, `session.json`) |

---

## 5. 신뢰성 요구사항

| 시나리오 | 동작 |
|----------|------|
| 정상 9분 WebSocket 경계 | GoAway → 자동 재연결 → 2~3초 내 자막 재개 |
| 인터넷 장애 | 지수 백오프 (2초, 4초, 8초), 3회 실패 시 FAILED 상태 |
| FAILED 상태 | 모든 참석자 SSE에 "번역 불가" 이벤트 전송 |
| 오디오 장치 연결 해제 | 스트림 읽기 오류 시 `AudioStatus.DISCONNECTED` |
| 장시간 묵음 (>10초) | `AudioStatus.NO_SIGNAL` — 장치 해제와 구별 |
| 참석자 WiFi 끊김 | SSE 자동 재연결; 기기에 "재연결 중..." 표시 |

---

## 6. 단계별 개발 현황

- [x] 2026-11-08 현장 검토 반영: 단일 운영자 화면, 고정 모델, 원시 입력 MP3 백업, 이름 교정, 입력 언어에 따른 출력 보호를 구현했습니다. 기존 라이브 전사 기준 CER는 18.0%/19.1%이며 새 버전의 실음성 정확도 개선은 미검증입니다. [작업 결과](single_operator_results.md), [평가](evaluation.md), [추가 정리 승인표](single_operator_audit.md)를 참고하세요.

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
| 15 | 제한된 자동 복구(Auto-Restart) 루프, 예외 수신 상세화, 운영자 경고 알림 및 GoAway 재연결 처리 완료 (27분 설명은 2026-11-02 정정, `/docs/TECHNICAL.md`) | ✅ 완료 |
| 16 | mDNS 호스트네임 광고 (`python-zeroconf`), 동적 URL 리졸버, 운영자 콘솔 주/비상용 접속 주소 표시 | ✅ 완료 |
| 17 | UI 외부 템플릿 리팩토링: `attendee.html` 및 `operator.html`을 `server.py`에서 분리하고, 개발 환경 실시간 핫 리로드를 위한 동적 로더 구현 | ✅ 완료 |
| 18 | 운영자 콘솔 UX: 고정 헤더, 상태 스트립 헤더 통합, 반응형 타이틀, 6열 통계 그리드, 전체 요소 툴팁, 사용 가이드 카드. 참석자 화면: 한국어 탭 보기 (백엔드 `ko` 필드), 단락 그루핑, 타임스탬프 제거, 폰트 범위 20–40px. GoAway 로그 INFO 레벨 수정. 운영자 미리보기 중복 텍스트 버그 수정. | ✅ 완료 |
| 19 | 독립 설정 마법사 GUI (`setup_gui.py` / `SKC_setup.exe`) 및 2개 실행 파일 배포 아키텍처 구현 | ✅ 완료 |
| 20 | 과거 모델 선택·LKG·폴백 구현은 제거하고 `gemini.model` 직접 사용으로 대체 | ✅ 제거 완료 |
| 21 | 수동 Pause/Resume 세션 경계와 세대 격리 유지. 원문 언어에 따른 자동 리셋·자막 숨김·음소거는 사용하지 않음 | ✅ 현재 동작 반영 |
| 22 | 운영자 콘솔 컨트롤 바 분리 및 일시정지 알림: 3열 고정 레이아웃, `role="status"` 및 `aria-live="polite"` 접근성 상태 배지, 일시정지 경과 타이머, 3분 경과 주황색 강조 알림, `prefers-reduced-motion` 지원 및 비활성 중립 회색 종료 버튼 | ✅ 완료 |
| 23 | HTTPS 공인 접속 및 보안 강화 (Release v3.0.0): Cloudflare Named Tunnel 백그라운드 매니저, PublicHostGuard 미들웨어, 운영자 HMAC-SHA256 세션 인증, 네트워크/AI 지연 분리 텔레메트리, 2열 데스크톱 설정 마법사 (1060x670), 병렬 멀티스레드 빌더 (`build_parallel.py`) 및 포트 8080 표준화 | ✅ 완료 |
| 25 | 텔레메트리 청취자 정합성 & 운영자 영문 현지화 완전성 (Release v3.2.1): 실시간 텔레메트리 WebSocket 연결 즉시 핑 송신 (10초 블라인드 스팟 제거), 0ms 서브 밀리초 로컬 RTT 수용, 단일 라벨 호스트명 및 로컬 도메인 현장 Wi-Fi 라우트 분류 정상화, 모바일 백그라운드 타이머 스로틀링 마진 보장, 6개 세션 메트릭 대시보드 및 툴팁 100% 한/영 이중 언어 지원 | ✅ 완료 |
| 26 | 텔레메트리 불변식 보장, GoAway 예외 언래핑 & 운영자 콘솔 레이아웃 정밀화 (Release v3.2.2): 모바일 타이머 스로틀링 120초 마진 및 `local + public + unknown == total` 불변식 보장, Python 3.11 `ExceptionGroup` 재귀 언래핑 및 누적 재연결 카운터, 운영자 콘솔 `en` / `한` 간결 토글 및 반응형 폭 조절 (1024px에서 좌측 1인치 축소), 템플릿 한글 전수 검사 `test_bilingual_parity.py` 자동화 | ✅ 완료 |
| 27 | 당시 음악/발화 분류와 YouTube 챕터 구현. 11월 8일 현장 오류 확인 후 관련 코드·UI·내보내기 제거 | ✅ 제거 완료 |
| 28 | 주석 보존 YAML, 네트워크 분리, 적응형 로컬 모드 유지. 어휘 힌트·검토·입력 언어 UI는 제거 | ✅ 현재 동작 반영 |
| 검증 | 과거 단계별 테스트 기록은 [WALKTHROUGH.md](WALKTHROUGH.md), 현재 검증 결과는 [CHANGELOG.md](../CHANGELOG.md) 참고 | 기록 분리 |

---

## 7. 설정 가이드 (Configuration)

```yaml
audio:
  device_index: 2         # 설정된 입력 믹서 장치 번호
  sample_rate: 16000      # 전송은 16 kHz, 실제 캡처 포맷은 장치 지원값으로 결정
  channels: 1
  chunk_ms: 100
  auto_stop_timeout_min: 10

gemini:
  model: gemini-3.5-live-translate-preview
  voice: "orus"
# 교회 내부 Wi-Fi (현장 접속용)
intranet:
  host: 0.0.0.0
  port: 8080
  hostname: skc.local

# 공용 인터넷 접속용 (Cloudflare 터널)
internet:
  enable_tunnel: false
  public_url: ""

translation:
  supported_targets: [en, ko, zh, es]
  default_active_targets: [en]

logging:
  log_dir: logs
  max_bytes: 10485760   # 10 MB
  backup_count: 5
```

---

## 8. 향후 확장 계획 (Future Phases)

### Phase 23 — Public HTTPS / Cloudflare Named Tunnel 및 보안 통합 (Release v3.0.0) [✅ 완료]
- **완료 일자**: 2026-08-31
- **주요 산출물**: Cloudflare Named Tunnel 매니저, PublicHostGuard 보안 경계, 운영자 HMAC 인증, 텔레메트리 RTT 분리, 2열 GUI, 병렬 빌더.

### Phase 24 — 다국어 / 다중 타겟 동시 통역 & 런타임 안정화 (Release v3.1.0) [✅ 완료]
- **완료 일자**: 2026-09-02
- **주요 산출물**:
  1. **TranslationManager 팬아웃 파이프라인 (`app/translation_manager.py`)**: 단일 오디오 캡처에서 타겟별 독립 `GeminiSession` 및 `CaptionBroadcaster`로 비동기 팬아웃.
  2. **표준 70+ 언어 카탈로그 (`app/languages.py`)**: 네이티브 명칭 매핑 및 동적 활성 언어 동기화 (`GET /api/languages`).
  3. **지속형 참석자 UI (`app/templates/attendee.html`)**: 헤더 내 언어 선택기 및 오디오 토글 영구 배치, 3+ 타겟 확장 시 CSS 오버랩 방지, 이어폰 동의 클릭 내 동기식 `AudioContext.resume()` 실행.
  4. **방향성 장애 격리 (Directional Failure Isolation)**: 주 타겟 장애 시 보조 타겟 통역 유지, 보조 타겟 장애 시 주 타겟 및 운영자 발화 프리뷰 유지.
  5. **자발적 셧다운 수명주기 (`app/server.py`, `main.py`)**: `shutdown_event` 및 `SKCUvicornServer`를 통한 SSE/WS 스트림 0.5초 조기 종료로 Ctrl+C ASGI Traceback 제거.
  6. **오프라인 린팅 & 패키징 (`eslint.config.mjs`, `SKC_translation.spec`)**: 로컬 `eslint: 10.9.1` 기반 `no-undef` 검증, `jinja2` 패키징 보완.

### Phase 25 - 입력 언어 선택 UI [현재 제거]
- 과거 입력 언어 선택 UI는 제거했습니다. 현재는 출력 언어만 화면에서 선택하며 원문 언어는 자동 감지합니다. 기존 `translation.expected_source_languages`는 무시합니다. 과거 구현 이력은 [WALKTHROUGH.md](/docs/WALKTHROUGH.md)에 보존합니다.

### Phase 26 — 텔레메트리 불변식 보장, GoAway 예외 언래핑 & 다국어 검증 자동화 (Release v3.2.2) [✅ 완료]
- **완료 일자**: 2026-09-21
- **주요 산출물**:
  1. **텔레메트리 120초 마진 및 불변식 보장**: 모바일 백그라운드 타이머 스로틀링(60초) 대응 120초 TTL (2:1 마진) 적용, `local + public + unknown == total` 덧셈 항등식 보장.
  2. **Python 3.11 `ExceptionGroup` 재귀 언래핑**: `asyncio.TaskGroup` 예외 계층에서 GoAway 및 1000 정상 종료 코드를 재귀 감지하여 누적 재연결 카운터 보존.
  3. **템플릿 한글 전수 검사 자동화 (`tests/test_bilingual_parity.py`)**: 모든 템플릿 텍스트의 한/영 이중 언어 태그 누락 방지.

### Phase 27 및 28 - 현장 검토 후 단순화 [현재 반영]
- [x] 2026-11-08 단일 운영자용 고정 모델 통역, 연속 MP3 백업과 상시 진단을 통합했습니다. 결정 이력은 [WALKTHROUGH.md](/docs/WALKTHROUGH.md), 현재 동작은 [TECHNICAL.md](/docs/TECHNICAL.md), 보류 항목은 아래 계획과 [기능 감사](/docs/single_operator_audit.md)에 있습니다.

### 보류: Live Translate의 어휘·원문 언어 힌트
- Google이 번역 모델용 어휘·원문 언어 설정을 공식 지원하거나 새 지원 모델을 출시하면 재검토합니다. 복구 커밋과 채택 기준은 [어휘·언어 힌트 보관 문서](/docs/archive/어휘_언어힌트_보관.md)에 있습니다.

### 자동 재연결의 문맥 유지
- [x] 2026-11-09 B1을 구현하고 전체 예배의 8회 재개·후반 발화 품질을 확인했습니다. 남은 송영 오인식과 적용 범위는 [검증 보고서](/docs/b1_full_service_validation.md), 결정 이력은 [WALKTHROUGH.md](/docs/WALKTHROUGH.md)에 있습니다.

### 다음 주일 현장 검토
- [ ] 실제 예배 후 시작 시간·연결 안정성·통역 오류·녹음 보존을 함께 검토합니다. 현재 배포본과 검증 범위는 [빌드 기록](/docs/BUILD_EXE.md)을 기준으로 하며, 새 유료 평가나 추가 기능은 현장 결과를 확인한 뒤 논의합니다.

### 보류: 음악 분류기와 자동 일시정지
- B1을 우선 적용하며 분류기·상태 배지는 복구하지 않습니다. 향후 평가는 오디오만 사용하고 실제 노트북의 CPU·로딩·정확도를 먼저 측정합니다. 단일 작업 스레드, ONNX Runtime의 순차 실행·스피닝 제어, 늦은 창 건너뛰기와 캡처·녹음 격리는 [보존한 평가 조건](/docs/b1_full_service_validation.md)을 따릅니다.

### 보류: YouTube 오디오 누락 때만 사용하는 보조 복구
- 실시간 통역이 주 기능입니다. 21일 보관 MP3는 YouTube 영상에서 소리가 빠졌을 때 해당 구간을 더빙·정렬하는 백업이며, 사후 전사는 필요한 경우에만 별도로 요청합니다. 예배 후 자동 전사나 새 운영자 기능을 만들지 않습니다.
- 설교 CER 18.0% → 12.1%의 관측은 후속 검토의 출발점으로 보존합니다. 전체 예배의 누락 문제 때문에 실시간 전사를 대체하지 않으며, 향후 필요한 설교·누락 구간을 대상으로 검토합니다. 기존 [평가 스크립트·결과](/docs/evaluation.md)를 재사용하고 유료 실행은 먼저 비용 승인을 받습니다.

### Phase 29 - 터널링 초기 설정 자동화 (Automated Cloudflare Tunnel Setup & Zero-Config Provisioning) [보류]
- 현재 배포는 현장 Wi-Fi를 기본으로 하며 고정 도메인·터널이 준비된 교회만 공용 HTTPS를 설정합니다. 아래는 보존한 과거 제안이며, 임시 QR 주소와 자동 프로비저닝은 현재 구현 범위에 포함하지 않습니다. 사용자가 다시 요청할 때 검토합니다.
- **목표**: Cloudflare Zero Trust 대시보드를 수동으로 탐색하여 토큰을 발급받는 복잡한 초기 진입 장벽을 제거하고, 설정 마법사(`SKC_setup.exe`) 및 초기 구동 시 터널 생성 및 공용 HTTPS 연결을 원클릭으로 자동화.
- **주요 계획 항목**:
  1. **원클릭 터널 프로비저닝 (Cloudflare API 연동)**:
     - Cloudflare API 토큰 또는 간이 인증을 통해 사용자가 웹 대시보드를 직접 조작하지 않고도 터널 생성, 인증 토큰 획득, CNAME 호스트 라우팅 등록을 `SKC_setup.exe`에서 자동 수행.
  2. **Zero-Config 임시 터널 폴백 (Quick Tunnel / TryCloudflare)**:
     - 영구 도메인/토큰이 아직 설정되지 않은 새 환경에서도 즉시 외부 접속 테스트가 가능하도록, Cloudflare Quick Tunnel(`*.trycloudflare.com`) 임시 공용 HTTPS 프로비저닝 및 동적 QR 코드 생성 지원.
  3. **사전 진단 및 연결 헬스체크 자동화**:
     - 터널 프로세스 실행 전 방화벽 포트 차단, DNS 전파 상태, 로컬 8080 포트 응답성을 3초 이내에 사전 점검(Pre-flight Diagnostics)하여 사용자 친화적 해결 가이드 제공.






