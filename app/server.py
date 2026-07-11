"""
FastAPI backend: SSE caption stream, operator/admin API, QR code, static pages.
"""
import asyncio
import io
import json
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import qrcode
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse

from app.audio import AudioCapture, list_input_devices
from app.broadcast import CaptionBroadcaster, CaptionEvent
from app.config import logging_cfg, network_cfg, save_audio_device
from app.gemini_session import GEMINI_MODEL
from app.gemini_session import GeminiSession, SessionStatus
from app.logger import server_log

# Gemini 3.5 Live Translate pricing (Paid Tier):
# Audio Input: $0.0053/min (~$0.00008833/sec)
# Audio Output: $0.0315/min (~$0.000525/sec)
# Total: $0.0368/min (~$0.00061333/sec)
_COST_PER_AUDIO_SEC = 0.0368 / 60.0

# ── Singletons ────────────────────────────────────────────────────────────────
broadcaster = CaptionBroadcaster()
audio = AudioCapture()
session = GeminiSession(
    on_caption=broadcaster.on_caption_delta,
    on_state_change=lambda s: (
        broadcaster.set_unavailable() if s.status == SessionStatus.FAILED else None
    ),
    on_audio_chunk=broadcaster.on_audio_chunk,
)

_qr_png_cache: bytes | None = None
_service_running = False
_paused = False
_service_start_time: float | None = None   # monotonic, set when service starts
_billed_seconds: float = 0.0               # audio seconds sent to Gemini
_pause_start: float | None = None          # monotonic when paused


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _build_qr(url: str) -> bytes:
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _runtime_seconds() -> float:
    if _service_start_time is None:
        return 0.0
    elapsed = time.monotonic() - _service_start_time
    if _pause_start is not None:
        elapsed -= (time.monotonic() - _pause_start)
    return max(0.0, elapsed)


def _write_session_log() -> None:
    """Write per-language transcript files into a timestamped session folder on stop.

    Output layout:
        logs/sessions/20260710_171124/
            summary.txt     — runtime, cost, model
            ko.txt          — Korean source turns, one per line with timestamps
            en.txt          — English translation turns, one per line with timestamps
            aligned.txt     — Korean + English interleaved, human-readable
    """
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = Path(logging_cfg().get("log_dir", "logs")) / "sessions" / ts
        session_dir.mkdir(parents=True, exist_ok=True)

        session.flush_current_turn()  # capture any in-progress turn not yet committed
        runtime = _runtime_seconds()
        cost = _billed_seconds * _COST_PER_AUDIO_SEC
        entries = session.transcript
        t0 = entries[0].timestamp if entries else 0

        def ts_tag(t: float) -> str:
            m, s = divmod(int(t - t0), 60)
            return f"[{m:02d}:{s:02d}]"

        # ── summary.txt ───────────────────────────────────────────────────
        (session_dir / "summary.txt").write_text("\n".join([
            f"Session ended: {datetime.now().isoformat()}",
            f"Runtime:       {runtime/60:.1f} min ({runtime:.0f}s)",
            f"Audio billed:  {_billed_seconds:.0f}s",
            f"Est. cost:     ${cost:.4f} USD",
            f"Turns:         {len(entries)}",
            f"Captions:      {broadcaster.caption_count}",
            f"Model:         {GEMINI_MODEL}",
        ]), encoding="utf-8")

        # ── ko.txt ────────────────────────────────────────────────────────
        (session_dir / "ko.txt").write_text("\n".join(
            f"{ts_tag(e.timestamp)}  {e.korean}" for e in entries
        ), encoding="utf-8")

        # ── en.txt ────────────────────────────────────────────────────────
        (session_dir / "en.txt").write_text("\n".join(
            f"{ts_tag(e.timestamp)}  {e.english}" for e in entries
        ), encoding="utf-8")

        # ── aligned.txt ───────────────────────────────────────────────────
        aligned = []
        for e in entries:
            tag = ts_tag(e.timestamp)
            aligned.append(f"{tag}  KO: {e.korean}")
            aligned.append(f"        EN: {e.english}")
            aligned.append("")
        (session_dir / "aligned.txt").write_text("\n".join(aligned), encoding="utf-8")

        server_log.info("Session exported: %s (%d turns)", session_dir, len(entries))
    except Exception as e:
        server_log.warning("Could not write session log: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = network_cfg()
    port = cfg.get("port", 8000)
    public_url = cfg.get("public_url") or f"http://{_local_ip()}:{port}"
    live_url = f"{public_url}/live"

    global _qr_png_cache
    _qr_png_cache = _build_qr(live_url)
    server_log.info("QR code URL: %s", live_url)

    async def _ping():
        while True:
            await asyncio.sleep(15)
            broadcaster._push(CaptionEvent(kind="ping"))

    asyncio.create_task(_ping())
    yield
    await session.stop()
    audio.stop()


app = FastAPI(lifespan=lifespan)


# ── SSE caption stream ────────────────────────────────────────────────────────
async def _sse_generator(request: Request, q: asyncio.Queue) -> AsyncIterator[str]:
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=20.0)
                payload = {"kind": event.kind, "text": event.text}
                yield f"data: {json.dumps(payload)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        broadcaster.remove_client(q)


@app.get("/stream")
async def caption_stream(request: Request):
    q = broadcaster.add_client()
    return StreamingResponse(
        _sse_generator(request, q),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Binary WebSocket audio stream ─────────────────────────────────────────────
@app.websocket("/audio-stream")
async def audio_stream(ws: WebSocket):
    await ws.accept()
    q = broadcaster.add_audio_client()
    try:
        while True:
            pcm = await asyncio.wait_for(q.get(), timeout=30.0)
            await ws.send_bytes(pcm)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception:
        pass
    finally:
        broadcaster.remove_audio_client(q)


# ── Operator control API ───────────────────────────────────────────────────────
@app.post("/api/start")
async def start_service(body: dict = {}):
    global _service_running, _paused, _service_start_time, _billed_seconds, _pause_start
    device_index = body.get("device_index")
    audio.start(device_index=device_index)
    _service_start_time = time.monotonic()
    _billed_seconds = 0.0
    _paused = False
    _pause_start = None
    broadcaster.reset()
    session.reset_transcript()

    async def _pipe():
        global _billed_seconds
        CHUNK_MS = 100  # matches config default
        async for chunk in audio.chunks():
            if not _paused:
                await session.send_audio(chunk)
                _billed_seconds += CHUNK_MS / 1000.0

    asyncio.create_task(_pipe())
    await session.start()
    _service_running = True
    server_log.info("Service started")
    return {"ok": True}


@app.post("/api/stop")
async def stop_service():
    global _service_running, _paused, _pause_start
    await session.stop()
    audio.stop()
    _write_session_log()
    _service_running = False
    _paused = False
    _pause_start = None
    server_log.info("Service stopped")
    return {"ok": True}


@app.post("/api/pause")
async def pause_service():
    global _paused, _pause_start
    if _service_running and not _paused:
        _paused = True
        _pause_start = time.monotonic()
        broadcaster._push(CaptionEvent(kind="paused"))
        server_log.info("Service paused")
    return {"ok": True, "paused": _paused}


@app.post("/api/resume")
async def resume_service():
    global _paused, _pause_start
    if _service_running and _paused:
        _paused = False
        _pause_start = None
        broadcaster._push(CaptionEvent(kind="resumed"))
        server_log.info("Service resumed")
    return {"ok": True, "paused": _paused}


@app.get("/api/status")
async def get_status():
    a = audio.state
    s = session.state
    runtime = _runtime_seconds()
    cost = _billed_seconds * _COST_PER_AUDIO_SEC
    return {
        "service_running": _service_running,
        "paused": _paused,
        "runtime_s": round(runtime, 1),
        "cost_usd": round(cost, 4),
        "billed_audio_s": round(_billed_seconds, 1),
        "audio": {
            "status": a.status,
            "level": round(a.level_rms, 1),
            "device": a.device_name,
        },
        "session": {
            "status": s.status,
            "reconnect_count": s.reconnect_count,
            "last_event": s.last_event,
            "latency_ms": round(s.last_latency_ms, 1),
            "model": GEMINI_MODEL,
        },
        "attendees": broadcaster.client_count,
        "captions": broadcaster.caption_count,
    }


@app.get("/api/devices")
async def get_devices():
    return [
        {"index": d.index, "name": d.name,
         "channels": d.max_input_channels, "rate": int(d.default_sample_rate)}
        for d in list_input_devices()
    ]


@app.post("/api/devices/select")
async def select_device(body: dict):
    idx = int(body["index"])
    save_audio_device(idx)
    return {"ok": True, "index": idx}


@app.get("/api/qr.png")
async def qr_png():
    if _qr_png_cache is None:
        return Response(status_code=503)
    return Response(content=_qr_png_cache, media_type="image/png")


@app.get("/logo.webp")
async def get_logo():
    logo_path = Path(__file__).parent / "pca-logo-white-small.webp"
    if not logo_path.exists():
        return Response(status_code=404)
    with open(logo_path, "rb") as f:
        content = f.read()
    return Response(content=content, media_type="image/webp")


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/help", response_class=HTMLResponse)
async def help_page():
    help_path = Path(__file__).parent.parent / "how_to_use.html"
    if not help_path.exists():
        return HTMLResponse("Help file not found", status_code=404)
    return HTMLResponse(help_path.read_text(encoding="utf-8"))


@app.get("/live", response_class=HTMLResponse)
async def attendee_page():
    return _ATTENDEE_HTML


@app.get("/", response_class=HTMLResponse)
async def operator_page():
    return _OPERATOR_HTML


# ── Embedded HTML ─────────────────────────────────────────────────────────────

_ATTENDEE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#1a2a42">
<title>Live Translation — Starkville Korean Church</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Noto+Sans+KR:wght@400;500&family=Noto+Serif+KR:wght@600;700&family=Source+Serif+4:opsz,wght@8..60,600;8..60,700&display=swap" rel="stylesheet">
<style>
  :root {
    --color-navy-900: #0f1b2d;
    --color-navy-800: #1a2a42;
    --color-navy-700: #243757;
    --color-navy-600: #2e4a6e;
    --color-navy-100: #e8edf4;
    --color-navy-50: #f3f6fa;
    --color-gold-500: #b8923e;
    --color-gold-400: #c9a555;
    --color-gold-300: #d4b872;
    --color-gold-100: #f5edd8;
    --color-warm-white: #faf8f5;
    --color-warm-50: #f5f2ed;
    --color-warm-100: #eae5dc;
    --color-text-primary: #1a1a1a;
    --color-text-secondary: #4a4a4a;
    --color-text-muted: #7a7a7a;
    --color-text-inverse: #faf8f5;
    --color-error: #a33b3b;
    --color-success: #3b6e4f;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--color-warm-white);
    color: var(--color-text-primary);
    font-family: 'Noto Sans KR', 'Inter', system-ui, sans-serif;
    display: flex;
    flex-direction: column;
    height: 100dvh;
    overflow: hidden;
    line-height: 1.75;
  }

  /* Header Branding */
  .church-header {
    background: var(--color-navy-800);
    color: var(--color-text-inverse);
    padding: 12px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 3px solid var(--color-gold-300);
    flex-shrink: 0;
  }
  .header-content {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .church-logo {
    width: 22px;
    height: auto;
  }
  .church-titles {
    display: flex;
    flex-direction: column;
  }
  .kr-title {
    font-family: 'Noto Serif KR', serif;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.02em;
    line-height: 1.2;
  }
  .en-title {
    font-family: 'Source Serif 4', Georgia, serif;
    font-size: 10px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.8;
    line-height: 1.2;
  }
  .status-pill {
    font-family: 'Noto Sans KR', sans-serif;
    font-size: 11px;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 99px;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: rgba(255,255,255,0.1);
    color: var(--color-text-muted);
    transition: all 0.3s ease;
  }
  .status-pill.ok {
    background: rgba(59, 110, 79, 0.2);
    color: #4ade80;
  }
  .status-pill.warn {
    background: rgba(201, 165, 85, 0.2);
    color: #fbd58d;
  }
  .status-pill.err {
    background: rgba(163, 59, 59, 0.2);
    color: #fca5a5;
  }

  /* Control Bar */
  #controls {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 20px;
    background: var(--color-warm-50);
    border-bottom: 1px solid var(--color-warm-100);
    flex-shrink: 0;
    gap: 20px;
  }
  .control-group {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
  }
  .control-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-secondary);
    white-space: nowrap;
  }
  #font-range {
    flex: 1;
    accent-color: var(--color-gold-500);
    height: 4px;
    border-radius: 2px;
    cursor: pointer;
    max-width: 300px;
  }
  .ctl-btn {
    background: var(--color-warm-white);
    border: 1px solid var(--color-warm-100);
    color: var(--color-navy-900);
    padding: 6px 14px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .ctl-btn:hover {
    background: var(--color-navy-50);
    border-color: var(--color-navy-100);
  }
  .ctl-btn.active {
    background: var(--color-gold-500);
    color: var(--color-text-inverse);
    border-color: var(--color-gold-500);
  }
  .ctl-btn.active:hover {
    background: var(--color-gold-400);
  }

  /* Caption Flow Display */
  #history {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    width: 100%;
    box-sizing: border-box;
  }
  .history-spacer {
    margin-top: auto;
  }
  .history-inner {
    max-width: 42rem;
    width: 100%;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .line {
    font-family: 'Inter', 'Noto Sans KR', system-ui, sans-serif;
    font-size: var(--fs, 28px);
    line-height: 1.75;
    color: var(--color-text-secondary);
    animation: slideUp 0.4s ease-out;
  }
  
  #current-wrapper {
    background: var(--color-warm-50);
    border-top: 1px solid var(--color-warm-100);
    flex-shrink: 0;
    width: 100%;
  }
  #current {
    max-width: 42rem;
    width: 100%;
    margin: 0 auto;
    padding: 20px 24px;
    box-sizing: border-box;
    font-family: 'Inter', 'Noto Sans KR', system-ui, sans-serif;
    font-size: var(--fs, 28px);
    line-height: 1.75;
    color: var(--color-navy-900);
    font-weight: 500;
    min-height: 3.5em;
  }

  @keyframes slideUp {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }

  /* Earphone warning modal */
  #earphone-modal {
    position: fixed;
    inset: 0;
    background: rgba(15, 27, 45, 0.6);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    padding: 24px;
  }
  #earphone-modal.hidden { display: none; }
  .modal-box {
    background: var(--color-warm-white);
    border-radius: 12px;
    border: 1px solid var(--color-warm-100);
    box-shadow: 0 10px 30px rgba(15, 27, 45, 0.15);
    padding: 32px 24px;
    max-width: 380px;
    text-align: center;
  }
  .modal-box h2 {
    font-family: 'Noto Serif KR', serif;
    font-size: 20px;
    color: var(--color-navy-900);
    margin-bottom: 12px;
  }
  .modal-box p {
    font-size: 15px;
    color: var(--color-text-secondary);
    line-height: 1.7;
    margin-bottom: 24px;
  }
  .modal-box .icon {
    font-size: 40px;
    margin-bottom: 12px;
    color: var(--color-gold-500);
  }
  .modal-confirm {
    background: var(--color-gold-500);
    color: var(--color-text-inverse);
    border: none;
    padding: 12px 24px;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 700;
    cursor: pointer;
    width: 100%;
    transition: background 0.2s;
  }
  .modal-confirm:hover {
    background: var(--color-gold-400);
  }
  .modal-skip {
    background: none;
    border: none;
    color: var(--color-text-muted);
    font-size: 13px;
    cursor: pointer;
    margin-top: 14px;
    width: 100%;
    text-decoration: underline;
  }
</style>
</head>
<body>

<div id="earphone-modal" class="hidden">
  <div class="modal-box">
    <div class="icon">🎧</div>
    <h2>Use Earphones</h2>
    <p>Audio translation will play through your device. Please use wired or Bluetooth earphones to avoid disturbing the service.</p>
    <button class="modal-confirm" id="modal-ok">I have earphones — enable audio</button>
    <button class="modal-skip" id="modal-skip">Text only</button>
  </div>
</div>

<header class="church-header">
  <div class="header-content">
    <img src="/logo.webp" alt="PCA Logo" class="church-logo">
    <div class="church-titles">
      <span class="kr-title" style="font-family: 'Source Serif 4', Georgia, serif;">Starkville Korean Church (PCA)</span>
    </div>
  </div>
  <div class="header-status">
    <span id="status-pill" class="status-pill warn">● Connecting</span>
  </div>
</header>

<div id="controls">
  <div class="control-group">
    <span class="control-label">Font Size</span>
    <input id="font-range" type="range" min="20" max="56" value="28">
  </div>
  <button id="audio-btn" class="ctl-btn" title="Enable audio translation">🔇 Audio Off</button>
</div>

<div id="history">
  <div class="history-spacer"></div>
  <div class="history-inner" id="history-inner"></div>
</div>

<div id="current-wrapper">
  <div id="current">Connecting…</div>
</div>

<script>
const historyEl = document.getElementById('history');
const historyInner = document.getElementById('history-inner');
const current = document.getElementById('current');
const pill = document.getElementById('status-pill');
const fontRange = document.getElementById('font-range');
const audioBtn = document.getElementById('audio-btn');
const modal = document.getElementById('earphone-modal');

// ── Font size ──────────────────────────────────────────────────────────────
fontRange.addEventListener('input', () => {
  document.documentElement.style.setProperty('--fs', fontRange.value + 'px');
});

// ── Wake Lock ──────────────────────────────────────────────────────────────
async function requestWakeLock() {
  try { await navigator.wakeLock.request('screen'); } catch {}
}
requestWakeLock();
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') requestWakeLock();
});

// ── Status Pill ─────────────────────────────────────────────────────────────
function setStatus(state) {
  pill.className = 'status-pill';
  if (state === 'ok') {
    pill.classList.add('ok');
    pill.textContent = '● Live';
  } else if (state === 'warn') {
    pill.classList.add('warn');
    pill.textContent = '● Connecting';
  } else if (state === 'err') {
    pill.classList.add('err');
    pill.textContent = '● Error';
  }
}

// ── Audio engine (Web Audio API, 24kHz PCM16 mono) ────────────────────────
let audioCtx = null;
let audioEnabled = false;
let audioWs = null;
let nextPlayAt = 0;
const SAMPLE_RATE = 24000;

function ensureAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
    nextPlayAt = audioCtx.currentTime;
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();
}

function playPCM16(arrayBuffer) {
  if (!audioEnabled || !audioCtx) return;
  const raw = new Int16Array(arrayBuffer);
  const buf = audioCtx.createBuffer(1, raw.length, SAMPLE_RATE);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < raw.length; i++) ch[i] = raw[i] / 32768;
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(audioCtx.destination);
  const now = audioCtx.currentTime;
  if (nextPlayAt < now) nextPlayAt = now + 0.05;
  src.start(nextPlayAt);
  nextPlayAt += buf.duration;
}

function connectAudio() {
  if (audioWs) { audioWs.close(); audioWs = null; }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  audioWs = new WebSocket(proto + '//' + location.host + '/audio-stream');
  audioWs.binaryType = 'arraybuffer';
  audioWs.onmessage = (e) => playPCM16(e.data);
  audioWs.onerror = () => {};
  audioWs.onclose = () => {
    audioWs = null;
    if (audioEnabled) setTimeout(connectAudio, 3000); // reconnect if still wanted
  };
}

function disconnectAudio() {
  if (audioWs) { audioWs.close(); audioWs = null; }
}

// ── Earphone modal ─────────────────────────────────────────────────────────
function enableAudio() {
  ensureAudioCtx();
  audioEnabled = true;
  connectAudio();
  audioBtn.textContent = '🔊 Audio On';
  audioBtn.classList.add('active');
  modal.classList.add('hidden');
}

document.getElementById('modal-ok').addEventListener('click', enableAudio);
document.getElementById('modal-skip').addEventListener('click', () => {
  modal.classList.add('hidden');
});

audioBtn.addEventListener('click', () => {
  if (audioEnabled) {
    audioEnabled = false;
    disconnectAudio();
    audioBtn.textContent = '🔇 Audio Off';
    audioBtn.classList.remove('active');
  } else {
    modal.classList.remove('hidden');
  }
});

// ── SSE caption stream (captions only — audio is on WS /audio-stream) ─────
let es;
function connect() {
  es = new EventSource('/stream');
  es.onopen = () => { setStatus('ok'); current.textContent = ''; };
  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.kind === 'update') {
      current.textContent = msg.text;
    } else if (msg.kind === 'commit') {
      if (msg.text.trim()) {
        const div = document.createElement('div');
        div.className = 'line';
        div.textContent = msg.text;
        historyInner.appendChild(div);
        historyEl.scrollTop = historyEl.scrollHeight;
        while (historyInner.children.length > 25) historyInner.removeChild(historyInner.firstChild);
      }
      current.textContent = '';
    } else if (msg.kind === 'unavailable') {
      current.textContent = 'Translation unavailable';
      setStatus('err');
    }
  };
  es.onerror = () => {
    setStatus('warn');
    current.textContent = 'Reconnecting…';
    es.close();
    setTimeout(connect, 3000);
  };
}
connect();
</script>
</body>
</html>"""


_OPERATOR_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#1a2a42">
<title>실시간 자막 번역 관리자 콘솔 — 스탁빌 한인 교회</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Noto+Sans+KR:wght@400;500&family=Noto+Serif+KR:wght@600;700&family=Source+Serif+4:opsz,wght@8..60,600;8..60,700&display=swap" rel="stylesheet">
<style>
  :root {
    --color-navy-900: #0f1b2d;
    --color-navy-800: #1a2a42;
    --color-navy-700: #243757;
    --color-navy-600: #2e4a6e;
    --color-navy-100: #e8edf4;
    --color-navy-50: #f3f6fa;
    --color-gold-500: #b8923e;
    --color-gold-400: #c9a555;
    --color-gold-300: #d4b872;
    --color-gold-100: #f5edd8;
    --color-warm-white: #faf8f5;
    --color-warm-50: #f5f2ed;
    --color-warm-100: #eae5dc;
    --color-text-primary: #1a1a1a;
    --color-text-secondary: #4a4a4a;
    --color-text-muted: #7a7a7a;
    --color-text-inverse: #faf8f5;
    --color-error: #a33b3b;
    --color-success: #3b6e4f;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Noto Sans KR', 'Inter', system-ui, sans-serif;
    background: var(--color-warm-white);
    color: var(--color-text-primary);
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    line-height: 1.6;
  }

  /* Header */
  header {
    background: var(--color-navy-800);
    color: var(--color-text-inverse);
    padding: 14px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 3px solid var(--color-gold-300);
    flex-shrink: 0;
  }
  .header-content {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .church-logo {
    width: 22px;
    height: auto;
  }
  .church-titles {
    display: flex;
    flex-direction: column;
  }
  .kr-title {
    font-family: 'Noto Serif KR', serif;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.02em;
    line-height: 1.2;
  }
  .en-title {
    font-family: 'Source Serif 4', Georgia, serif;
    font-size: 10px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.8;
    line-height: 1.2;
  }
  .header-side {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  
  .badge {
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 99px;
    font-weight: 700;
    text-transform: uppercase;
  }
  .badge-green { background: var(--color-success); color: #fff; }
  .badge-red   { background: var(--color-error); color: #fff; }
  .badge-amber { background: var(--color-gold-500); color: #fff; }
  .badge-gray  { background: var(--color-text-muted); color: #fff; }
  .badge-blue  { background: var(--color-navy-600); color: #fff; }

  /* Main container */
  main {
    flex: 1;
    padding: 24px;
    display: flex;
    flex-direction: row;
    gap: 24px;
    align-items: flex-start;
    max-width: 1024px;
    margin: 0 auto;
    width: 100%;
  }
  .col-left  { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 20px; }
  .col-right { width: 300px; flex-shrink: 0; display: flex; flex-direction: column; gap: 20px; }
  @media (max-width: 768px) {
    main { flex-direction: column; padding: 16px; }
    .col-right { width: 100%; }
  }

  /* Cards */
  .card {
    background: var(--color-warm-50);
    border: 1px solid var(--color-warm-100);
    border-radius: 12px;
    padding: 20px;
  }
  .card h2 {
    font-family: 'Noto Serif KR', serif;
    font-size: 13px;
    color: var(--color-navy-900);
    text-transform: uppercase;
    letter-spacing: .08em;
    margin-bottom: 12px;
    border-left: 3px solid var(--color-gold-500);
    padding-left: 8px;
  }
  .card-toggle {
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    user-select: none;
    margin-bottom: 0;
  }
  .card-toggle .chevron { font-size: 12px; color: var(--color-text-muted); transition: transform .2s; }
  .card-toggle.open .chevron { transform: rotate(180deg); }
  .card-body { margin-top: 16px; }
  .card-body.hidden { display: none; }

  /* Controls */
  select, button {
    font-family: inherit;
    font-size: 14px;
    padding: 10px 14px;
    border-radius: 6px;
    border: 1px solid var(--color-warm-100);
    background: var(--color-warm-white);
    color: var(--color-text-primary);
    width: 100%;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  select:focus, select:hover {
    border-color: var(--color-gold-500);
    outline: none;
  }
  
  button.primary {
    background: var(--color-gold-500);
    color: var(--color-text-inverse);
    border-color: var(--color-gold-500);
    font-weight: 600;
  }
  button.primary:hover {
    background: var(--color-gold-400);
  }
  button.primary:disabled {
    background: var(--color-warm-100);
    border-color: var(--color-warm-100);
    color: var(--color-text-muted);
    cursor: not-allowed;
  }
  
  button.danger  {
    background: var(--color-error);
    color: #fff;
    border-color: var(--color-error);
    font-weight: 600;
  }
  button.danger:hover {
    background: #bd4a4a;
  }
  button.danger:disabled  {
    background: var(--color-warm-100);
    border-color: var(--color-warm-100);
    color: var(--color-text-muted);
    cursor: not-allowed;
  }
  
  button.warning {
    background: var(--color-navy-700);
    color: #fff;
    border-color: var(--color-navy-700);
    font-weight: 600;
  }
  button.warning:hover {
    background: var(--color-navy-600);
  }
  button.warning:disabled {
    background: var(--color-warm-100);
    border-color: var(--color-warm-100);
    color: var(--color-text-muted);
    cursor: not-allowed;
  }
  
  button.secondary {
    background: var(--color-warm-white);
    color: var(--color-text-primary);
    border-color: var(--color-warm-100);
  }
  button.secondary:hover {
    background: var(--color-navy-50);
  }

  /* Audio Bar & Combined Control */
  .audio-row {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
    width: 100%;
  }
  #btn-audio {
    width: 100%;
    padding: 10px 14px;
    font-size: 14px;
    font-weight: 600;
    transition: all 0.2s ease;
    background: var(--color-warm-white);
    border-color: var(--color-warm-100);
  }
  #btn-audio.on {
    background: var(--color-success);
    color: #fff;
    border-color: var(--color-success);
  }
  #btn-audio.on:hover {
    background: #46855f;
  }
  #volume-wrapper {
    width: 100%;
    align-items: center;
    gap: 10px;
    padding: 4px 0;
  }
  #vol-slider {
    flex: 1;
    accent-color: var(--color-gold-500);
    height: 4px;
    border-radius: 2px;
    cursor: pointer;
  }

  /* Level Meter */
  .meter-wrap {
    height: 6px;
    background: var(--color-warm-100);
    border-radius: 3px;
    overflow: hidden;
    margin-top: 14px;
  }
  .meter-bar {
    height: 100%;
    background: var(--color-gold-500);
    transition: width .1s;
    width: 0%;
  }
  .meter-label {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin-top: 6px;
    font-weight: 500;
  }

  /* Stat Rows */
  .stat-row {
    display: flex;
    justify-content: space-between;
    font-size: 14px;
    padding: 8px 0;
    border-bottom: 1px solid var(--color-warm-100);
  }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { color: var(--color-text-secondary); }
  .stat-val { font-weight: 500; color: var(--color-text-primary); }
  .stat-val.ok   { color: var(--color-success); }
  .stat-val.warn { color: var(--color-gold-500); }
  .stat-val.err  { color: var(--color-error); }
  .stat-val.cost { color: var(--color-navy-700); font-weight: bold; }

  /* Caption preview */
  #preview-wrap {
    height: 180px;
    overflow-y: auto;
    background: var(--color-warm-white);
    border: 1px solid var(--color-warm-100);
    border-radius: 8px;
    padding: 12px;
    margin-top: 12px;
  }
  #preview {
    font-family: 'Inter', 'Noto Sans KR', sans-serif;
    font-size: 16px;
    color: var(--color-text-primary);
    line-height: 1.7;
    white-space: pre-wrap;
  }
  /* Event log */
  #log {
    height: 150px;
    overflow-y: auto;
    font-family: 'Consolas', 'Menlo', 'Monaco', 'Courier New', monospace;
    font-size: 11px;
    color: var(--color-text-secondary);
    background: var(--color-warm-white);
    border: 1px solid var(--color-warm-100);
    border-radius: 6px;
    padding: 8px;
  }
  .log-entry {
    padding: 4px 0;
    border-bottom: 1px dotted var(--color-warm-100);
  }
  .log-entry:last-child { border-bottom: none; }

  /* Control bar */
  .ctrl-bar {
    display: flex;
    gap: 12px;
    margin-top: 8px;
  }
  .ctrl-bar button {
    flex: 1;
    font-weight: 600;
  }

  /* Earphone Warning Modal */
  #earphone-modal {
    position: fixed;
    inset: 0;
    background: rgba(15, 27, 45, 0.6);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    padding: 24px;
  }
  #earphone-modal.hidden { display: none; }
  .modal-box {
    background: var(--color-warm-white);
    border-radius: 12px;
    border: 1px solid var(--color-warm-100);
    box-shadow: 0 10px 30px rgba(15, 27, 45, 0.15);
    padding: 32px 24px;
    max-width: 360px;
    text-align: center;
  }
  .modal-box .icon { font-size: 40px; margin-bottom: 12px; color: var(--color-gold-500); }
  .modal-box h2 { font-family: 'Noto Serif KR', serif; font-size: 18px; margin-bottom: 10px; color: var(--color-navy-900); }
  .modal-box p { font-size: 14px; color: var(--color-text-secondary); line-height: 1.6; margin-bottom: 24px; }
  .modal-confirm {
    background: var(--color-gold-500);
    color: var(--color-text-inverse);
    border: none;
    padding: 12px 24px;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 700;
    cursor: pointer;
    width: 100%;
    transition: background 0.2s;
  }
  .modal-confirm:hover { background: var(--color-gold-400); }
  .modal-skip {
    background: none;
    border: none;
    color: var(--color-text-muted);
    font-size: 13px;
    cursor: pointer;
    margin-top: 14px;
    width: 100%;
    text-decoration: underline;
  }
</style>
</head>
<body>

<div id="earphone-modal" class="hidden">
  <div class="modal-box">
    <div class="icon">🎧</div>
    <h2>이어폰 사용 안내</h2>
    <p>오디오 모니터링 기능은 소리가 스피커로 재생되지 않도록 주의해 주세요. 예배에 방해가 되지 않도록 <strong>유선 또는 블루투스 이어폰</strong>을 사용하시기 바랍니다.</p>
    <button class="modal-confirm" id="modal-ok">이어폰 연결됨 (오디오 켜기)</button>
    <button class="modal-skip" id="modal-skip">자막만 보기</button>
  </div>
</div>

<header>
  <div class="header-content">
    <img src="/logo.webp" alt="PCA Logo" class="church-logo">
    <div class="church-titles">
      <span class="kr-title" style="font-family: 'Source Serif 4', Georgia, serif;">Starkville Korean Church (PCA)</span>
    </div>
  </div>
  <div class="header-side">
    <a href="/help" target="_blank" style="font-size:12px; color:var(--color-gold-500); text-decoration:underline; font-weight:600; margin-right:16px;">📖 사용 가이드 (Guide)</a>
    <span style="font-size:12px; opacity:0.8; font-weight:500;">Live Translation Console</span>
    <span id="hdr-badge" class="badge badge-gray">Stopped</span>
  </div>
</header>

<main>

  <div class="col-left">

    <!-- Input device + level meter -->
    <div class="card">
      <h2>입력 장치 설정 (Input Device)</h2>
      <select id="device-select"><option>Loading…</option></select>
      <div class="meter-wrap"><div class="meter-bar" id="level-bar"></div></div>
      <div class="meter-label" id="level-label">입력 레벨 (Input level)</div>
    </div>

    <!-- Status -->
    <div class="card">
      <h2>상태 모니터 (Status)</h2>
      <div class="stat-row"><span class="stat-label">오디오 입력 (Audio in)</span><span class="stat-val" id="stat-audio">—</span></div>
      <div class="stat-row"><span class="stat-label">Gemini 세션 (Gemini)</span><span class="stat-val" id="stat-session">—</span></div>
      <div class="stat-row"><span class="stat-label">지연 속도 (Latency)</span><span class="stat-val" id="stat-latency">—</span></div>
      <div class="stat-row"><span class="stat-label">접속자 수 (Attendees)</span><span class="stat-val" id="stat-attendees">0</span></div>
      <div class="stat-row"><span class="stat-label">재연결 횟수 (Reconnects)</span><span class="stat-val" id="stat-reconnects">0</span></div>
      <div class="stat-row"><span class="stat-label">자막 생성 수 (Captions)</span><span class="stat-val" id="stat-captions">0</span></div>
      <div class="stat-row"><span class="stat-label">진행 시간 (Runtime)</span><span class="stat-val" id="stat-runtime">—</span></div>
      <div class="stat-row"><span class="stat-label">예상 비용 (Est. cost)</span><span class="stat-val cost" id="stat-cost">—</span></div>
      <div class="stat-row"><span class="stat-label">사용 모델 (Model)</span><span class="stat-val" id="stat-model" style="font-size:12px;color:var(--color-text-muted)">—</span></div>
    </div>

    <!-- Caption preview -->
    <div class="card">
      <h2>실시간 자막 미리보기 (Preview)</h2>
      <div id="preview-wrap"><div id="preview">—</div></div>
    </div>

    <!-- Control buttons -->
    <div class="ctrl-bar">
      <button class="primary"  id="btn-start">▶ Start</button>
      <button class="warning"  id="btn-pause" disabled>⏸ Pause</button>
      <button class="danger"   id="btn-stop"  disabled>■ Stop</button>
    </div>

  </div><!-- /col-left -->

  <div class="col-right">

    <!-- QR code -->
    <div class="card">
      <h2 class="card-toggle open" id="qr-toggle">
        접속 QR 코드 <span class="chevron">▼</span>
      </h2>
      <div class="card-body" id="qr-body">
        <img id="qr-img" src="/api/qr.png" alt="QR code" style="width:100%;height:auto;display:block;">
      </div>
    </div>

    <!-- Audio playback -->
    <div class="card">
      <h2>음성 통역 모니터 (Audio)</h2>
      <div class="card-body">
        <div class="audio-row">
          <button id="btn-audio" class="secondary">🔇 Playback Muted</button>
          <div id="volume-wrapper" style="display: none; align-items: center; gap: 8px; margin-top: 10px; width: 100%;">
            <span style="font-size: 13px;">🔊</span>
            <input id="vol-slider" type="range" min="0" max="1" step="0.05" value="0.8" style="flex: 1; accent-color: var(--color-gold-500);">
            <span id="vol-label" style="font-size: 12px; color: var(--color-text-muted);">80%</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Event log (foldable, collapsed by default) -->
    <div class="card">
      <h2 class="card-toggle" id="log-toggle">
        이벤트 로그 (Event Log) <span class="chevron">▼</span>
      </h2>
      <div class="card-body hidden" id="log-body">
        <div id="log"><div class="log-entry" style="color:var(--color-text-muted)">No events yet…</div></div>
      </div>
    </div>

  </div><!-- /col-right -->

</main>

<script>
let polling = null;
let captionEs = null;
let previewLines = [];
let lastEvent = '';

const btnStart = document.getElementById('btn-start');
const btnPause = document.getElementById('btn-pause');
const btnStop  = document.getElementById('btn-stop');
const preview  = document.getElementById('preview');
const previewWrap = document.getElementById('preview-wrap');
const logEl    = document.getElementById('log');

// ── Foldable cards ──────────────────────────────────────────────────────────
['qr-toggle', 'log-toggle'].forEach(id => {
  document.getElementById(id).addEventListener('click', function() {
    this.classList.toggle('open');
    const bodyId = id === 'qr-toggle' ? 'qr-body' : 'log-body';
    document.getElementById(bodyId).classList.toggle('hidden');
  });
});

// ── Audio engine (24kHz PCM16 mono, via WS /audio-stream) ───────────────────
let audioCtx = null, gainNode = null, audioEnabled = false, audioWs = null, nextPlayAt = 0;
const SAMPLE_RATE = 24000;

function ensureAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
    gainNode = audioCtx.createGain();
    gainNode.gain.value = parseFloat(document.getElementById('vol-slider').value);
    gainNode.connect(audioCtx.destination);
    nextPlayAt = audioCtx.currentTime;
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();
}

function playPCM16(arrayBuffer) {
  if (!audioEnabled || !audioCtx) return;
  const raw = new Int16Array(arrayBuffer);
  const buf = audioCtx.createBuffer(1, raw.length, SAMPLE_RATE);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < raw.length; i++) ch[i] = raw[i] / 32768;
  const src = audioCtx.createBufferSource();
  src.buffer = buf; src.connect(gainNode);
  const now = audioCtx.currentTime;
  if (nextPlayAt < now) nextPlayAt = now + 0.05;
  src.start(nextPlayAt);
  nextPlayAt += buf.duration;
}

function connectAudio() {
  if (audioWs) { audioWs.close(); audioWs = null; }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  audioWs = new WebSocket(proto + '//' + location.host + '/audio-stream');
  audioWs.binaryType = 'arraybuffer';
  audioWs.onmessage = (e) => playPCM16(e.data);
  audioWs.onerror = () => {};
  audioWs.onclose = () => {
    audioWs = null;
    if (audioEnabled) setTimeout(connectAudio, 3000);
  };
}

function disconnectAudio() {
  if (audioWs) { audioWs.close(); audioWs = null; }
}

const modal   = document.getElementById('earphone-modal');
const btnAudio = document.getElementById('btn-audio');
const volSlider = document.getElementById('vol-slider');
const volLabel  = document.getElementById('vol-label');
const volWrapper = document.getElementById('volume-wrapper');

function enableAudio() {
  ensureAudioCtx();
  audioEnabled = true;
  connectAudio();
  btnAudio.textContent = '🔊 Playback Enabled';
  btnAudio.classList.add('on');
  volWrapper.style.display = 'flex';
  modal.classList.add('hidden');
  updateVolLabel();
}

function updateVolLabel() {
  volLabel.textContent = Math.round(parseFloat(volSlider.value) * 100) + '%';
}

document.getElementById('modal-ok').addEventListener('click', enableAudio);
document.getElementById('modal-skip').addEventListener('click', () => modal.classList.add('hidden'));

btnAudio.addEventListener('click', () => {
  if (audioEnabled) {
    audioEnabled = false;
    disconnectAudio();
    btnAudio.textContent = '🔇 Playback Muted';
    btnAudio.classList.remove('on');
    volWrapper.style.display = 'none';
  } else {
    modal.classList.remove('hidden');
  }
});

volSlider.addEventListener('input', () => {
  if (gainNode) gainNode.gain.value = parseFloat(volSlider.value);
  updateVolLabel();
});

// ── Device list ──────────────────────────────────────────────────────────────
async function loadDevices() {
  const devices = await fetch('/api/devices').then(r => r.json());
  const sel = document.getElementById('device-select');
  sel.innerHTML = devices.map(d => `<option value="${d.index}">[${d.index}] ${d.name}</option>`).join('');
}

// ── Start ─────────────────────────────────────────────────────────────────────
btnStart.addEventListener('click', async () => {
  btnStart.disabled = true; btnStart.textContent = '⏳ Starting…';
  const idx = parseInt(document.getElementById('device-select').value);
  try {
    await fetch('/api/start', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({device_index: idx}) });
    btnPause.disabled = false; btnStop.disabled = false;
    previewLines = []; preview.textContent = '';
    document.getElementById('stat-runtime').textContent = '—';
    document.getElementById('stat-cost').textContent = '—';
    startStatusPoll(); connectSSE();
  } catch {
    btnStart.disabled = false; btnStart.textContent = '▶ Start';
  }
});

// ── Pause / Resume ────────────────────────────────────────────────────────────
let _paused = false;
btnPause.addEventListener('click', async () => {
  if (_paused) {
    await fetch('/api/resume', {method:'POST'});
    _paused = false; btnPause.textContent = '⏸ Pause'; btnPause.className = 'warning';
  } else {
    await fetch('/api/pause', {method:'POST'});
    _paused = true; btnPause.textContent = '▶ Resume'; btnPause.className = 'primary';
  }
});

// ── Stop ──────────────────────────────────────────────────────────────────────
btnStop.addEventListener('click', async () => {
  btnStop.disabled = true; btnStop.textContent = '⏳ Stopping…';
  try { await fetch('/api/stop', {method:'POST'}); }
  finally {
    btnStart.disabled = false; btnStart.textContent = '▶ Start';
    btnPause.disabled = true;  btnPause.textContent = '⏸ Pause'; btnPause.className = 'warning';
    btnStop.disabled = true;   btnStop.textContent = '■ Stop';
    _paused = false;
    if (captionEs) { captionEs.close(); captionEs = null; }
  }
});

// ── Status poll ───────────────────────────────────────────────────────────────
const SESSION_COLOR = { connected:'ok', reconnecting:'warn', failed:'err', connecting:'warn', stopped:'' };
const AUDIO_COLOR   = { connected:'ok', no_signal:'warn', disconnected:'err', stopped:'' };

function fmtRuntime(s) {
  return Math.floor(s/60) + ':' + String(Math.floor(s%60)).padStart(2,'0');
}

function addLog(msg) {
  const d = document.createElement('div');
  d.className = 'log-entry';
  d.textContent = new Date().toLocaleTimeString() + ' — ' + msg;
  logEl.prepend(d);
  while (logEl.children.length > 100) logEl.removeChild(logEl.lastChild);
}

function startStatusPoll() {
  if (polling) clearInterval(polling);
  polling = setInterval(async () => {
    let st;
    try { st = await fetch('/api/status').then(r => r.json()); } catch { return; }

    document.getElementById('level-bar').style.width = st.audio.level + '%';
    document.getElementById('level-label').textContent =
      st.audio.level > 0 ? '레벨: ' + Math.round(st.audio.level) + '%' : '입력 레벨 — 신호 없음';

    const auEl = document.getElementById('stat-audio');
    auEl.textContent = st.audio.status + (st.audio.device ? ' — ' + st.audio.device : '');
    auEl.className = 'stat-val ' + (AUDIO_COLOR[st.audio.status] || '');

    const seEl = document.getElementById('stat-session');
    seEl.textContent = st.session.status + (st.session.last_event ? ' (' + st.session.last_event + ')' : '');
    seEl.className = 'stat-val ' + (SESSION_COLOR[st.session.status] || '');

    document.getElementById('stat-latency').textContent = st.session.latency_ms ? st.session.latency_ms + ' ms' : '—';
    document.getElementById('stat-attendees').textContent = st.attendees;
    document.getElementById('stat-reconnects').textContent = st.session.reconnect_count;
    document.getElementById('stat-captions').textContent = st.captions || 0;
    document.getElementById('stat-model').textContent = st.session.model || '—';
    if (st.service_running) {
      document.getElementById('stat-runtime').textContent = fmtRuntime(st.runtime_s);
      document.getElementById('stat-cost').textContent = '$' + st.cost_usd.toFixed(4);
    }

    if (st.session.last_event && st.session.last_event !== lastEvent) {
      addLog(st.session.last_event);
      lastEvent = st.session.last_event;
    }

    const badge = document.getElementById('hdr-badge');
    if (!st.service_running) {
      badge.textContent = 'Stopped'; badge.className = 'badge badge-gray';
      if (!btnStop.disabled) {
        btnStart.disabled = false; btnStart.textContent = '▶ Start';
        btnPause.disabled = true;
        btnStop.disabled = true; btnStop.textContent = '■ Stop';
      }
    } else if (st.paused) {
      badge.textContent = 'Paused'; badge.className = 'badge badge-blue';
    } else if (st.session.status === 'connected') {
      badge.textContent = 'Live'; badge.className = 'badge badge-green';
    } else if (st.session.status === 'failed') {
      badge.textContent = 'Error'; badge.className = 'badge badge-red';
    } else {
      badge.textContent = 'Starting'; badge.className = 'badge badge-amber';
    }
  }, 1000);
}

// ── SSE: captions only (audio is on WS /audio-stream) ────────────────────────
function connectSSE() {
  if (captionEs) captionEs.close();
  captionEs = new EventSource('/stream');
  captionEs.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.kind === 'ping')  { return; }
    if (msg.kind === 'update') {
      if (previewLines.length === 0) previewLines.push('');
      previewLines[previewLines.length - 1] = msg.text;
    } else if (msg.kind === 'commit') {
      if (previewLines.length > 50) previewLines.shift();
      previewLines.push('');
    } else if (msg.kind === 'unavailable') {
      previewLines.push('[Translation unavailable]');
    } else if (msg.kind === 'paused') {
      previewLines.push('— Paused —');
    } else if (msg.kind === 'resumed') {
      previewLines.push('— Resumed —');
    }
    preview.textContent = previewLines.join('\\n');
    previewWrap.scrollTop = previewWrap.scrollHeight;
  };
}

loadDevices();
startStatusPoll();
</script>
</body>
</html>"""
