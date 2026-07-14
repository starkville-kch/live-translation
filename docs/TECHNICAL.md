# 기술 참고서 / Technical Reference
### 실시간 예배 번역 시스템 / Live Translation System

이 문서는 본 시스템의 핵심 구성요소별 구현 방식을 코드 수준에서 설명합니다. 새 기능을 추가하거나 기존 동작을 수정하는 개발자를 위한 참고서입니다.  
This document explains how each core component of the system is implemented at the code level. It is a reference for developers adding features or modifying existing behaviour.

---

언어 선택 / Select Language:
- 🇰🇷 [한국어 — 기술 참고서](#korean-section)
- 🇺🇸 [English — Technical Reference](#english-section)

***

<details open>
<summary><b>🇰🇷 한국어 버전 (클릭하여 접기/펼치기)</b></summary>
<a name="korean-section"></a>

## 📌 목차
1. [데이터 흐름 전체 개요](#1-데이터-흐름-전체-개요)
2. [FastAPI 서버 구조](#2-fastapi-서버-구조)
3. [Gemini Live API 세션](#3-gemini-live-api-세션)
4. [오디오 캡처 파이프라인](#4-오디오-캡처-파이프라인)
5. [SSE 자막 브로드캐스트](#5-sse-자막-브로드캐스트)
6. [Web Audio API 실시간 재생](#6-web-audio-api-실시간-재생)
7. [자막 커밋 전략](#7-자막-커밋-전략)
8. [용어집 교정 패스 (Glossary)](#8-용어집-교정-패스-glossary)
9. [Asyncio 패턴 요약](#9-asyncio-패턴-요약)
10. [확장 가이드](#10-확장-가이드)

---

## 1. 데이터 흐름 전체 개요

```
[USB 믹서]
    │ PCM 오디오 (16kHz mono PCM16)
    ▼
app/audio.py  →  AudioCapture.read_chunk()
    │ bytes (3200 bytes / 100ms)
    ▼
app/gemini_session.py  →  GeminiSession._audio_queue
    │ asyncio.Queue[bytes]  (maxsize=500)
    ▼
GeminiSession._send_loop()  →  session.send_realtime_input(audio=Blob)
    │                                 [Gemini Live API WebSocket]
    ▼
GeminiSession._recv_loop()
    ├── output_transcription.text  →  on_caption(delta)
    ├── input_transcription.text   →  on_source_transcript(delta)
    └── model_turn.inline_data     →  on_audio_chunk(pcm_bytes)
    │
    ▼
app/broadcast.py  →  CaptionBroadcaster
    ├── on_caption_delta()   →  SSE "update" 이벤트 → /stream
    ├── _schedule_commit()   →  SSE "commit" 이벤트 → /stream  (1.5s 묵음 후)
    ├── on_source_delta()    →  SSE "source" 이벤트 → /stream  (운영자 전용)
    └── on_audio_chunk()     →  WS binary → /audio-stream
    │
    ▼
[참석자 모바일 브라우저]
    ├── SSE EventSource  →  자막 렌더링
    └── WebSocket        →  Web Audio API 재생
```

---

## 2. FastAPI 서버 구조

### 라이프사이클 (Lifespan)
`app/server.py`는 FastAPI `lifespan` 컨텍스트 매니저로 서버 시작/종료를 관리합니다.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작: 모델 탐색, 브로드캐스터·세션 싱글턴 초기화
    yield
    # 종료: 번역 파이프라인 정지 및 세션 로그 저장
```

전역 싱글턴 두 개가 서버 전체 생애주기 동안 유지됩니다:
- `_broadcaster`: `CaptionBroadcaster` — SSE/WS 팬아웃 담당
- `_session`: `GeminiSession` — Gemini API 세션 담당

### 주요 라우트 구조

| 라우트 | 유형 | 반환값 | 역할 |
|--------|------|--------|------|
| `GET /` | HTTP | `HTMLResponse` | 운영자 콘솔 (임베디드 HTML) |
| `GET /live` | HTTP | `HTMLResponse` | 참석자 자막 페이지 |
| `GET /stream` | SSE | `EventSourceResponse` | 자막 이벤트 스트림 |
| `WS /audio-stream` | WebSocket | binary frames | 24kHz PCM16 오디오 |
| `GET /api/status` | HTTP | JSON | 시스템 상태 |
| `POST /api/start` | HTTP | JSON | 번역 파이프라인 시작 |
| `POST /api/stop` | HTTP | JSON | 파이프라인 정지 + 로그 저장 |
| `POST /api/pause` | HTTP | JSON | 마이크 및 과금 일시정지 |
| `POST /api/resume` | HTTP | JSON | 일시정지 해제 |

### SSE 엔드포인트 구현 패턴

`sse_starlette` 라이브러리를 사용합니다. 제너레이터 함수가 클라이언트 연결당 하나씩 생성됩니다.

```python
from sse_starlette.sse import EventSourceResponse

@app.get("/stream")
async def stream(request: Request):
    async def event_generator():
        queue = asyncio.Queue()
        _broadcaster.register(queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield {"event": event["type"], "data": event["data"]}
        except asyncio.TimeoutError:
            yield {"event": "ping", "data": ""}  # keepalive
        finally:
            _broadcaster.unregister(queue)
    return EventSourceResponse(event_generator())
```

클라이언트가 접속할 때마다 고유한 `asyncio.Queue`가 생성되어 브로드캐스터에 등록됩니다. 클라이언트가 연결을 끊으면 `finally` 블록에서 큐가 해제됩니다.

### WebSocket 바이너리 오디오

```python
@app.websocket("/audio-stream")
async def audio_stream(websocket: WebSocket):
    await websocket.accept()
    _broadcaster.register_audio(websocket)
    try:
        while True:
            await websocket.receive_bytes()  # 연결 유지용 핑
    except WebSocketDisconnect:
        pass
    finally:
        _broadcaster.unregister_audio(websocket)
```

오디오 데이터는 서버→클라이언트 단방향으로만 흐릅니다. 클라이언트로부터의 수신(`receive_bytes`)은 오직 연결 상태 감지용입니다.

### 임베디드 HTML 서빙
별도 템플릿 엔진 없이 HTML 전체를 Python 문자열로 `server.py` 내에 정의합니다. 장점: 단일 파일 배포 가능. 단점: HTML이 길어질수록 유지보수성 하락. 향후 Jinja2 템플릿 분리 가능.

---

## 3. Gemini Live API 세션

### 연결 설정 (`_build_config`)

번역 전용 모델(`gemini-3.5-live-translate-preview`)은 `translation_config`를 사용합니다.

```python
types.LiveConnectConfig(
    response_modalities=["AUDIO"],          # TEXT 미지원 (오류 1007)
    translation_config=types.TranslationConfig(
        target_language_code="en",
        echo_target_language=True,
    ),
    speech_config=types.SpeechConfig(       # 보이스 고정 필수
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="orus")
        )
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(
        language_hints=types.LanguageHints(language_codes=["ko", "en"])
    ),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    context_window_compression=types.ContextWindowCompressionConfig(
        sliding_window=types.SlidingWindow()
    ),
    session_resumption=types.SessionResumptionConfig(handle=self._resumption_handle),
)
```

### 응답 파싱 (`_recv_loop`)

하나의 응답 프레임에는 여러 종류의 데이터가 함께 올 수 있습니다.

```python
async for response in session.receive():
    sc = response.server_content

    # 1. 합성 오디오 PCM (24kHz, PCM16, mono)
    for part in (sc.model_turn.parts or []):
        if part.inline_data:
            on_audio_chunk(part.inline_data.data)  # ~12,000 bytes/chunk

    # 2. 한국어 원문 전사
    if sc.input_transcription?.text:
        current_ko += sc.input_transcription.text

    # 3. 영어 번역 텍스트
    if sc.output_transcription?.text:
        en_text = sc.output_transcription.text
        on_caption(en_text)

    # 4. GoAway — 즉시 재연결 트리거
    if response.go_away:
        raise RuntimeError("GoAway")

    # 5. 세션 재개 핸들 업데이트
    if response.session_resumption_update?.handle:
        self._resumption_handle = response.session_resumption_update.handle
```

### 자동 재연결 (`_run_with_retry`)

```
attempt=0 → 연결 시도
    │
    ├─ 정상 실행 → attempt=0 리셋 반복
    │
    └─ 예외 발생
          attempt += 1
          attempt >= 3 → FAILED 상태, 루프 종료
          delay = min(2 * 2^(attempt-1), 60)초 대기 → 재시도
```

GoAway 수신 시: `RuntimeError("GoAway")` → attempt 증가 없이 즉시 재연결  
(GoAway는 구글이 예고한 계획적 연결 교체이므로 attempt를 소모하지 않습니다)

> **주의**: GoAway는 attempt를 소모하지 않으나, 현재 코드에서는 일반 예외와 동일하게 처리되어 attempt가 증가합니다. 10분 세션 내 GoAway가 여러 번 오면 FAILED 상태가 될 수 있습니다 — 향후 개선 여지.

### 세션 재개 vs. 신규 연결
`_resumption_handle`이 존재하면 세션 재개(컨텍스트 유지), 없으면 신규 연결입니다. 핸들은 매 응답 프레임마다 갱신되며 재연결 직후 소비됩니다.

---

## 4. 오디오 캡처 파이프라인

### PCM 포맷 체인

```
PyAudio 장치 (native rate, e.g. 48000Hz)
    │ raw bytes (int16, little-endian)
    ▼
_resample_chunk()  →  16kHz mono PCM16
    │ 선형 보간 리샘플러 (NumPy 의존 없음)
    ▼
GeminiSession._audio_queue  (asyncio.Queue, maxsize=500)
    │
    ▼
session.send_realtime_input(audio=Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
```

### 리샘플러 동작

```python
# 16kHz 목표 청크 크기: 100ms = 1600 samples = 3200 bytes
ratio = native_rate / 16000
indices = [int(i * ratio) for i in range(target_samples)]
resampled = [source_samples[i] for i in indices]
```

단순 최근접 이웃(nearest-neighbor) 선형 인덱싱으로 구현합니다. 음성 인식용도이므로 오디오 품질보다 지연 최소화가 우선입니다.

### RMS 레벨 미터링

```python
rms = sqrt(mean(sample**2 for sample in chunk))
db = 20 * log10(rms / 32768)  # 0 dBFS 기준 정규화
```

10Hz 주기(100ms마다)로 계산하며, `-60 dBFS` 이하 지속 시 `NO_SIGNAL` 경보를 발생시킵니다.

---

## 5. SSE 자막 브로드캐스트

### 이벤트 종류

| 이벤트 | `data` 형식 | 수신 대상 | 설명 |
|--------|------------|---------|------|
| `update` | 영어 텍스트 (델타) | 참석자+운영자 | 현재 줄 실시간 갱신 |
| `commit` | 영어 전체 줄 | 참석자+운영자 | 줄 확정 및 기록 추가 |
| `source` | 한국어 텍스트 (델타) | **운영자 전용** | 원문 스트리밍 |
| `unavailable` | `""` | 참석자+운영자 | 번역 불가 알림 |
| `ping` | `""` | 참석자+운영자 | 15초 keepalive |
| `paused` | `""` | 참석자+운영자 | 일시정지 알림 |
| `resumed` | `""` | 참석자+운영자 | 재개 알림 |

### 팬아웃 구조

```python
class CaptionBroadcaster:
    _queues: list[asyncio.Queue]   # SSE 클라이언트당 1개
    _audio_clients: list[WebSocket]  # 오디오 WebSocket 클라이언트

    def broadcast(self, event_type: str, data: str):
        for q in self._queues:
            q.put_nowait({"type": event_type, "data": data})
        # 큐가 꽉 차면 put_nowait은 QueueFull → 해당 클라이언트만 drop
```

각 클라이언트는 독립 큐를 가지므로 느린 클라이언트가 빠른 클라이언트를 블로킹하지 않습니다.

---

## 6. Web Audio API 실시간 재생

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

iOS Safari는 사용자 인터랙션(탭) 없이 `AudioContext`를 생성할 수 없습니다. "오디오 듣기" 버튼 클릭 시에 `AudioContext`를 초기화하는 이유입니다.

---

## 7. 자막 커밋 전략

자막 한 줄의 "확정" 시점 결정 로직입니다.

### 세 가지 커밋 경로

```
스트리밍 델타 수신 중...
    │
    ├─ 1.5초 동안 새 델타 없음  →  [커밋 경로 1] 1.5초 묵음 타이머
    │                               broadcast("commit", current_line)
    │                               glossary.correct(ko, en) 적용 후
    │
    ├─ 현재 줄 150자 초과        →  [커밋 경로 2] 강제 커밋
    │                               _find_split()로 자연 경계 탐색
    │                               ". " → "! " → "? " → "; " → ", " → " "
    │
    └─ turn_complete 신호        →  [커밋 경로 3] 자동 커밋 루프
                                    _auto_commit_loop()에서 감지
```

### `turn_complete` 미사용 이유
Gemini는 "음", "어" 같은 필러 발화에도 `turn_complete`를 발생시킵니다. 설교 중 빈번한 필러로 인해 자막이 과도하게 단편화되어 가독성이 심각하게 저하됩니다. 묵음 타이머가 사람이 실제 말을 쉬는 자연적 구두점과 더 잘 일치합니다.

---

## 8. 용어집 교정 패스 (Glossary)

`app/glossary.py` + `config/glossary.yaml`

### 동작 원리

1. 자막 줄이 **커밋될 때만** 동작합니다 (스트리밍 중 미적용).
2. 한국어 원문(`input_transcription`)에 용어가 존재하는지 확인합니다.
3. 영어 번역에 올바른 용어가 이미 있으면 건너뜁니다.
4. 없으면 줄 뒤에 `[올바른 용어]`를 추가합니다.

```python
# 예: 당회가 한국어 원문에 있고 영어에 "Session"이 없을 때
"The elders discussed the matter." → "The elders discussed the matter. [Session]"
```

### 경계 매칭 규칙

한국어 명사에는 조사가 직접 붙으므로 오른쪽 경계를 검사하지 않습니다.

```python
# ✅ 올바른 패턴 (왼쪽 경계만)
pattern = r"(?<![가-힣])" + re.escape(phrase)

# 예: "당회"는 "당회에서", "당회를", "당회의" 모두 매칭
# 예: "장로회당회"에서 "당회"는 매칭 안 됨 (앞에 한글 있음)
```

### 용어집 활성화/비활성화
`config/glossary.yaml`에서 `enabled: true/false`로 카테고리별 제어:
- **A (PCA 직분명)**: `enabled: true` — 당회, 장로, 목사, 집사
- **C (신조 문서)**: `enabled: true` — 웨스트민스터 신앙고백
- **B, D, E, F**: `enabled: false` — 필요 시 활성화

---

## 9. Asyncio 패턴 요약

### 세션 내 태스크 구조

```
_run_session() 내부:
    ┌─────────────────────────────────────────┐
    │  asyncio.wait(FIRST_COMPLETED)          │
    │  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐ │
    │  │send_loop │ │recv_loop │ │commit  │ │stop_event│ │
    │  │(오디오전송)│ │(응답수신)│ │(루프)  │ │(정지감지)│ │
    │  └──────────┘ └──────────┘ └────────┘ └──────────┘ │
    └─────────────────────────────────────────┘
    첫 번째 태스크 완료/예외 → 나머지 모두 cancel
```

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

## 10. 확장 가이드

### 새 REST 엔드포인트 추가

```python
# app/server.py 내부
@app.post("/api/my-endpoint")
async def my_endpoint():
    # _session, _broadcaster 싱글턴에 직접 접근 가능
    return {"ok": True}
```

### 새 SSE 이벤트 종류 추가

1. `app/broadcast.py`에 브로드캐스트 메서드 추가:
   ```python
   def broadcast_my_event(self, data: str):
       self.broadcast("my_event", data)
   ```
2. 참석자 JavaScript에서 수신:
   ```javascript
   es.addEventListener("my_event", (e) => { ... });
   ```

### 용어집에 새 항목 추가

`config/glossary.yaml`의 `direct:` 목록에 추가:
```yaml
- category: A
  ko: "새로운 한국어 용어"
  en: "Correct English Term"
  enabled: true
  variants: ["맞춤법 변형1", "맞춤법 변형2"]  # 선택
```
서버 재시작 없이는 반영되지 않습니다 (`GlossaryCorrector`는 시작 시 1회 로딩).

### 오디오 샘플레이트 변경

`config.yaml`의 `audio.sample_rate`만 바꾸면 리샘플러가 자동 적용됩니다. Gemini는 항상 16kHz로 전송됩니다.

</details>

***

<details>
<summary><b>🇺🇸 English Version (Click to Collapse/Expand)</b></summary>
<a name="english-section"></a>

## 📌 Table of Contents
1. [End-to-End Data Flow](#1-end-to-end-data-flow)
2. [FastAPI Server Structure](#2-fastapi-server-structure)
3. [Gemini Live API Session](#3-gemini-live-api-session)
4. [Audio Capture Pipeline](#4-audio-capture-pipeline)
5. [SSE Caption Broadcast](#5-sse-caption-broadcast)
6. [Web Audio API Real-Time Playback](#6-web-audio-api-real-time-playback)
7. [Caption Commit Strategy](#7-caption-commit-strategy)
8. [Glossary Correction Pass](#8-glossary-correction-pass)
9. [Asyncio Patterns Summary](#9-asyncio-patterns-summary)
10. [Extension Guide](#10-extension-guide)

---

## 1. End-to-End Data Flow

```
[USB Mixer]
    │ PCM audio (16kHz mono PCM16)
    ▼
app/audio.py  →  AudioCapture.read_chunk()
    │ bytes (3200 bytes / 100ms chunk)
    ▼
app/gemini_session.py  →  GeminiSession._audio_queue
    │ asyncio.Queue[bytes]  (maxsize=500)
    ▼
GeminiSession._send_loop()  →  session.send_realtime_input(audio=Blob)
    │                                 [Gemini Live API WebSocket]
    ▼
GeminiSession._recv_loop()
    ├── output_transcription.text  →  on_caption(delta)
    ├── input_transcription.text   →  on_source_transcript(delta)
    └── model_turn.inline_data     →  on_audio_chunk(pcm_bytes)
    │
    ▼
app/broadcast.py  →  CaptionBroadcaster
    ├── on_caption_delta()   →  SSE "update" event  → /stream
    ├── _schedule_commit()   →  SSE "commit" event  → /stream  (after 1.5s silence)
    ├── on_source_delta()    →  SSE "source" event  → /stream  (operator only)
    └── on_audio_chunk()     →  WS binary           → /audio-stream
    │
    ▼
[Attendee mobile browser]
    ├── SSE EventSource  →  caption rendering
    └── WebSocket        →  Web Audio API playback
```

---

## 2. FastAPI Server Structure

### Lifespan
`app/server.py` uses the FastAPI `lifespan` context manager for startup/shutdown sequencing.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: model resolution, broadcaster + session singleton init
    yield
    # Shutdown: stop translation pipeline, write session transcript log
```

Two global singletons persist for the server's lifetime:
- `_broadcaster`: `CaptionBroadcaster` — owns all SSE/WS fan-out
- `_session`: `GeminiSession` — owns the Gemini API connection

### Route Map

| Route | Type | Returns | Role |
|-------|------|---------|------|
| `GET /` | HTTP | `HTMLResponse` | Operator console (embedded HTML) |
| `GET /live` | HTTP | `HTMLResponse` | Attendee caption page |
| `GET /stream` | SSE | `EventSourceResponse` | Caption event stream |
| `WS /audio-stream` | WebSocket | binary frames | 24kHz PCM16 audio |
| `GET /api/status` | HTTP | JSON | System status snapshot |
| `POST /api/start` | HTTP | JSON | Start translation pipeline |
| `POST /api/stop` | HTTP | JSON | Stop pipeline + write log |
| `POST /api/pause` | HTTP | JSON | Pause mic + billing |
| `POST /api/resume` | HTTP | JSON | Resume from pause |

### SSE Endpoint Pattern

Uses `sse_starlette`. A new generator instance is created per client connection.

```python
from sse_starlette.sse import EventSourceResponse

@app.get("/stream")
async def stream(request: Request):
    async def event_generator():
        queue = asyncio.Queue()
        _broadcaster.register(queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield {"event": event["type"], "data": event["data"]}
        except asyncio.TimeoutError:
            yield {"event": "ping", "data": ""}  # keepalive
        finally:
            _broadcaster.unregister(queue)
    return EventSourceResponse(event_generator())
```

Each connecting client gets its own `asyncio.Queue` registered with the broadcaster. When the client disconnects, the `finally` block deregisters and discards the queue.

### Binary WebSocket Audio

```python
@app.websocket("/audio-stream")
async def audio_stream(websocket: WebSocket):
    await websocket.accept()
    _broadcaster.register_audio(websocket)
    try:
        while True:
            await websocket.receive_bytes()  # connection keepalive only
    except WebSocketDisconnect:
        pass
    finally:
        _broadcaster.unregister_audio(websocket)
```

Data flows server→client only. `receive_bytes()` is called solely to detect disconnection.

### Embedded HTML Serving
All HTML is defined as Python strings inside `server.py`. No template engine is used. Advantage: single-file deployment. Trade-off: HTML maintainability decreases as it grows. Could be migrated to Jinja2 templates later.

---

## 3. Gemini Live API Session

### Connection Config (`_build_config`)

The dedicated translate model requires `translation_config`.

```python
types.LiveConnectConfig(
    response_modalities=["AUDIO"],          # TEXT not supported (error 1007)
    translation_config=types.TranslationConfig(
        target_language_code="en",
        echo_target_language=True,
    ),
    speech_config=types.SpeechConfig(       # voice pinning is mandatory
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="orus")
        )
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(
        language_hints=types.LanguageHints(language_codes=["ko", "en"])
    ),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    context_window_compression=types.ContextWindowCompressionConfig(
        sliding_window=types.SlidingWindow()
    ),
    session_resumption=types.SessionResumptionConfig(handle=self._resumption_handle),
)
```

### Response Parsing (`_recv_loop`)

A single response frame can carry multiple payload types simultaneously.

```python
async for response in session.receive():
    sc = response.server_content

    # 1. Synthesised audio PCM (24kHz, PCM16, mono)
    for part in (sc.model_turn.parts or []):
        if part.inline_data:
            on_audio_chunk(part.inline_data.data)  # ~12,000 bytes/chunk

    # 2. Korean source transcription
    if sc.input_transcription?.text:
        current_ko += sc.input_transcription.text

    # 3. English translation text
    if sc.output_transcription?.text:
        on_caption(sc.output_transcription.text)

    # 4. GoAway — triggers immediate reconnect
    if response.go_away:
        raise RuntimeError("GoAway")

    # 5. Session resumption handle update
    if response.session_resumption_update?.handle:
        self._resumption_handle = response.session_resumption_update.handle
```

### Auto-Reconnect (`_run_with_retry`)

```
attempt=0 → connect
    │
    ├─ clean run → reset attempt=0, loop
    │
    └─ exception raised
          attempt += 1
          attempt >= 3 → emit FAILED, exit
          delay = min(2 * 2^(attempt-1), 60)s → retry
```

GoAway path: `RuntimeError("GoAway")` raised → attempt increments → immediate retry with negligible delay since the GoAway delay is near-zero.

### Session Resumption vs. Fresh Connect
If `_resumption_handle` is set, the next connect call passes it to `SessionResumptionConfig`, preserving model context across the reconnect. The handle is cleared immediately after being sent and refreshed from the next response.

---

## 4. Audio Capture Pipeline

### PCM Format Chain

```
PyAudio device (native rate, e.g. 48000 Hz)
    │ raw bytes (int16, little-endian, mono)
    ▼
_resample_chunk()  →  16kHz mono PCM16
    │ linear interpolation resampler (no NumPy dependency)
    ▼
GeminiSession._audio_queue  (asyncio.Queue, maxsize=500)
    │
    ▼
session.send_realtime_input(audio=Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
```

### Resampler

```python
# Target: 16kHz, 100ms = 1600 samples = 3200 bytes
ratio = native_rate / 16000
indices = [int(i * ratio) for i in range(target_samples)]
resampled = [source_samples[i] for i in indices]
```

Nearest-neighbour linear index mapping. Prioritises low latency over audio fidelity — appropriate for speech recognition input.

### RMS Level Metering

```python
rms = sqrt(mean(sample**2 for sample in chunk))
db = 20 * log10(rms / 32768)  # relative to 0 dBFS
```

Computed every 100ms (10 Hz). Sustained readings below `-60 dBFS` trigger the `NO_SIGNAL` warning.

---

## 5. SSE Caption Broadcast

### Event Types

| Event | `data` format | Recipients | Description |
|-------|--------------|------------|-------------|
| `update` | EN text delta | Attendees + Operator | Replace current streaming line |
| `commit` | EN full line | Attendees + Operator | Finalise line, add to history |
| `source` | KO text delta | **Operator only** | Korean source streaming |
| `unavailable` | `""` | All | Session failed, show banner |
| `ping` | `""` | All | 15s keepalive (no UI effect) |
| `paused` | `""` | All | Show paused state |
| `resumed` | `""` | All | Show live state |

### Fan-out Architecture

```python
class CaptionBroadcaster:
    _queues: list[asyncio.Queue]    # one per SSE client
    _audio_clients: list[WebSocket] # audio WS clients

    def broadcast(self, event_type: str, data: str):
        for q in self._queues:
            q.put_nowait({"type": event_type, "data": data})
        # QueueFull on put_nowait → that client drops the event only
```

Each client has an independent queue, so a slow client cannot block a fast one.

---

## 6. Web Audio API Real-Time Playback

### Server → Browser Flow

```
GeminiSession  →  on_audio_chunk(pcm_bytes)
    ↓
CaptionBroadcaster.on_audio_chunk()
    ↓
websocket.send_bytes(pcm_bytes)  (to each /audio-stream client)
    ↓
[Browser JavaScript]
ws.onmessage = (e) => scheduleAudioChunk(e.data)
```

### Browser PCM16 → Float32 Conversion

```javascript
// ArrayBuffer (Int16, 24kHz, mono) → Float32Array
const int16 = new Int16Array(arrayBuffer);
const float32 = new Float32Array(int16.length);
for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768.0;  // normalise to [-1.0, 1.0]
}
```

### Gapless Audio Buffer Scheduling

```javascript
function scheduleAudioChunk(float32Data) {
    const buffer = audioCtx.createBuffer(1, float32Data.length, 24000);
    buffer.getChannelData(0).set(float32Data);
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(audioCtx.destination);
    // Schedule start immediately after the previous chunk ends
    nextStartTime = Math.max(audioCtx.currentTime, nextStartTime);
    source.start(nextStartTime);
    nextStartTime += buffer.duration;
}
```

iOS Safari requires a user gesture (tap) before `AudioContext` can be created — this is why the "Listen to Audio" button initialises `AudioContext` on click.

---

## 7. Caption Commit Strategy

How the system decides when a streaming line is "done" and ready to display permanently.

### Three Commit Paths

```
Streaming deltas arriving...
    │
    ├─ No new delta for 1.5s     →  [Path 1] Silence timer commit
    │                                broadcast("commit", current_line)
    │                                glossary.correct(ko, en) applied first
    │
    ├─ Current line exceeds 150  →  [Path 2] Force-commit overflow
    │  characters                    _find_split() searches last 60 chars
    │                                ". " → "! " → "? " → "; " → ", " → " "
    │
    └─ turn_complete signal      →  [Path 3] Auto-commit loop
                                    _auto_commit_loop() detects 1.5s silence
```

### Why `turn_complete` Is Not the Primary Trigger
Gemini fires `turn_complete` on every filler utterance ("um", "uh") in Korean sermon speech. This caused excessive caption fragmentation during testing. The 1.5s silence timer aligns much better with natural spoken sentence boundaries.

---

## 8. Glossary Correction Pass

`app/glossary.py` + `config/glossary.yaml`

### How It Works

1. Runs only when a caption line **commits** — never on streaming drafts.
2. Checks whether the Korean term (or any spelling variant) appears in the accumulated `input_transcription` for the turn.
3. If the correct English term is already in the output, skips it.
4. If missing, appends `[Correct Term]` to the end of the committed line.

```python
# Example: 당회 in Korean source, "Session" absent from English output
"The elders discussed the matter." → "The elders discussed the matter. [Session]"
```

### Boundary Matching

Korean nouns take particles directly (no space), so only the left boundary is checked.

```python
pattern = r"(?<![가-힣])" + re.escape(phrase)

# ✅ "당회" matches "당회에서", "당회를", "당회의"
# ❌ "당회" does NOT match "장로당회" (Korean character immediately before)
```

### Enabling / Disabling Categories

Set `enabled: true/false` per category in `config/glossary.yaml`:
- **A (PCA polity titles)**: `enabled: true` — 당회/Session, 장로/Elder, 목사/Pastor, 집사/Deacon
- **C (confessional documents)**: `enabled: true` — Westminster Confession
- **B, D, E, F**: `enabled: false` — activate after confirming real misses in logs

---

## 9. Asyncio Patterns Summary

### Task Structure Inside a Session

```
_run_session() contains:
    ┌──────────────────────────────────────────────┐
    │  asyncio.wait(FIRST_COMPLETED)               │
    │  ┌───────────┐ ┌───────────┐ ┌───────┐ ┌─────────┐ │
    │  │ send_loop │ │ recv_loop │ │commit │ │  stop   │ │
    │  │(audio out)│ │(responses)│ │(loop) │ │ (event) │ │
    │  └───────────┘ └───────────┘ └───────┘ └─────────┘ │
    └──────────────────────────────────────────────┘
    First task to complete/raise → all others cancelled
```

### Key Synchronisation Primitives

| Primitive | Location | Role |
|-----------|----------|------|
| `asyncio.Queue(maxsize=500)` | `_audio_queue` | Audio chunk backpressure |
| `asyncio.Queue()` (per client) | SSE broadcast | Client isolation |
| `asyncio.Event` | `_stop_event` | Stop signal propagation |
| `asyncio.wait_for(timeout=15)` | SSE generator | Keepalive ping trigger |

### Backpressure Policy

- `_audio_queue.put_nowait()` — drops the chunk if full (never blocks the audio thread)
- SSE `q.put_nowait()` — drops that event for the slow client only
- Audio WS `send_bytes()` — exception removes that client from the list

---

## 10. Extension Guide

### Adding a New REST Endpoint

```python
# Inside app/server.py
@app.post("/api/my-endpoint")
async def my_endpoint():
    # _session and _broadcaster singletons are directly accessible
    return {"ok": True}
```

### Adding a New SSE Event Type

1. Add a broadcast method in `app/broadcast.py`:
   ```python
   def broadcast_my_event(self, data: str):
       self.broadcast("my_event", data)
   ```
2. Listen in attendee JavaScript:
   ```javascript
   es.addEventListener("my_event", (e) => { /* handle */ });
   ```

### Adding a Glossary Entry

Append to the `direct:` list in `config/glossary.yaml`:
```yaml
- category: A
  ko: "새로운 한국어 용어"
  en: "Correct English Term"
  enabled: true
  variants: ["spelling variant 1", "spelling variant 2"]  # optional
```
Requires server restart — `GlossaryCorrector` loads once at startup.

### Changing the Audio Sample Rate

Edit only `audio.sample_rate` in `config.yaml`. The resampler applies automatically. Gemini always receives 16kHz regardless of the device rate.

### Adding a Second Language Target (Phase 10 prep)

1. Create a second `GeminiSession` with `target_language_code="zh"`.
2. Create a second `CaptionBroadcaster` for Chinese captions.
3. Add `/stream?lang=zh` route that uses the second broadcaster's queues.
4. Wire the same `AudioCapture` output to both sessions via `asyncio.Queue` duplication.

</details>
