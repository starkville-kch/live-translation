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


_zc = None
_zc_info = None


def _register_zeroconf(hostname: str, port: int, ip_addr: str) -> None:
    global _zc, _zc_info
    try:
        from zeroconf import Zeroconf, ServiceInfo
        dns_name = hostname if hostname.endswith(".") else f"{hostname}."
        _zc = Zeroconf()
        _zc_info = ServiceInfo(
            type_="_http._tcp.local.",
            name="SKC Live Translation._http._tcp.local.",
            addresses=[socket.inet_aton(ip_addr)],
            port=port,
            properties={},
            server=dns_name,
        )
        _zc.register_service(_zc_info)
        server_log.info("Registered mDNS hostname: %s pointing to %s", hostname, ip_addr)
    except Exception as e:
        server_log.warning("Could not register mDNS hostname via Zeroconf: %s", e)


def _unregister_zeroconf() -> None:
    global _zc, _zc_info
    if _zc is not None:
        try:
            if _zc_info is not None:
                _zc.unregister_service(_zc_info)
            _zc.close()
            server_log.info("Unregistered mDNS hostname")
        except Exception as e:
            server_log.warning("Error closing Zeroconf: %s", e)
        finally:
            _zc = None
            _zc_info = None


def _get_live_urls() -> tuple[str, str]:
    cfg = network_cfg()
    port = cfg.get("port", 8080)
    hostname = cfg.get("hostname", "")
    if hostname and not hostname.endswith(".local"):
        hostname = f"{hostname}.local"

    ip_addr = _local_ip()

    public_url = cfg.get("public_url")
    if not public_url:
        if hostname:
            public_url = f"http://{hostname}:{port}"
        else:
            public_url = f"http://{ip_addr}:{port}"

    live_url_primary = f"{public_url}/live"
    live_url_fallback = f"http://{ip_addr}:{port}/live"

    return live_url_primary, live_url_fallback


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
    port = cfg.get("port", 8080)
    hostname = cfg.get("hostname", "")
    ip_addr = _local_ip()

    if hostname:
        if not hostname.endswith(".local"):
            hostname = f"{hostname}.local"
        _register_zeroconf(hostname, port, ip_addr)

    primary_url, fallback_url = _get_live_urls()
    _qr_png_cache = _build_qr(primary_url)
    server_log.info("QR code URL: %s", primary_url)
    server_log.info("Fallback URL: %s", fallback_url)

    operator_events.add(
        "success", "System started",
        {"port": port, "primary_url": primary_url, "fallback_url": fallback_url}
    )

    async def _ping():
        while True:
            await asyncio.sleep(15)
            broadcaster._push(CaptionEvent(kind="ping"))

    asyncio.create_task(_ping())
    yield
    _unregister_zeroconf()
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
    primary_url, fallback_url = _get_live_urls()
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
        "live_url_primary": primary_url,
        "live_url_fallback": fallback_url,
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
    if getattr(sys, "frozen", False):
        return _ATTENDEE_HTML_CACHE
    return _read_template("attendee.html")


@app.get("/", response_class=HTMLResponse)
async def operator_page():
    if getattr(sys, "frozen", False):
        return _OPERATOR_HTML_CACHE
    return _read_template("operator.html")


def _read_template(filename: str) -> str:
    import sys
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).parent.parent

    template_path = base_dir / "app" / "templates" / filename
    if not template_path.exists():
        template_path = Path(__file__).parent / "templates" / filename

    try:
        return template_path.read_text(encoding="utf-8")
    except Exception as e:
        server_log.error("Failed to read template %s: %s", filename, str(e))
        return f"Error: Template {filename} not found."


# Cache templates in production
import sys
_ATTENDEE_HTML_CACHE = _read_template("attendee.html")
_OPERATOR_HTML_CACHE = _read_template("operator.html")
