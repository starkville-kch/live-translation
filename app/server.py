"""
app/server.py — FastAPI Web Server
====================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
The central hub of the live translation system.  Wires together audio
capture, the Gemini session manager, and the broadcast layer, then exposes
them via HTTP/WebSocket endpoints served by Uvicorn.

HTTP route table
----------------
GET  /                 — Operator console HTML (bilingual Korean/English)
GET  /live             — Attendee caption page HTML (English captions + audio)
GET  /help             — Bilingual volunteer guide (how_to_use.html)
GET  /stream           — SSE text caption stream (event-stream MIME type)
WS   /audio-stream     — Binary WebSocket: raw 24 kHz PCM16 audio chunks
GET  /api/status       — JSON snapshot of audio, session, cost, and attendee state
GET  /api/devices      — JSON list of available PyAudio input devices
POST /api/start        — Start audio capture + Gemini session (body: {device_index})
POST /api/stop         — Stop service, flush transcripts, export session logs
POST /api/pause        — Pause billing & audio forwarding (mic still captured)
POST /api/resume        — Resume forwarding after pause
POST /api/devices/select — Persist selected device_index to config.yaml
GET  /api/qr.png       — Dynamically generated branded QR code PNG
GET  /logo.webp        — PCA logo asset (served from app/ directory)

Global state
------------
``_service_running`` — bool, True between /api/start and /api/stop
``_paused``          — bool, audio forwarded to Gemini only when False
``_service_start_time`` — monotonic timestamp for runtime calculation
``_billed_seconds``  — cumulative audio seconds forwarded (used for cost estimate)
``_qr_png_cache``    — bytes, the PNG image generated once at startup lifespan

Cost estimation
---------------
Gemini 3.5 Live Translate Paid Tier combined rate:
  Input audio  $0.0053/min + Output audio $0.0315/min = $0.0368/min total
  Encoded in ``_COST_PER_AUDIO_SEC`` = 0.0368 / 60.0

QR code design
--------------
Generated via ``_build_qr()`` using the ``qrcode`` + ``Pillow`` libraries:
  • ERROR_CORRECT_H (30 % recovery) to tolerate the central logo overlay
  • RoundedModuleDrawer for modern rounded data dots
  • Presbyterian Navy (#1a2a42) data modules
  • Pixel-level gold (#b89445) recoloring of the three 7×7 finder patterns
  • White quiet-zone ellipse → navy inner circle → white PCA logo overlay

Session transcript export
-------------------------
On /api/stop, ``_write_session_log()`` writes four files to
``logs/sessions/YYYYMMDD_HHMMSS/``:
  summary.txt   — runtime, cost, model, turn count
  ko.txt        — Korean source turns with timestamps
  en.txt        — English translation turns with timestamps
  aligned.txt   — Korean/English pairs interleaved for easy review
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

from app.audio import AudioCapture, AudioStatus, list_input_devices
from app.broadcast import CaptionBroadcaster, CaptionEvent
from app.config import logging_cfg, network_cfg, save_audio_device, audio_cfg, save_auto_stop_timeout
from app.events import operator_events
from app.gemini_session import GEMINI_MODEL
from app.gemini_session import GeminiSession, SessionStatus
from app.glossary import GlossaryCorrector
from app.logger import server_log

# Gemini 3.5 Live Translate pricing (Paid Tier):
# Audio Input: $0.0053/min (~$0.00008833/sec)
# Audio Output: $0.0315/min (~$0.000525/sec)
# Total: $0.0368/min (~$0.00061333/sec)
_COST_PER_AUDIO_SEC = 0.0368 / 60.0

# ── Singletons ────────────────────────────────────────────────────────────────
from enum import Enum

class ServiceState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"

_glossary = GlossaryCorrector()
broadcaster = CaptionBroadcaster(glossary=_glossary)
audio = AudioCapture()
session = GeminiSession(
    on_caption=broadcaster.on_caption_delta,
    on_source_transcript=broadcaster.on_source_delta,
    on_audio_chunk=broadcaster.on_audio_chunk,
    glossary=_glossary,
)

_state_lock = asyncio.Lock()
_state = ServiceState.STOPPED
_qr_png_cache: bytes | None = None
_paused = False
_service_start_time: float | None = None   # monotonic, set when service starts
_billed_seconds: float = 0.0               # audio seconds sent to Gemini
_pause_start: float | None = None          # monotonic when paused
_auto_restart_attempt = 0
_auto_restart_reason = ""
_auto_restart_task: asyncio.Task | None = None



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
    from PIL import Image, ImageDraw
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
    from qrcode.image.styles.colormasks import SolidFillColorMask

    # ERROR_CORRECT_H gives ~30% module recovery — required for a central logo overlay
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # 1. Base: dark-blue rounded modules on white
    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        color_mask=SolidFillColorMask(
            back_color=(255, 255, 255),
            front_color=(26, 42, 66),   # Presbyterian Navy
        ),
    ).convert("RGB")

    # 2. Pixel-level recolor: replace navy with gold (#b89445) in the three
    #    7×7 finder-pattern squares (top-left, top-right, bottom-left).
    #    We iterate each pixel in those rectangular areas and swap navy → gold.
    NAVY  = (26, 42, 66)
    GOLD  = (184, 148, 69)   # #b89445
    px    = img.load()
    bs    = qr.box_size
    border = qr.border
    n     = qr.modules_count   # total module count per side

    finder_origins = [
        (0, 0),            # top-left
        (n - 7, 0),        # top-right
        (0, n - 7),        # bottom-left
    ]

    for col, row in finder_origins:
        # pixel bounding box of this 7×7 finder pattern
        px1 = (col + border) * bs
        py1 = (row + border) * bs
        px2 = px1 + 7 * bs
        py2 = py1 + 7 * bs
        for x in range(px1, px2):
            for y in range(py1, py2):
                if px[x, y] == NAVY:
                    px[x, y] = GOLD

    # 3. Embed central PCA logo with a mandatory white quiet-zone circle buffer
    logo_path = Path(__file__).parent / "pca-logo-white-small.webp"
    if logo_path.exists():
        img = img.convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")

        total_w, total_h = img.size
        # Logo must not exceed 20 % of the QR width (per spec)
        logo_size = int(total_w * 0.20)
        logo = logo.resize((logo_size, logo_size), Image.Resampling.LANCZOS)

        cx, cy = total_w // 2, total_h // 2
        draw = ImageDraw.Draw(img)

        # Draw a solid white circle as a quiet-zone buffer *before* pasting logo
        buf_margin = int(logo_size * 0.20)   # 20 % padding around logo
        buf_r      = logo_size // 2 + buf_margin
        draw.ellipse(
            [cx - buf_r, cy - buf_r, cx + buf_r, cy + buf_r],
            fill=(255, 255, 255, 255),
        )

        # Draw navy inner circle so the white logo is visible against it
        inner_r = logo_size // 2 + int(logo_size * 0.06)
        draw.ellipse(
            [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
            fill=(26, 42, 66, 255),
        )

        # Paste the logo precisely centred inside the navy circle
        logo_x = cx - logo_size // 2
        logo_y = cy - logo_size // 2
        img.paste(logo, (logo_x, logo_y), logo)

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
        t0 = _service_start_time if _service_start_time is not None else (entries[0].timestamp if entries else 0.0)

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
    global _qr_png_cache
    cfg = network_cfg()
    port = cfg.get("port", 8000)
    public_url = cfg.get("public_url") or f"http://{_local_ip()}:{port}"
    live_url = f"{public_url}/live"

    _qr_png_cache = _build_qr(live_url)
    server_log.info("QR code URL: %s", live_url)

    operator_events.add("success", "System started", {"port": port})

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
                if event.kind == "commit":
                    runtime = _runtime_seconds()
                    m, s = divmod(int(runtime), 60)
                    payload["time_str"] = f"{m:02d}:{s:02d}"
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
async def _teardown():
    global _state
    audio.stop()
    if audio._thread and audio._thread.is_alive():
        await asyncio.get_event_loop().run_in_executor(None, audio._thread.join, 1.5)
    await session.stop()
    _write_session_log()
    while not audio._queue.empty():
        try:
            audio._queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    while not session._audio_queue.empty():
        try:
            session._audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    _state = ServiceState.STOPPED


async def _auto_stop_check():
    """Periodically checks if the mic status remains silent or disconnected.
    Stops the service if no signal is detected for the configured duration.
    """
    silence_start = None
    while _state == ServiceState.RUNNING:
        await asyncio.sleep(5.0)
        if _state != ServiceState.RUNNING:
            break

        timeout_min = audio_cfg().get("auto_stop_timeout_min", 10)
        if timeout_min <= 0:
            silence_start = None
            continue

        current_status = audio.state.status
        if current_status in (AudioStatus.NO_SIGNAL, AudioStatus.DISCONNECTED) and not _paused:
            if silence_start is None:
                silence_start = time.monotonic()
            elif time.monotonic() - silence_start >= (timeout_min * 60.0):
                elapsed_min = (time.monotonic() - silence_start) / 60.0
                server_log.warning(
                    "AUTO_STOP_TIMER fired: elapsed=%.2fmin configured_limit=%dmin",
                    elapsed_min, timeout_min
                )
                server_log.warning("Service automatically stopped: no audio signal for %d min", timeout_min)
                operator_events.add("warning", f"Auto-stop: no audio signal for {timeout_min} min")
                await stop_service()
                break
        else:
            silence_start = None


@app.post("/api/start")
async def start_service(body: dict = {}, from_auto_restart: bool = False):
    global _state, _paused, _service_start_time, _billed_seconds, _pause_start
    global _auto_restart_task, _auto_restart_attempt, _auto_restart_reason
    if not from_auto_restart:
        if _auto_restart_task and not _auto_restart_task.done():
            _auto_restart_task.cancel()
            _auto_restart_task = None
        _auto_restart_attempt = 0
        _auto_restart_reason = ""
    async with _state_lock:
        if _state in (ServiceState.RUNNING, ServiceState.STARTING):
            server_log.warning("start_service called while service is already running. Ignoring.")
            return {"ok": True, "info": "Service already running"}
        _state = ServiceState.STARTING
        try:
            await _teardown()
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
                CHUNK_MS = 100
                try:
                    async for chunk in audio.chunks():
                        if not _paused:
                            await session.send_audio(chunk)
                            _billed_seconds += CHUNK_MS / 1000.0
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    server_log.error("Audio pipe error: %s", e)

            asyncio.create_task(_pipe())
            asyncio.create_task(_auto_stop_check())
            await session.start()
            _state = ServiceState.RUNNING
            server_log.info("Service started")
            operator_events.add("success", "Translation started")
        except Exception as e:
            server_log.error("Failed to start service: %s", e)
            await _teardown()
            _state = ServiceState.STOPPED
            raise e
    return {"ok": True}


@app.post("/api/stop")
async def stop_service():
    global _state, _auto_restart_task, _auto_restart_attempt, _auto_restart_reason
    if _auto_restart_task and not _auto_restart_task.done():
        _auto_restart_task.cancel()
        _auto_restart_task = None
    _auto_restart_attempt = 0
    _auto_restart_reason = ""
    async with _state_lock:
        if _state in (ServiceState.STOPPED, ServiceState.STOPPING):
            return {"ok": True, "info": "Service already stopped"}
        _state = ServiceState.STOPPING
        await _teardown()
        server_log.info("Service stopped")
        operator_events.add("gemini", "Translation stopped")
    return {"ok": True}


@app.post("/api/shutdown")
async def shutdown_service(request: Request):
    client_host = request.client.host
    if client_host not in ("127.0.0.1", "localhost", "::1"):
        return Response("Unauthorized", status_code=403)

    import os
    import signal
    server_log.info("Shutdown requested via web interface")
    
    if _state != ServiceState.STOPPED:
        await stop_service()

    async def _graceful():
        await asyncio.sleep(1.0)
        server_log.info("Sending SIGINT to exit process gracefully")
        os.kill(os.getpid(), signal.SIGINT)

    asyncio.create_task(_graceful())
    return {"ok": True}


@app.post("/api/pause")
async def pause_service():
    global _paused, _pause_start
    if _state == ServiceState.RUNNING and not _paused:
        _paused = True
        _pause_start = time.monotonic()
        broadcaster._push(CaptionEvent(kind="paused"))
        server_log.info("Service paused")
        operator_events.add("user", "Translation paused")
    return {"ok": True, "paused": _paused}


@app.post("/api/resume")
async def resume_service():
    global _paused, _pause_start
    if _state == ServiceState.RUNNING and _paused:
        _paused = False
        _pause_start = None
        broadcaster._push(CaptionEvent(kind="resumed"))
        server_log.info("Service resumed")
        operator_events.add("user", "Translation resumed")
    return {"ok": True, "paused": _paused}


async def _auto_stop_on_failure(reason: str):
    global _state, _auto_restart_attempt, _auto_restart_reason
    MAX_AUTO_RESTART_ATTEMPTS = 3
    AUTO_RESTART_BACKOFF_SEC = [2, 5, 15]
    
    try:
        server_log.warning("SESSION_FAILURE trigger: pipeline auto-restart loop initiated. Reason: %s", reason)
        operator_events.add("error", f"Session failure: {reason}")
        
        # 1. Teardown and export the current session transcript
        async with _state_lock:
            if _state == ServiceState.RUNNING:
                _state = ServiceState.STOPPING
                await _teardown()
                server_log.warning("Service automatically stopped: session failure (%s)", reason)
        
        # 2. Run the bounded auto-restart loop
        _auto_restart_reason = reason
        for attempt, backoff in enumerate(AUTO_RESTART_BACKOFF_SEC, start=1):
            _auto_restart_attempt = attempt
            operator_events.add(
                "warning",
                f"Auto-restart attempt {attempt}/{MAX_AUTO_RESTART_ATTEMPTS} in {backoff}s"
            )
            await asyncio.sleep(backoff)
            try:
                device_index = audio_cfg().get("device_index")
                await start_service({"device_index": device_index}, from_auto_restart=True)
                operator_events.add("success", f"Auto-restart succeeded on attempt {attempt}")
                _auto_restart_attempt = 0
                _auto_restart_reason = ""
                return
            except Exception as e:
                operator_events.add("error", f"Auto-restart attempt {attempt} failed: {e}")
                
        # All attempts exhausted
        _auto_restart_attempt = 0
        _auto_restart_reason = ""
        operator_events.add("error", "Auto-restart exhausted — manual intervention required")
        broadcaster.set_unavailable()
        async with _state_lock:
            _state = ServiceState.FAILED
            
    except asyncio.CancelledError:
        server_log.info("Auto-restart loop cancelled")
        _auto_restart_attempt = 0
        _auto_restart_reason = ""
        raise


def _handle_session_state_change(s):
    global _auto_restart_task
    if s.status == SessionStatus.FAILED:
        broadcaster.set_unavailable()
        if _auto_restart_task and not _auto_restart_task.done():
            _auto_restart_task.cancel()
        _auto_restart_task = asyncio.create_task(_auto_stop_on_failure(s.last_event))

session._on_state = _handle_session_state_change



@app.post("/api/config/auto-stop")
async def set_auto_stop(body: dict):
    minutes = int(body["minutes"])
    save_auto_stop_timeout(minutes)
    operator_events.add("user", f"Auto-stop set to {minutes} min")
    return {"ok": True, "minutes": minutes}


@app.get("/api/status")
async def get_status():
    a = audio.state
    s = session.state
    runtime = _runtime_seconds()
    cost = _billed_seconds * _COST_PER_AUDIO_SEC
    return {
        "service_running": _state != ServiceState.STOPPED,
        "state": _state.value,
        "paused": _paused,
        "runtime_s": round(runtime, 1),
        "cost_usd": round(cost, 4),
        "billed_audio_s": round(_billed_seconds, 1),
        "auto_stop_timeout_min": audio_cfg().get("auto_stop_timeout_min", 10),
        "device_index": audio_cfg().get("device_index", 0),
        "auto_restart_attempt": _auto_restart_attempt,
        "auto_restart_reason": _auto_restart_reason,
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


@app.get("/api/events")
async def get_events(since: int = -1):
    return {"events": operator_events.since(since), "latest_id": operator_events.latest_id}


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
  .timestamp {
    color: var(--color-gold-500);
    font-size: 0.75em;
    font-family: 'Inter', sans-serif;
    margin-right: 12px;
    font-weight: 500;
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
        if (msg.time_str) {
          const span = document.createElement('span');
          span.className = 'timestamp';
          span.textContent = `[${msg.time_str}] `;
          div.appendChild(span);
        }
        const textSpan = document.createElement('span');
        textSpan.textContent = msg.text;
        div.appendChild(textSpan);
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

  /* Compact status grid — 4 columns: label | val | label | val
     Full-width rows (long values) span all 4 cols via .sg-wide */
  .stat-grid {
    display: grid;
    grid-template-columns: auto 1fr auto 1fr;
    gap: 0 10px;
    margin-top: 4px;
  }
  .sg-label {
    font-size: 12px;
    color: var(--color-text-secondary);
    align-self: center;
    white-space: nowrap;
    padding: 4px 0;
    border-bottom: 1px solid var(--color-warm-100);
  }
  .sg-val {
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-primary);
    align-self: center;
    padding: 4px 0;
    border-bottom: 1px solid var(--color-warm-100);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* Full-width label: col 1, value: cols 2-4 */
  .sg-label.sg-wide          { grid-column: 1; }
  .sg-val.sg-wide            { grid-column: 2 / span 3; }
  /* Remove bottom border on last row */
  .sg-label.sg-last, .sg-val.sg-last { border-bottom: none; }
  .sg-val.ok   { color: var(--color-success); }
  .sg-val.warn { color: var(--color-gold-500); }
  .sg-val.err  { color: var(--color-error); }
  .sg-val.cost { color: var(--color-navy-700); font-weight: 700; }

  /* Tooltips */
  .tooltip {
    position: relative;
    display: inline-block;
    border-bottom: 1px dotted var(--color-gold-500);
    cursor: help;
  }
  .tooltip .tooltiptext {
    visibility: hidden;
    width: 240px;
    background-color: var(--color-navy-900);
    color: #fff;
    text-align: center;
    border-radius: 6px;
    padding: 6px 10px;
    position: absolute;
    z-index: 100;
    bottom: 125%;
    left: 50%;
    margin-left: -120px;
    opacity: 0;
    transition: opacity 0.2s ease-in-out;
    font-size: 12px;
    line-height: 1.4;
    font-weight: normal;
    box-shadow: 0 4px 10px rgba(15, 27, 45, 0.2);
    border: 1px solid var(--color-gold-500);
    white-space: normal;
  }
  .tooltip .tooltiptext::after {
    content: "";
    position: absolute;
    top: 100%;
    left: 50%;
    margin-left: -5px;
    border-width: 5px;
    border-style: solid;
    border-color: var(--color-navy-900) transparent transparent transparent;
  }
  .tooltip:hover .tooltiptext {
    visibility: visible;
    opacity: 1;
  }

  /* Caption preview */
  #preview-wrap {
    height: 280px;
    overflow-y: auto;
    background: var(--color-warm-white);
    border: 1px solid var(--color-warm-100);
    border-radius: 8px;
    padding: 12px;
    margin-top: 12px;
  }
  .preview-pair {
    margin-bottom: 10px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--color-warm-100);
  }
  .preview-pair:last-child { border-bottom: none; margin-bottom: 0; }
  .preview-ko {
    font-family: 'Noto Sans KR', sans-serif;
    font-size: 13px;
    color: var(--color-text-muted);
    line-height: 1.6;
  }
  .preview-en {
    font-family: 'Inter', sans-serif;
    font-size: 15px;
    color: var(--color-text-primary);
    font-weight: 500;
    line-height: 1.6;
    margin-top: 2px;
  }
  .preview-en.live { color: var(--color-navy-600); }
  .preview-ts {
    font-size: 11px;
    color: var(--color-gold-500);
    font-weight: 600;
    margin-right: 6px;
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

  /* Status strip */
  #status-strip {
    background: var(--color-navy-800);
    border-bottom: 2px solid var(--color-navy-700);
    padding: 6px 20px;
    display: flex;
    gap: 8px;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    flex-wrap: wrap;
    overflow: visible;
    position: relative;
    z-index: 50;
  }
  .ss-item {
    font-size: 12px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 12px;
    background: var(--color-navy-700);
    color: #8fa3bc;
    transition: background 0.3s, color 0.3s;
    position: relative;
    cursor: help;
  }
  .ss-ok   { background: #14532d; color: #86efac; }
  .ss-warn { background: #713f12; color: #fde68a; }
  .ss-err  { background: #7f1d1d; color: #fca5a5; }
  /* Tooltip for status strip — opens downward since strip is at top of page */
  .ss-item .ss-tip {
    visibility: hidden;
    opacity: 0;
    width: 220px;
    background: var(--color-navy-900);
    color: #fff;
    font-size: 12px;
    font-weight: normal;
    line-height: 1.5;
    text-align: left;
    padding: 8px 10px;
    border-radius: 6px;
    border: 1px solid var(--color-gold-500);
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    position: absolute;
    top: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    z-index: 200;
    transition: opacity 0.2s;
    white-space: normal;
    pointer-events: none;
  }
  .ss-item .ss-tip::before {
    content: "";
    position: absolute;
    bottom: 100%;
    left: 50%;
    margin-left: -5px;
    border-width: 5px;
    border-style: solid;
    border-color: transparent transparent var(--color-navy-900) transparent;
  }
  .ss-item:hover .ss-tip { visibility: visible; opacity: 1; }

  /* Event log expandable entries */
  .log-entry { padding: 3px 0; border-bottom: 1px dotted var(--color-warm-100); }
  .log-entry.has-details { cursor: pointer; }
  .log-details { display: none; padding: 3px 0 3px 18px; font-size: 11px;
                 color: var(--color-text-muted); line-height: 1.5; }
  .log-details.open { display: block; }
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

<div id="status-strip">
  <div class="ss-item" id="ss-audio">🔵 Audio
    <span class="ss-tip">
      <strong>Audio (USB Mixer)</strong><br>
      🟢 Signal detected<br>
      🟡 No signal — mic may be muted or disconnected<br>
      🔴 Device disconnected — check USB cable<br>
      🔵 Service not started
    </span>
  </div>
  <div class="ss-item" id="ss-gemini">🔵 Gemini
    <span class="ss-tip">
      <strong>Gemini AI Session</strong><br>
      🟢 Connected and translating<br>
      🟡 Reconnecting — translation will resume<br>
      🔴 Failed — check internet or restart<br>
      🔵 Service not started
    </span>
  </div>
  <div class="ss-item" id="ss-internet">🔵 Internet
    <span class="ss-tip">
      <strong>Internet / Google API</strong><br>
      🟢 Reachable<br>
      🟡 Reconnecting to Google servers<br>
      🔴 Cannot reach Google — check Wi-Fi<br>
      🔵 Service not started
    </span>
  </div>
  <div class="ss-item" id="ss-translation">🔵 Translation
    <span class="ss-tip">
      <strong>Translation Pipeline</strong><br>
      🟢 Live — captions flowing to attendees<br>
      🟡 Starting up or paused<br>
      🔴 Error — restart the service<br>
      🔵 Stopped
    </span>
  </div>
  <div class="ss-item ss-ok">🟢 Web Server
    <span class="ss-tip">
      <strong>Web Server</strong><br>
      🟢 Running — attendees can connect<br>
      (If you see this page, the server is up)
    </span>
  </div>
</div>

<main>

  <div class="col-left">

    <!-- Input device + level meter -->
    <div class="card">
      <h2>입력 장치 설정 (Input Device)</h2>
      <select id="device-select"><option>Loading…</option></select>
      <div class="meter-wrap"><div class="meter-bar" id="level-bar"></div></div>
      <div class="meter-label" id="level-label">입력 레벨 (Input level)</div>
    </div>

    <!-- Status (compact grid) -->
    <div class="card" id="status-card">
      <h2>상태 모니터 (Status)</h2>
      <div class="stat-grid">
        <!-- Row 1: Audio (full width) -->
        <span class="sg-label sg-wide tooltip">오디오 입력
          <span class="tooltiptext">Windows PC의 마이크/오디오 신호가 서버에 정상 수신되고 있는지 나타냅니다. (Indicates if the mic audio is being captured properly.)</span>
        </span>
        <span class="sg-val sg-wide" id="stat-audio">—</span>
        <!-- Row 2: Gemini (full width) -->
        <span class="sg-label sg-wide tooltip">Gemini 세션
          <span class="tooltiptext">구글 Gemini Live API 서버와의 실시간 웹소켓 번역 연결 상태입니다. (WebSocket connection status to Google Gemini Live API.)</span>
        </span>
        <span class="sg-val sg-wide" id="stat-session">—</span>
        <!-- Row 3: Model (full width, right below Gemini) -->
        <span class="sg-label sg-wide tooltip">모델
          <span class="tooltiptext">번역과 음성 스트리밍을 수행하고 있는 구글 제미나이 인공지능 모델 번호입니다. (Gemini model ID performing the live translation.)</span>
        </span>
        <span class="sg-val sg-wide" id="stat-model" style="font-size:11px;color:var(--color-text-muted)">—</span>
        <!-- Row 4: Latency | Attendees -->
        <span class="sg-label tooltip">지연
          <span class="tooltiptext">입력된 음성이 번역되어 폰 화면에 표시될 때까지 소요되는 지체 시간입니다. (Time lag from audio capture to caption output.)</span>
        </span>
        <span class="sg-val" id="stat-latency">—</span>
        <span class="sg-label tooltip">접속자
          <span class="tooltiptext">현재 와이파이를 통해 자막(/live) 페이지에 접속해 있는 실시간 기기 수입니다. (Number of devices currently viewing the live captions.)</span>
        </span>
        <span class="sg-val" id="stat-attendees">0</span>
        <!-- Row 5: Reconnects | Captions -->
        <span class="sg-label tooltip">재연결
          <span class="tooltiptext">Gemini Live API의 연결 불안정 혹은 10분 만료로 인해 재설정된 자동 복구 횟수입니다. (Auto-recovery count due to Gemini API connection drops.)</span>
        </span>
        <span class="sg-val" id="stat-reconnects">0</span>
        <span class="sg-label tooltip">자막 수
          <span class="tooltiptext">현재 세션 동안 번역되어 전송 완료된 자막 라인의 총 개수입니다. (Total translated caption paragraphs sent in this session.)</span>
        </span>
        <span class="sg-val" id="stat-captions">0</span>
        <!-- Row 6: Runtime | Cost -->
        <span class="sg-label tooltip sg-last">시간
          <span class="tooltiptext">예배 번역 시스템이 시작된 이후 총 가동 시간입니다. (Total active runtime of the translation server.)</span>
        </span>
        <span class="sg-val sg-last" id="stat-runtime">—</span>
        <span class="sg-label tooltip sg-last">비용
          <span class="tooltiptext">구글 제미나이 Live API의 유료 실시간 가격(분당 $0.0368) 기준으로 계산된 추정 요금입니다. (Estimated API cost based on Gemini Paid Tier.)</span>
        </span>
        <span class="sg-val cost sg-last" id="stat-cost">—</span>
      </div>
    </div>

    <!-- Caption preview (Korean + English paired) -->
    <div class="card">
      <h2>실시간 자막 미리보기 (Preview)</h2>
      <div id="preview-wrap"><div id="preview"></div></div>
    </div>

    <!-- Control buttons -->
    <div class="ctrl-bar">
      <button class="primary"  id="btn-start">▶ Start</button>
      <button class="warning"  id="btn-pause" disabled>⏸ Pause</button>
      <button class="danger"   id="btn-stop"  disabled>■ Stop</button>
    </div>

    <!-- Auto-Stop + Exit System side by side -->
    <div style="margin-top: 10px; display: flex; align-items: center; gap: 8px;">
      <span class="tooltip" style="flex-shrink: 0; font-size: 18px; cursor: default; line-height: 1;">⏱
        <span class="tooltiptext" style="left: 0; margin-left: 0;">자동 종료 대기 시간 — 설정한 시간 동안 오디오가 없으면 자동으로 서비스를 종료합니다. (Auto-Stop: service stops automatically after this silence duration.)</span>
      </span>
      <select id="auto-stop-select" style="font-size: 12px; padding: 7px 8px; flex: 1; min-width: 0;">
        <option value="0">자동종료 안 함 (Off)</option>
        <option value="1">1분 (1 Min) — 테스트용</option>
        <option value="5">5분 (5 Min)</option>
        <option value="10">10분 (10 Min)</option>
        <option value="15">15분 (15 Min)</option>
        <option value="20">20분 (20 Min)</option>
        <option value="30">30분 (30 Min)</option>
      </select>
      <button id="btn-shutdown" class="secondary" style="flex: 1; border-color: var(--color-error); color: var(--color-error); font-weight: 600; padding: 7px 12px;">
        🔴 Exit System
      </button>
    </div>

  </div><!-- /col-left -->

  <div class="col-right">

    <!-- Audio playback -->
    <div class="card">
      <h2>
        <span class="tooltip">음성 통역 모니터 (Audio)
          <span class="tooltiptext">관리자가 이어폰을 꽂고 번역된 실시간 영어 음성 스트림을 모니터링하기 위한 볼륨 조절 영역입니다. (Volume control for checking English voice outputs using headphones.)</span>
        </span>
      </h2>
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
        <div id="log"><div class="log-placeholder" style="color:var(--color-text-muted); padding:3px 0;">No events yet…</div></div>
      </div>
    </div>

    <!-- QR code (bottom of right column) -->
    <div class="card">
      <h2 class="card-toggle" id="qr-toggle">
        접속 QR 코드 <span class="chevron">▼</span>
      </h2>
      <div class="card-body hidden" id="qr-body">
        <img id="qr-img" src="/api/qr.png" alt="QR code" style="width:100%;height:auto;display:block;">
      </div>
    </div>

  </div><!-- /col-right -->

</main>

<script>
let polling = null;
let captionEs = null;
let hasInitializedAutoStop = false;
let lastEventId = -1;
let userScrolledUp = false;
let eventPollTimer = null;
let lastAutoRestartAttempt = 0;

function playBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.3);
  } catch (e) {
    console.error("Could not play beep", e);
  }
}

const btnStart = document.getElementById('btn-start');
const btnPause = document.getElementById('btn-pause');
const btnStop  = document.getElementById('btn-stop');
const btnShutdown = document.getElementById('btn-shutdown');
const preview  = document.getElementById('preview');
const previewWrap = document.getElementById('preview-wrap');
const logEl    = document.getElementById('log');

// ── Preview: Korean + English paired DOM ──────────────────────────────────────
// Each "pair" = one committed turn: { koEl, enEl, wrapEl, koText, enText }
let pairs = [];      // committed pairs
let livePair = null; // the currently-accumulating in-progress pair

const MAX_PAIRS = 50;

function getOrCreateLivePair() {
  if (livePair) return livePair;
  const wrap = document.createElement('div');
  wrap.className = 'preview-pair';
  const koEl = document.createElement('div');
  koEl.className = 'preview-ko';
  const enEl = document.createElement('div');
  enEl.className = 'preview-en live';
  wrap.appendChild(koEl);
  wrap.appendChild(enEl);
  preview.appendChild(wrap);
  livePair = { wrap, koEl, enEl };
  return livePair;
}

function commitLivePair(timeStr) {
  if (!livePair) return;
  livePair.enEl.classList.remove('live');
  if (timeStr) {
    const ts = document.createElement('span');
    ts.className = 'preview-ts';
    ts.textContent = '[' + timeStr + '] ';
    livePair.enEl.prepend(ts);
  }
  pairs.push(livePair);
  livePair = null;
  // Trim oldest pairs
  while (pairs.length > MAX_PAIRS) {
    const old = pairs.shift();
    old.wrap.remove();
  }
}

function resetPreview() {
  preview.innerHTML = '';
  pairs = [];
  livePair = null;
}

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
  const status = await fetch('/api/status').then(r => r.json());
  const sel = document.getElementById('device-select');
  sel.innerHTML = devices.map(d => `<option value="${d.index}">[${d.index}] ${d.name}</option>`).join('');
  if (status && status.device_index !== undefined) {
    sel.value = status.device_index;
  }
  // Hook up change event to persist select index immediately
  sel.addEventListener('change', async () => {
    const idx = parseInt(sel.value);
    await fetch('/api/devices/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: idx })
    });
  });
}

// ── Start ─────────────────────────────────────────────────────────────────────
btnStart.addEventListener('click', async () => {
  btnStart.disabled = true; btnStart.textContent = '⏳ Starting…';
  const idx = parseInt(document.getElementById('device-select').value);
  try {
    await fetch('/api/start', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({device_index: idx}) });
    btnPause.disabled = false; btnStop.disabled = false;
    resetPreview();
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

// ── Shutdown ──────────────────────────────────────────────────────────────────
btnShutdown.addEventListener('click', async () => {
  const ok = confirm(
    "전체 번역 시스템을 완전히 종료하시겠습니까?\\n" +
    "이 작업은 서버 프로그램을 닫으므로 다시 사용하려면 바탕화면의 시작 파일을 실행해야 합니다.\\n\\n" +
    "Are you sure you want to completely exit the system?\\n" +
    "This will close the server program."
  );
  if (!ok) return;

  btnShutdown.disabled = true;
  btnShutdown.textContent = '⏳ 종료 중 (Shutting down…)';
  try {
    await fetch('/api/shutdown', { method: 'POST' });
    document.body.innerHTML = `
      <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; font-family: sans-serif; background: #faf8f5; color: #1a1a1a; padding: 20px; text-align: center;">
        <div style="font-size: 60px; margin-bottom: 20px;">🔌</div>
        <h1 style="font-family: serif; font-size: 24px; margin-bottom: 12px;">시스템이 안전하게 종료되었습니다</h1>
        <p style="color: #4a4a4a; font-size: 15px; max-width: 450px; line-height: 1.6; margin-bottom: 24px;">
          번역 서버 프로그램이 정상 종료되었습니다. 이제 실행 중인 검은색 터미널 창을 닫으셔도 됩니다. 나중에 다시 시작하려면 바탕화면의 시작 배치 파일을 실행하세요.
        </p>
        <h1 style="font-family: serif; font-size: 20px; margin-bottom: 12px; color: #7a7a7a;">System Successfully Terminated</h1>
        <p style="color: #7a7a7a; font-size: 14px; max-width: 450px; line-height: 1.6;">
          The translation server has shut down gracefully. You may now close any remaining terminal windows.
        </p>
      </div>
    `;
  } catch {
    btnShutdown.disabled = false;
    btnShutdown.textContent = '🔴 프로그램 완전 종료 (Exit System)';
  }
});


// ── Status poll ───────────────────────────────────────────────────────────────
const SESSION_COLOR = { connected:'ok', reconnecting:'warn', failed:'err', connecting:'warn', stopped:'' };
const AUDIO_COLOR   = { connected:'ok', no_signal:'warn', disconnected:'err', stopped:'' };

function fmtRuntime(s) {
  return Math.floor(s/60) + ':' + String(Math.floor(s%60)).padStart(2,'0');
}

// ── Event log (polls /api/events) ────────────────────────────────────────────
logEl.addEventListener('scroll', () => {
  const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 10;
  userScrolledUp = !atBottom;
});

function fmtTs(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

function appendLogEvent(ev) {
  const placeholder = logEl.querySelector('.log-placeholder');
  if (placeholder) placeholder.remove();

  const hasDetails = ev.details && Object.keys(ev.details).length > 0;
  const d = document.createElement('div');
  d.className = 'log-entry' + (hasDetails ? ' has-details' : '');

  const mainLine = document.createElement('div');
  mainLine.textContent = fmtTs(ev.ts) + ' ' + ev.icon + ' ' + ev.message +
                         (hasDetails ? ' ▸' : '');
  d.appendChild(mainLine);

  if (hasDetails) {
    const dd = document.createElement('div');
    dd.className = 'log-details';
    for (const [k, v] of Object.entries(ev.details)) {
      const row = document.createElement('div');
      row.textContent = k + ': ' + v;
      dd.appendChild(row);
    }
    d.appendChild(dd);
    d.addEventListener('click', () => dd.classList.toggle('open'));
  }

  logEl.appendChild(d);
  while (logEl.children.length > 50) logEl.removeChild(logEl.firstChild);
  if (!userScrolledUp) logEl.scrollTop = logEl.scrollHeight;
}

async function pollEvents() {
  try {
    const data = await fetch('/api/events?since=' + lastEventId).then(r => r.json());
    if (data.events && data.events.length > 0) {
      data.events.forEach(appendLogEvent);
      lastEventId = data.latest_id;
    }
  } catch { /* network error — skip */ }
}

function startEventPoll() {
  if (eventPollTimer) clearInterval(eventPollTimer);
  pollEvents();
  eventPollTimer = setInterval(pollEvents, 1500);
}

function startStatusPoll() {
  if (polling) clearInterval(polling);
  polling = setInterval(async () => {
    let st;
    try { st = await fetch('/api/status').then(r => r.json()); } catch { return; }

    if (st.auto_restart_attempt !== undefined && st.auto_restart_attempt > 0) {
      if (st.auto_restart_attempt !== lastAutoRestartAttempt) {
        playBeep();
        lastAutoRestartAttempt = st.auto_restart_attempt;
      }
      const card = document.getElementById('status-card');
      if (card) {
        card.style.borderColor = 'var(--color-error)';
        card.style.backgroundColor = 'rgba(163, 59, 59, 0.05)';
      }
    } else {
      lastAutoRestartAttempt = 0;
      const card = document.getElementById('status-card');
      if (card) {
        card.style.borderColor = '';
        card.style.backgroundColor = '';
      }
    }

    if (!hasInitializedAutoStop && st.auto_stop_timeout_min !== undefined) {
      document.getElementById('auto-stop-select').value = st.auto_stop_timeout_min;
      hasInitializedAutoStop = true;
    }

    document.getElementById('level-bar').style.width = st.audio.level + '%';
    document.getElementById('level-label').textContent =
      st.audio.level > 0 ? '레벨: ' + Math.round(st.audio.level) + '%' : '입력 레벨 — 신호 없음';

    const auEl = document.getElementById('stat-audio');
    auEl.textContent = st.audio.status + (st.audio.device ? ' — ' + st.audio.device : '');
    auEl.className = 'sg-val ' + (AUDIO_COLOR[st.audio.status] || '');

    const seEl = document.getElementById('stat-session');
    seEl.textContent = st.session.status + (st.session.last_event ? ' (' + st.session.last_event + ')' : '');
    seEl.className = 'sg-val ' + (SESSION_COLOR[st.session.status] || '');

    document.getElementById('stat-latency').textContent = st.session.latency_ms ? st.session.latency_ms + ' ms' : '—';
    document.getElementById('stat-attendees').textContent = st.attendees;
    document.getElementById('stat-reconnects').textContent = st.session.reconnect_count;
    document.getElementById('stat-captions').textContent = st.captions || 0;
    document.getElementById('stat-model').textContent = st.session.model || '—';
    if (st.service_running) {
      document.getElementById('stat-runtime').textContent = fmtRuntime(st.runtime_s);
      document.getElementById('stat-cost').textContent = '$' + st.cost_usd.toFixed(4);
    }

    // ── Status strip ──────────────────────────────────────────────────────────
    function ssSet(id, cls, label) {
      const el = document.getElementById(id);
      el.className = 'ss-item ' + cls;
      // Update only the leading text node — leave the .ss-tip child span intact
      for (const node of el.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) { node.textContent = label + ' '; return; }
      }
      el.insertBefore(document.createTextNode(label + ' '), el.firstChild);
    }
    const audioMap = {connected:['ss-ok','🟢 Audio'], no_signal:['ss-warn','🟡 Audio'], disconnected:['ss-err','🔴 Audio'], stopped:['','🔵 Audio']};
    ssSet('ss-audio', ...(audioMap[st.audio.status] || ['','🔵 Audio']));
    const geminiMap = {connected:['ss-ok','🟢 Gemini'], reconnecting:['ss-warn','🟡 Gemini'], connecting:['ss-warn','🟡 Gemini'], failed:['ss-err','🔴 Gemini'], stopped:['','🔵 Gemini']};
    ssSet('ss-gemini', ...(geminiMap[st.session.status] || ['','🔵 Gemini']));
    if (st.session.status === 'connected') ssSet('ss-internet', 'ss-ok', '🟢 Internet');
    else if (st.session.status === 'reconnecting') ssSet('ss-internet', 'ss-warn', '🟡 Internet');
    else if (st.session.status === 'failed') ssSet('ss-internet', 'ss-err', '🔴 Internet');
    else ssSet('ss-internet', '', '🔵 Internet');
    if (st.state === 'running' && st.session.status === 'connected') ssSet('ss-translation', 'ss-ok', '🟢 Translation');
    else if (st.state === 'starting' || st.paused) ssSet('ss-translation', 'ss-warn', '🟡 Translation');
    else if (st.state === 'failed' || st.session.status === 'failed') ssSet('ss-translation', 'ss-err', '🔴 Translation');
    else ssSet('ss-translation', '', '🔵 Translation');

    const badge = document.getElementById('hdr-badge');
    if (!st.service_running) {
      badge.textContent = 'Stopped'; badge.className = 'badge badge-gray';
      btnStart.disabled = false; btnStart.textContent = '▶ Start';
      btnPause.disabled = true; btnPause.textContent = '⏸ Pause'; btnPause.className = 'warning';
      btnStop.disabled = true; btnStop.textContent = '■ Stop';
      _paused = false;
    } else {
      btnStart.disabled = true; btnStart.textContent = '▶ Running';
      btnStop.disabled = false;
      btnPause.disabled = false;

      _paused = st.paused;
      if (_paused) {
        btnPause.textContent = '▶ Resume'; btnPause.className = 'primary';
        badge.textContent = 'Paused'; badge.className = 'badge badge-blue';
      } else {
        btnPause.textContent = '⏸ Pause'; btnPause.className = 'warning';
        if (st.session.status === 'connected') {
          badge.textContent = 'Live'; badge.className = 'badge badge-green';
        } else if (st.session.status === 'failed') {
          badge.textContent = 'Error'; badge.className = 'badge badge-red';
        } else {
          badge.textContent = 'Starting'; badge.className = 'badge badge-amber';
        }
      }
    }
  }, 1000);
}

// ── SSE: Korean + English paired preview ─────────────────────────────────────
function connectSSE() {
  if (captionEs) captionEs.close();
  captionEs = new EventSource('/stream');
  captionEs.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.kind === 'ping') { return; }

    if (msg.kind === 'source') {
      // Korean delta — append to the live pair's KO line
      const p = getOrCreateLivePair();
      p.koEl.textContent += msg.text;

    } else if (msg.kind === 'update') {
      // English delta — replace live pair's EN line (broadcaster sends full accumulated text)
      const p = getOrCreateLivePair();
      p.enEl.textContent = msg.text;

    } else if (msg.kind === 'commit') {
      // Finalize the live pair: remove live styling, prepend timestamp
      commitLivePair(msg.time_str || null);

    } else if (msg.kind === 'unavailable') {
      commitLivePair(null);
      const wrap = document.createElement('div');
      wrap.className = 'preview-pair';
      wrap.innerHTML = '<div class="preview-en" style="color:var(--color-error)">[번역 불가 / Translation unavailable]</div>';
      preview.appendChild(wrap);

    } else if (msg.kind === 'paused') {
      commitLivePair(null);
      const wrap = document.createElement('div');
      wrap.className = 'preview-pair';
      wrap.innerHTML = '<div class="preview-en" style="color:var(--color-text-muted)">— Paused —</div>';
      preview.appendChild(wrap);

    } else if (msg.kind === 'resumed') {
      const wrap = document.createElement('div');
      wrap.className = 'preview-pair';
      wrap.innerHTML = '<div class="preview-en" style="color:var(--color-success)">— Resumed —</div>';
      preview.appendChild(wrap);
    }

    previewWrap.scrollTop = previewWrap.scrollHeight;
  };
}

document.getElementById('auto-stop-select').addEventListener('change', async (e) => {
  const mins = parseInt(e.target.value);
  try {
    await fetch('/api/config/auto-stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: mins })
    });
  } catch (err) { /* event will still appear via backend operator_events */ }
});

loadDevices();
startStatusPoll();
startEventPoll();
</script>
</body>
</html>"""

