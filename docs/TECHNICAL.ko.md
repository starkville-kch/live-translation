# 기술 참고서
### 실시간 예배 번역 시스템

> **English version**: [TECHNICAL.en.md](TECHNICAL.en.md)

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
9. [용어집 교정 패스 (Glossary)](#9-용어집-교정-패스-glossary)
10. [Asyncio 패턴 요약](#10-asyncio-패턴-요약)
11. [확장 가이드](#11-확장-가이드)

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
| `GET /api/events?since=N` | HTTP | JSON | 운영자 이벤트 증분 폴링 |

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

## 3. 운영자 이벤트 로그

`app/events.py` — `OperatorEventLog`

### 목적
운영자용 이벤트(Gemini 연결 완료, 참석자 접속 등)와 개발자 로그(회전 파일 `ops.log`/`session.log`)를 분리합니다. 봉사자는 대시보드의 구조화된 이벤트를 보고, 개발자는 `/admin/logs`에서 원시 로그를 확인합니다.

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

## 5. 오디오 캡처 파이프라인

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

## 6. SSE 자막 브로드캐스트

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

iOS Safari는 사용자 인터랙션(탭) 없이 `AudioContext`를 생성할 수 없습니다. "오디오 듣기" 버튼 클릭 시에 `AudioContext`를 초기화하는 이유입니다.

---

## 8. 자막 커밋 전략

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

## 9. 용어집 교정 패스 (Glossary)

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

## 10. Asyncio 패턴 요약

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

## 11. 확장 가이드

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
