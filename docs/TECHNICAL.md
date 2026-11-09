# 기술 참고서
### 실시간 예배 번역 시스템

이 문서는 본 시스템의 핵심 구성요소별 구현 방식을 코드 수준에서 설명합니다. 새 기능을 추가하거나 기존 동작을 수정하는 개발자를 위한 참고서입니다.

---

## 📌 목차
1. [데이터 흐름 전체 개요](#1-데이터-흐름-전체-개요)
2. [FastAPI 서버 구조](#2-fastapi-서버-구조)
3. [운영자 이벤트 로그](#3-운영자-이벤트-로그)
4. [Gemini Live API 세션](#4-gemini-live-api-세션)
5. [오디오 캡처 파이프라인](#5-오디오-캡처-파이프라인)
6. [SSE 자막 브로드캐스트](#6-sse-자막-브로드캐스트)
7. [Web Audio API 실시간 재생](#7-web-audio-api-실시간-재생)
8. [자막 커밋 전략](#8-자막-커밋-전략)
9. [이름 교정과 전사 평가](#9-이름-교정과-전사-평가)
10. [Asyncio 패턴 요약](#10-asyncio-패턴-요약)
11. [확장 가이드](#11-확장-가이드)
12. [Cloudflare Named Tunnel 및 Windows 서비스 관리](#12-cloudflare-named-tunnel-및-windows-서비스-관리)
13. [실시간 텔레메트리 및 청취자 통계 정합성 (Network Telemetry & Listener Tracking)](#13-실시간-텔레메트리-및-청취자-통계-정합성-network-telemetry--listener-tracking)

---

## 1. 데이터 흐름 전체 개요

```text
마이크/믹서 → AudioCapture (실제 장치 포맷, PCM16, 단일 캡처)
                 ├─ 모노 변환 전 원시 PCM → bounded 녹음 큐 → MP3 writer
                 │                            일시정지 중에도 계속 기록
                 └─ 모노 변환 → 필터·16 kHz 리샘플링
                                   ↓
                          TranslationManager
                       출력 언어별 독립 GeminiSession
                                   ↓
                    설정된 gemini.model로 Live 연결
                    입력/출력 전사 + 번역 PCM 수신
                       ├─ 원문 언어는 전사 라벨·로그에만 기록
                       └─ 언어 정보와 무관하게 자막·번역 음성 전달
                                   ↓
                     언어별 CaptionBroadcaster
                       ├─ SSE /stream?lang=... → 자막
                       └─ WS /audio-stream?lang=... → Web Audio
```

원시 녹음과 번역은 별도 경로입니다. 번역을 일시정지해도 캡처·녹음은 유지하며, 녹음 writer 오류가 번역 경로를 중단하지 않습니다. 장치별 입력 채널 RMS와 L/R 상관관계는 로그로만 기록합니다.

---

## 2. FastAPI 서버 구조

### 라이프사이클 (Lifespan)
`app/server.py`는 FastAPI `lifespan` 컨텍스트 매니저로 서버 시작/종료를 관리합니다.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작: 설정 로드, 매니저 초기화. 모델 탐색·검증 연결은 실행하지 않음
    yield
    # 종료: 번역 파이프라인 정지 및 세션 로그 저장
```

서버의 `TranslationManager`가 단일 오디오 캡처와 출력 언어별 `GeminiSession`·`CaptionBroadcaster`를 관리합니다. 주 언어 상태와 각 출력 언어의 상태를 `/api/status`에서 함께 제공합니다.

### 주요 라우트 구조

| 라우트 | 유형 | 반환값 | 역할 |
|--------|------|--------|------|
| `GET /` / `GET /admin` | HTTP | `HTMLResponse` | 운영자 콘솔 (`app/templates/operator.html`에서 서빙) |
| `GET /live` | HTTP | `HTMLResponse` | 참석자 자막 페이지 (`app/templates/attendee.html`에서 서빙) |
| `GET /stream` | SSE | `EventSourceResponse` | 자막 이벤트 스트림 |
| `WS /audio-stream` | WebSocket | binary frames | 24kHz PCM16 오디오 |
| `WS /ws/telemetry` | WebSocket | JSON frames | 참석자 레이턴시 (RTT) 및 접속자 실시간 텔레메트리 |
| `GET /api/status` | HTTP | JSON | 시스템 상태 스냅샷 (`pause_duration_s`, 지연 통계 포함) |
| `POST /api/reconnect-public` | HTTP | JSON | Cloudflare 퍼블릭 링크 즉시 재연결 검사 |
| `GET /api/auth/status` | HTTP | JSON | 운영자 인증 활성화 및 로그인 상태 |
| `POST /api/auth/login` | HTTP | JSON | 운영자 비밀번호 검증 및 HMAC-SHA256 세션 쿠키 발급 |
| `POST /api/auth/logout` | HTTP | JSON | 세션 쿠키 무효화 및 로그아웃 |
| `POST /api/start` | HTTP | JSON | 번역 파이프라인 시작 (인증 필요) |
| `POST /api/stop` | HTTP | JSON | 파이프라인 정지 + 로그 저장 (인증 필요) |
| `POST /api/pause` | HTTP | JSON | 번역 세션 일시정지 + 번역 오디오 큐 비우기, 입력 녹음 유지 (인증 필요) |
| `POST /api/resume` | HTTP | JSON | 신규 Gemini 컨텍스트로 일시정지 해제 (인증 필요) |
| `POST /api/models/test` | HTTP | JSON | 설정된 모델의 수동 연결 테스트, 모델 선택값 저장 없음 |
| `POST /api/config/auto-stop` | HTTP | JSON | 자동 종료 타임아웃(분) 설정 |
| `GET /api/events?since=N` | HTTP | JSON | 운영자 이벤트 증분 폴링 |

### PublicHostGuard 미들웨어 & 보안 경계
`app/server.py`의 `PublicHostGuardMiddleware`는 공인 Cloudflare 터널 호스트네임으로 유입되는 모든 요청에 대해 기본 거부(Default-Deny) 보안 경계를 적용합니다. `/admin`, `/api/start`, `/api/devices` 등 운영자 및 제어 엔드포인트는 404를 반환하고, 오직 참석자 엔드포인트(`/live`, `/stream`, `/audio-stream`, `/ws/telemetry`, 정적 에셋)만 허용합니다. 루트(`/`) 요청은 자동으로 `/live`로 리다이렉트됩니다.

### 외부 HTML 템플릿 서빙
HTML 파일은 `app/templates/operator.html` 및 `app/templates/attendee.html`로 분리 관리됩니다. 동적 템플릿 로더를 통해 개발 환경에서는 서버 재시작 없이 실시간 수정(Hot-Reload)을 지원하며, PyInstaller 단일 실행 파일 배포 시에는 캐시된 에셋을 고속 서빙합니다.

---

## 3. 운영자 이벤트 로그

`app/events.py` — `OperatorEventLog`

### 목적
운영자용 이벤트(Gemini 연결 완료, 참석자 접속 등)와 개발자 로그(회전 파일 `ops.log`/`session.log`)를 분리합니다. 봉사자는 대시보드의 구조화된 이벤트를 보고, 개발자는 로그 폴더의 회전 파일을 확인합니다.

### 구현

```python
class OperatorEventLog:
    def __init__(self, maxlen=50):
        self._deque = deque(maxlen=maxlen)   # 링 버퍼 — 가장 오래된 항목 자동 제거
        self._lock = threading.Lock()        # 오디오 스레드 + asyncio 공유
        self._next_id = 0

    def add(self, category, message, details=None):
        with self._lock:
            event = {"id": self._next_id, "ts": time.time(),
                     "category": category, "icon": CATEGORY_ICONS[category],
                     "message": message, "details": details or {}}
            self._deque.append(event)
            self._next_id += 1

    def since(self, last_id):
        with self._lock:
            return [e for e in self._deque if e["id"] > last_id]
```

`app/audio.py`가 별도 스레드에서 실행되므로 `asyncio.Lock` 대신 `threading.Lock`을 사용합니다.

### 카테고리

| 카테고리 | 아이콘 | 발생 시점 |
|----------|--------|---------|
| `success` | 🟢 | Gemini 연결 완료, 시스템 시작 |
| `audio` | 🔵 | 오디오 장치 연결, 신호 복구 |
| `gemini` | 🟣 | 세션 시작/종료, 연결 중 |
| `network` | 🟡 | 재연결 시도, GoAway |
| `user` | 👤 | 참석자 접속/이탈, 일시정지/재개 |
| `warning` | ⚠️ | 신호 없음 감지 |
| `error` | 🔴 | 장치 연결 해제, 최대 재시도 초과 |

### `/api/events` 엔드포인트

프런트엔드가 1.5초마다 `since=lastEventId`로 폴링하여 새 이벤트를 추가합니다. DOM은 최대 50개로 트림됩니다.

---

## 4. Gemini Live API 세션

### 고정 연결 설정

`config.yaml`의 `gemini.model`을 모든 최초 연결·재연결·수동 재개에 그대로 사용합니다. 시작 시 모델 목록 조회나 테스트 연결을 수행하지 않으며 자동 대체 모델도 없습니다. 실제 `_build_config()`의 핵심은 다음과 같습니다.

```python
types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    translation_config=types.TranslationConfig(
        target_language_code=self.target_language_code,
        echo_target_language=True,
    ),
    speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="orus"),
    )),
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
            prefix_padding_ms=200,
            silence_duration_ms=700,
        ),
    ),
    context_window_compression=types.ContextWindowCompressionConfig(
        sliding_window=types.SlidingWindow(),
    ),
)
```

입력 전사에는 언어·어휘 힌트를 넣지 않습니다. 시스템 지시문과 세션 복구 핸들도 사용하지 않습니다. 위 예시의 보이스는 실제로 `gemini.voice`에서 읽으며 기본값은 `orus`입니다. 힌트 제거와 기준 전사 검증의 판단 근거는 [STACK.md](STACK.md), [evaluation.md](evaluation.md)를 참고하세요.

### 입력 언어 메타데이터와 출력

응답의 입력 전사 `language_code`를 전사 라벨과 로그에 기록합니다. 원문 언어 목록으로 출력을 제한하지 않으며 기존 `translation.expected_source_languages`는 읽어 적용하지 않습니다.

- 중국어 등 다른 원문 언어도 수신한 자막·합성 음성·전사를 그대로 전달합니다.
- 언어 정보가 늦거나 없으면 자막·음성을 기다리게 하지 않습니다. 완료 때도 코드가 없으면 전사에 `unknown`으로 기록합니다.
- 언어가 바뀌어도 자동 음소거·자막 숨김·세션 리셋을 하지 않습니다. 수동 Pause/Resume의 세대 격리는 유지합니다.

언어 코드만으로 정상 외국어와 오인식을 구별할 수 없으므로 인식 오류도 전달될 수 있습니다. `/api/status`와 `translation.sessions[언어]`의 `output_hidden`, `audio_muted`는 기존 클라이언트 호환을 위해 남기며 원문 언어 때문에 켜지지 않습니다.

### 연결 오류와 재연결

연결에 성공하면 일반 연결 오류의 실패 시도 횟수를 0으로 초기화합니다. 일반 연결 오류에는 제한된 지수 백오프를 사용하며 3회 실패하면 `FAILED`로 전환합니다. `ValueError`, `TypeError`, 지정 모델의 404/NOT_FOUND/1008 오류는 모델명을 포함한 명확한 실패로 처리하며 다른 모델로 전환하지 않습니다.

HTTP 429, `RESOURCE_EXHAUSTED`, 결제·할당량 오류는 별도 3회 실패 한도를 사용합니다. 2초·5초 뒤 재시도하고 세 번째 오류에서 중단하므로, 연결 직후 같은 오류가 나도 무한 반복하지 않습니다. `/api/status`의 `session.error_kind`와 언어별 `translation.sessions[언어].error_kind`가 `quota`이면 화면에 지출 한도·할당량·요청 빈도 제한의 가능성을 한영으로 표시합니다. 오류 코드만으로 월 지출 한도와 일시적 요청 제한을 확정하지는 않습니다. 서비스 종료 후 결제·할당량을 확인하고 다시 시작합니다.

`GoAway`는 정상 연결 교체 신호로 구분해 실패 횟수 증가 없이 0.2초 뒤 같은 모델에 재연결합니다. 2026-11-01 기록 5건에서는 연결당 약 9분에 관찰됐으며, 이는 공급자의 고정 보장 시간이 아닙니다. 재연결은 새 컨텍스트를 엽니다.

일반 연결 오류의 제한된 재시도가 소진되면 서버의 `_auto_stop_on_failure`가 2초·5초·15초 간격으로 최대 3회 복구를 시도합니다. 설정 오류와 `quota` 오류는 이 반복 복구 대상에서 제외합니다. 해당 출력 언어의 장애는 다른 출력 언어와 단일 입력 캡처를 중단하지 않습니다. 실패한 세션의 PCM 큐는 더 채우지 않으며, 모든 출력이 실패하면 추정 과금 시간도 멈춥니다. MP3 녹음은 서비스 종료 전까지 계속합니다.

### 수동 일시정지와 재개

`Pause`는 번역 큐를 비우고 Gemini 연결을 닫습니다. 마이크 캡처와 입력 MP3 녹음은 계속합니다. `Resume`은 같은 모델로 새 컨텍스트를 열고 epoch를 증가시켜 이전 응답을 폐기합니다. 이 조작이 오인식을 반드시 교정한다고 보장하지 않습니다.

---

## 5. 오디오 캡처 파이프라인

`AudioCapture`가 장치를 한 번 열어 실제 샘플레이트·채널 수의 little-endian PCM16을 읽습니다. 가능한 장치에서는 네이티브 16 kHz 모노를 우선 사용하고, 그렇지 않으면 지원 포맷을 사용합니다. 녹음을 위해 기존 캡처 포맷 선택 방식을 바꾸지 않았습니다.

```text
장치 원시 PCM16
   ├─ WeeklyRecording → bounded 큐 → lameenc MP3 (약 128 kbps CBR)
   └─ 다채널이면 모노 평균 → 필요하면 Resampler → 16 kHz 모노 PCM16
                                                   ↓
                                  출력 언어별 Gemini 오디오 전송 큐
```

리샘플러는 4차 Butterworth 저역통과 필터와 청크 간 위상을 유지하는 선형 보간을 사용합니다. 네이티브 16 kHz이면 리샘플러를 건너뜁니다. 전송은 항상 `audio/pcm;rate=16000`입니다.

### MP3 기록과 보관

`logs/recordings/current_week/YYYY-MM-DD_HHMM.mp3`에 캡처 채널 수와 샘플레이트를 유지해 저장합니다. 네이티브 16 kHz 장치면 MP3도 16 kHz이며 원본에 없는 고주파 정보를 추가하지 않습니다. 시간당 용량은 약 58 MB입니다.

writer는 번역과 별도이며 녹음 큐는 크기를 제한합니다. 디스크·인코더 오류가 번역 전송을 막지 않도록 격리합니다. 큐 누락과 250 ms를 넘는 캡처 공백은 무음 및 녹음 메타데이터로 남깁니다. 번역 일시정지 동안에도 실제 캡처 입력을 기록합니다.

새 녹음 시작 시 21일보다 오래된 생성 녹음을 정리합니다. 달력상 주별 교체가 아닙니다. 잠긴 오래된 파일은 경고를 기록하고 넘어가며 현재 녹음을 계속합니다. 전사 보관 폴더와 사용자가 따로 복사한 파일은 별개입니다.

### 입력 진단과 레벨 미터

레벨 미터는 PCM의 RMS를 dBFS로 환산합니다. 장시간 무신호와 연결 해제를 구분합니다. 모노 변환 전의 채널별 RMS dBFS, 스테레오 L/R 상관관계를 캡처 시작 및 60초마다 로그에 남깁니다. 진단이 입력 채널이나 모드를 자동 변경하지는 않습니다.

---

## 6. SSE 자막 브로드캐스트

### 이벤트 종류

| 이벤트 | `data` 형식 | 수신 대상 | 설명 |
|--------|------------|---------|------|
| `update` | `target`/`text` 델타 | 참석자+운영자 | 허용된 현재 줄 갱신 |
| `commit` | 확정 원문·번역 | 참석자+운영자 | 로컬 이름 교정 후 두 텍스트를 교체·확정 |
| `source` | 원문 델타 | 참석자+운영자 | 허용된 원문 갱신 |
| `hidden` | 상태 신호 | 참석자+운영자 | 현재 미확정 줄만 제거 |
| `visible` | 상태 신호 | 참석자+운영자 | 자막 재개 상태 알림 |
| `audio_clear` | 상태 신호 | 참석자+운영자 | 재생 중·예약된 오디오 노드와 큐 비우기 |
| `unavailable` | 상태 신호 | 참석자+운영자 | 번역 불가 알림 |
| `ping` | 상태 신호 | 참석자+운영자 | 15초 keepalive |
| `paused` / `resumed` | 상태 신호 | 참석자+운영자 | 일시정지·재개 알림 |

스트림은 `CaptionEvent.kind`를 포함하는 JSON으로 전달합니다. 현재 줄은 델타로 보이고 `commit`에서 교정된 원문과 번역을 모두 덮어씁니다. 세션 시작 전에 확정된 이전 자막을 언어 이탈 알림으로 지우지 않습니다.

### 팬아웃 구조

`CaptionBroadcaster`는 SSE 클라이언트별 큐와 오디오 WebSocket 클라이언트 목록을 관리합니다. `CaptionEvent`를 각 SSE 큐에 넣고 오디오 바이트는 별도로 전송합니다. 각 클라이언트가 독립 큐를 가지므로 느린 클라이언트가 다른 클라이언트를 막지 않습니다.

---

## 7. Web Audio API 실시간 재생

### 서버 → 브라우저 흐름

```
GeminiSession  →  on_audio_chunk(pcm_bytes)
    ↓
CaptionBroadcaster.on_audio_chunk()
    ↓
websocket.send_bytes(pcm_bytes)  (각 /audio-stream 클라이언트)
    ↓
[브라우저 JavaScript]
ws.onmessage = (e) => scheduleAudioChunk(e.data)
```

### 브라우저 PCM16 → Float32 변환

```javascript
// ArrayBuffer (Int16, 24kHz, mono) → Float32Array
const int16 = new Int16Array(arrayBuffer);
const float32 = new Float32Array(int16.length);
for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768.0;  // 정규화 [-1, 1]
}
```

### 오디오 버퍼 큐잉

```javascript
function scheduleAudioChunk(float32Data) {
    const buffer = audioCtx.createBuffer(1, float32Data.length, 24000);
    buffer.getChannelData(0).set(float32Data);
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(audioCtx.destination);
    // 이전 청크 종료 시점 이후에 시작 (끊김 없는 연속 재생)
    nextStartTime = Math.max(audioCtx.currentTime, nextStartTime);
    source.start(nextStartTime);
    nextStartTime += buffer.duration;
}
```

`audio_clear`를 받으면 추적 중인 `AudioBufferSourceNode`를 정지하고 예약 목록과 다음 재생 시각을 초기화합니다. 새 오디오 판정 버퍼를 추가하지 않으므로 평상시 지연은 늘어나지 않습니다. 이미 재생된 소리를 취소할 수는 없습니다.

iOS Safari는 사용자 인터랙션(탭) 없이 `AudioContext`를 생성할 수 없습니다. "오디오 듣기" 버튼 클릭 시에 `AudioContext`를 초기화하는 이유입니다.

---

## 8. 자막 커밋 전략

Gemini 턴 기록과 브라우저 자막 줄 확정은 구분합니다.

- `GeminiSession`은 `turn_complete`를 받거나 새 텍스트가 1.5초간 없을 때 턴을 완료합니다. 원문 언어와 관계없이 이름 교정 후 전사 목록에 보관하며, 언어 정보가 없으면 `unknown`으로 기록합니다.
- `CaptionBroadcaster`는 마지막 번역 델타 이후 1.5초가 지나면 줄을 확정합니다. 연속 발화로 150자를 넘으면 `_find_split()`이 문장부호나 공백 경계를 찾아 먼저 확정합니다.
- 확정 시 원문·번역에 같은 `NameCorrector`를 적용합니다. 클라이언트는 `commit`의 원문·번역을 모두 교체하므로 스트리밍 중 보였던 오기가 확정 자막에 남지 않습니다. 합성 음성은 수정하지 않습니다.
- `hidden` 상태로 전환하면 아직 확정하지 않은 줄과 타이머를 폐기합니다. 먼저 확정되어 송출된 내용은 되돌리지 않습니다.

---

## 9. 이름 교정과 전사 평가

`app/text_corrections.py`의 `NameCorrector`는 `config/name_corrections.yaml`의 치환표를 읽습니다. 유지보수자가 확인한 오기만 직접 등록하고 프로그램을 다시 시작합니다.

```yaml
스타필드 한인 교회: 스타크빌 한인교회
Starfield Korean Church: Starkville Korean Church
카미카제 자매: 카미카 자매
Kamikaze sister: Kamika
```

긴 항목을 먼저 매칭하며 ASCII 항목은 단어 전체가 일치할 때만 치환합니다. 정확 일치이므로 대소문자나 표기가 다르면 별도 항목이 필요합니다. 자동 단어 추출, 사후 검토 모달, 모델 프롬프트 반영은 없습니다. `collapse_repetitions()`는 단어나 짧은 구절이 5회 이상 연속 반복된 경우 한 번으로 축약합니다.

교정은 확정 원문·번역 자막과 저장 전사에만 적용합니다. 잘못 인식된 Google 원시 응답, 개발자 로그, 번역 합성 음성을 고치거나 모델을 훈련하지 않습니다.

### 기존 전사 기준과 다음 검증

제공된 두 기계 전사본 대비 기존 라이브 발화 CER는 각각 18.0%와 19.1%입니다. 두 기준도 서로 차이가 있으므로 사람이 확인한 정답으로 간주하지 않습니다. 재현 명령, 승인된 사후 전사 평가 결과, 시간 정렬·의미 평가의 한계는 [evaluation.md](evaluation.md)에 정리했습니다. 사후 전사는 설교에서 개선됐지만 전체 발화 누락이 있어 실시간 통역을 대체하지 않습니다. 새 코드의 실시간 전사 개선 여부는 같은 오디오와 사람이 확인한 구간으로 추가 검증해야 합니다.

---

## 10. Asyncio 패턴 요약

### 세션 내 태스크 구조

`_run_session()`은 `asyncio.TaskGroup` 안에서 오디오 전송, 응답 수신, 자동 턴 커밋의 세 태스크를 실행합니다. 정지 이벤트와 epoch를 확인하며, 수신 종료나 예외 시 연결 컨텍스트를 종료하고 재시도 정책으로 넘깁니다.

### 주요 동기화 프리미티브

| 프리미티브 | 위치 | 역할 |
|-----------|------|------|
| `asyncio.Queue(maxsize=500)` | `_audio_queue` | 오디오 청크 백프레셔 |
| `asyncio.Queue()` (per-client) | SSE 브로드캐스트 | 클라이언트 격리 |
| `asyncio.Event` | `_stop_event` | 정지 신호 전파 |
| `asyncio.wait_for(timeout)` | SSE 제너레이터 | keepalive 핑 트리거 |

### 백프레셔 정책

- `_audio_queue.put_nowait()` — 큐 가득 차면 청크 **drop** (블로킹 안 함)
- SSE `q.put_nowait()` — 큐 가득 차면 해당 클라이언트 이벤트 **drop**
- 오디오 WS `send_bytes()` — 예외 발생 시 해당 클라이언트 제거

---

## 11. 확장 가이드

### 새 REST 엔드포인트 추가

```python
# app/server.py 내부
@app.post("/api/my-endpoint")
async def my_endpoint():
    # 기존 인증·PublicHostGuard 경계를 적용하고 매니저를 통해 작업
    return {"ok": True}
```

### 새 SSE 이벤트 종류 추가

1. `CaptionEvent.kind`에 새 종류를 추가하고 기존 `_push()` 경로로 전송합니다.
2. 참석자와 운영자 JavaScript의 `EventSource.onmessage`에서 JSON의 `kind`를 처리합니다.
3. 출력 언어별 스트림 분리, 현재 줄 정리, 두 화면의 한국어·영어 문구를 함께 확인합니다.

### 이름 교정표 수정

`config/name_corrections.yaml`에 `오기: 올바른 표기`를 추가합니다. 프로그램을 다시 시작한 뒤 확정 자막과 저장 전사 양쪽에서 확인합니다. 음성이나 Google 인식 결과가 개선됐다고 판정하지 않습니다.

### 캡처 포맷을 바꿀 때

실제 캡처 포맷은 장치 지원값으로 결정합니다. `audio.sample_rate`만 바꿔 원시 녹음이 고해상도가 된다고 가정하지 마세요. 장치 진단 로그의 샘플레이트·채널 수를 확인하고, 번역 전송이 항상 16 kHz 모노인지 점검합니다.

---

## 12. Cloudflare Named Tunnel 및 Windows 서비스 관리

### 아키텍처 및 서비스 수명주기
- **바이너리 위치**: 실행 파일 인접 디렉터리 (`cloudflared.exe` — Frozen exe 디렉터리, 앱 루트, CWD, PATH 순 탐색)
- **토큰 구성**:
  - 단일 통합 구성: `.env` 파일 내 `CLOUDFLARE_TUNNEL_TOKEN=<TOKEN>` (앱 시작 시 자동 로드)
  - 하위 호환 폴백: `C:\ProgramData\cloudflared\token` 또는 로컬 토큰 파일
- **Windows 서비스 명칭**: `Cloudflared` (교회 고정 PC용)
- **실행 명령 (On-Demand)**: `cloudflared.exe tunnel run --token <TOKEN>` (표준 사용자 권한으로 실행)
- **백그라운드 감시 및 로깅**:
  - `app/cloudflared_service.py` (`CloudflaredService`)가 서버 시작 시 Windows 서비스를 우선 조회.
  - Windows 서비스 미구동 시 on-demand 임베디드 자식 프로세스로 실행 (`CREATE_NO_WINDOW`).
  - 프로세스 출력은 `logs/cloudflared.log`에 기록되어 Windows 파이프 버퍼(4KB) 교착 상태(Deadlock)를 방지.
- **공용 엔드포인트**: `https://live.starkvillekoreanchurch.org/live` (또는 `config.yaml`의 `public_url`)

### 설정 마법사 및 타 컴퓨터 배포
- **설정 마법사 (`SKC_setup.exe` / `setup_gui.py`)**:
  - `Card 3 (Public HTTPS)`에서 토큰 입력 시 `.env` 파일에 `CLOUDFLARE_TUNNEL_TOKEN`으로 원자적(Atomic) 직접 저장.
  - 관리자 권한이 없는 일반 노트북 사용자라도 UAC 오류 없이 "포터블 모드 준비 완료" 상태로 안내.
  - `[⬇️ Download cloudflared.exe]` 버튼을 통해 공식 최신 바이너리(~54MB)를 원클릭 다운로드.
- **아카이브된 참조 스크립트 (`.agent/archive/legacy-experiments.zip`의 `loose/`)**:
  - `install_cloudflare_tunnel.bat`: 초기 교회 PC 서비스 설치 배치 (참조용).
  - `fix_cloudflared_service.bat`: 서비스 경로 복구 배치 (참조용).
  - `check_skc_live.bat`: 3단계 헬스체크 배치 (참조용).
  - 임베디드 온디맨드 터널 탑재로 일반 배포본에서는 불필요합니다. 과거 절차의 참고 자료로만 보관하며 현재 환경에 그대로 실행하지 않습니다.

### Cloudflare Zero Trust 대시보드 및 터널 토큰(Token) 확인
- **대시보드 접속 주소**: [https://one.dash.cloudflare.com](https://one.dash.cloudflare.com) 또는 [https://dash.cloudflare.com](https://dash.cloudflare.com)
- **탐색 경로**:
  1. Cloudflare Zero Trust 접속 ➔ **Networks** ➔ **Tunnels**
  2. 등록된 터널(예: `skc-live` / `live.starkvillekoreanchurch.org`) 선택
  3. **Configure** ➔ **Overview** 또는 **Install and run a connector** 탭 클릭
  4. 운영체제 **Windows** 선택 후 나타나는 설치 명령어 확인:
     ```cmd
     cloudflared.exe service install <TOKEN_STRING>
     ```
  5. 명령어 뒷부분의 `<TOKEN_STRING>`(Base64 인코딩된 긴 토큰 문자열)이 해당 터널의 인증 키입니다.
- **토큰 저장 방식**:
  - 최우선 (권장): `.env` 파일의 `CLOUDFLARE_TUNNEL_TOKEN` 변수 (Git 추적 제외)
  - Windows 서비스 전역: `C:\ProgramData\cloudflared\token` (SYSTEM 계정 권한 보관)

### 이중 실행 모드 (Dual Operation Mode)
1. **교회 본당 컴퓨터 (Windows Service 모드)**:
   - 고정 컴퓨터에서는 백그라운드 윈도우 서비스(`Cloudflared`)로 항상 켜져 있거나 서비스로 기동됩니다.
2. **개인 노트북 / 비관리자 환경 (임베디드 프로세스 모드 - On-Demand Process)**:
   - 관리자 권한(UAC) 없이 표준 사용자 권한으로도 실행 가능합니다.
   - 번역 프로그램(`main.py` / `SKC_translation.exe`) 실행 시:
     - 시스템 윈도우 서비스가 실행 중이지 않으면 앱 내부의 `CloudflaredService`가 `cloudflared.exe tunnel run --token <TOKEN>`을 창 없는 백그라운드 자식 프로세스(`CREATE_NO_WINDOW`)로 직접 구동합니다.
   - 번역 프로그램 종료 시:
     - `server.py` lifespan teardown 및 Python `atexit` 훅을 통해 `cloudflared.exe` 프로세스를 안전하게 자동 종료합니다.
     - 노트북을 닫거나 집에서 프로그램을 종료하면 터널이 함께 내려가므로 교회 본당 시스템과의 커넥터 충돌이 원천 방지됩니다.

---

## 13. 실시간 텔레메트리 및 청취자 통계 정합성 (Network Telemetry & Listener Tracking)

### 목적 및 개요
운영자 콘솔에 구글 제미나이 AI 처리 지연(Turn-Onset Latency)과 접속 경로별(현장 Wi-Fi vs 공용 HTTPS) 실시간 왕복 지연(RTT) 및 청취자 수를 정확히 제공합니다.

### 통신 프로토콜 (`/ws/telemetry`)
1. **즉시 핑 (Immediate Initial Ping)**:
   - 참석자가 `/live` 페이지에 접속하여 텔레메트리 WebSocket이 열리는 즉시(`ws.onopen`) 첫 번째 `latency_ping`을 전송합니다.
   - 이후 10초 주기로 정기 핑을 전송합니다. (초기 10초 동안 청취자 수가 0으로 집계되는 지연 현상 방지)
2. **서브 밀리초 RTT 수용 (`rtt_ms >= 0`)**:
   - 로컬호스트 및 초저지연 LAN 환경에서 `Math.round(rtt)` 연산 시 `0 ms`가 반환될 수 있으므로, 백엔드(`broadcast.py`, `server.py`)는 `rtt_ms >= 0`을 정상 샘플로 수용하여 활성 청취자 목록(`_active_clients`)에서 누락되지 않도록 보장합니다.
3. **접속 경로 판별 및 ASGI 스코프 스탬핑 (Route Classification & ASGI Scope Stamping)**:
   - `PublicHostGuardMiddleware`가 인입되는 모든 HTTP/WebSocket 요청에 대해 호스트 및 포워딩 헤더(`Host`, `X-Forwarded-Host`, `Forwarded`)를 검사하여 `scope["route_scope"] = "public" if is_public else "local"`로 1회 원자적 스탬핑합니다.
   - 핸들러(`/ws/telemetry` 등)는 중복 계산이나 외부 헬퍼 재호출 없이 `ws.scope.get("route_scope", "local")`을 직접 참조합니다.
4. **상호작용 타이밍 마진 (Interacting Timeouts Margin)**:
   - 모바일 브라우저(iOS/Android Safari/Chrome)는 백그라운드 탭 전환 또는 화면 잠금 시 `setInterval`을 약 60초 간격으로 스로틀링합니다.
   - 백엔드의 비활성 청취자 정리 TTL(`CLIENT_INACTIVE_TTL_S`)은 **120초**로 두어 60초 타이머 간격에 여유를 줍니다. 청취자 수가 주기적으로 0으로 출렁이는 현상을 줄이기 위한 값입니다.
5. **이벤트 정합성 및 Unknown 버킷 (Eventual Consistency & Unknown Bucket)**:
   - SSE 브로드캐스트 연결 큐(`total_listeners`)와 텔레메트리 WebSocket 수신 간의 네트워크 시차를 흡수하기 위해 `local_listeners + public_listeners + unknown_listeners == total_listeners` 항등식을 유지합니다.

### 운영자 콘솔 이중 언어 현지화 원칙 (Bilingual Parity)
- 모든 UI 요소는 `data-lang="ko"`와 `data-lang="en"` 요소를 동시에 유지해야 하며, 런타임 텍스트 동적 치환 시에도 순수 단일 언어 문자열로 innerHTML/textContent를 덮어쓰지 않고 이중 언어 태그를 보존하거나 `getOperatorUiLanguage()`를 검사하여 일치시킵니다.
- `<option>` 태그의 언어 표시는 해당 선택기를 갱신하는 JavaScript에서 `option.textContent`로 처리합니다. 고정 모델명에는 드롭다운을 사용하지 않습니다.

---

## 14. 주석 보존 설정 파일 지속성 및 방어적 환경 변수 (Config Persistence & Environment Parsing)

### 주석 보존 인플레이스 YAML 엔진 (`app/config.py`)
- **문제점**: 종래의 `yaml.dump(dict)` 호출은 `config.yaml`에 작성된 모든 사용자 주석, 한글 설명, 빈 줄, 섹션 서식을 완전히 삭제하고 알파벳순 평면 덤프로 덮어쓰는 문제가 있었습니다.
- **해결책**: 외부 무거운 라이브러리(`ruamel.yaml` 등) 의존성 추가 없이, 표준 라이브러리 정규식 기반 인플레이스 치환 엔진(`_update_yaml_scalar`, `_update_yaml_list`)을 개발했습니다.
  - 키의 블록 위치와 들여쓰기를 정밀하게 탐지하여 주석과 파일 구조를 100% 영구 보존합니다.
  - 섹션이나 키가 파일에 존재하지 않을 경우에만 기본 서식에 맞춰 안전하게 자동 추가합니다.
- **Windows 파일 잠금 백오프 재시도 (`os.replace`)**:
  - Windows 환경에서 바이러스 백신 검사나 동시 프로세스 접근으로 인해 `os.replace` 시 `WinError 5 Access is denied` (PermissionError)가 간헐적으로 발생할 수 있습니다.
  - 이를 원천 차단하기 위해 5회 시도 지수 백오프(`0.05 * (2 ** attempt)`) 재시도 루프를 적용했습니다.

### 방어적 환경 변수 파싱 (`app/operator_auth.py`, `app/cloudflared_service.py`)
- `.env` 파일 편집 시 줄바꿈(Newline) 누락으로 인해 `SKC_OPERATOR_PASSWORD=1155 CLOUDFLARE_TUNNEL_TOKEN=eyJh...` 형태로 붙여넣기되는 사고를 방어합니다.
- `get_operator_password()`는 비밀번호 뒤에 붙은 `CLOUDFLARE_` 문자열을 선제적으로 제거하며, `get_tunnel_token()`은 토큰이 비밀번호 줄에 결합되어 있더라도 정규식을 통해 올바른 토큰을 추출합니다.

### 인트라넷 및 인터넷 분리 아키텍처
- `config.yaml` 설정을 `intranet:` (host, port, local_hostname)과 `internet:` (enable_tunnel, public_url)으로 분리하여 네트워크 경계를 명확히 했습니다.
- 터널 토큰이 없거나 `enable_tunnel: false`인 경우 시스템이 단일 열 현장 Wi-Fi 전용 모드로 자동 적응하여 불필요한 외부 공격 표면을 노출하지 않습니다.
