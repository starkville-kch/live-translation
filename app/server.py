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

Session transcript export (v3.0 Canonical Format)
-------------------------------------------------
On /api/stop, ``_write_session_log()`` writes exactly four canonical files to
``logs/sessions/YYYYMMDD_HHMMSS/``:
  session.json    — Machine-readable manifest (session metadata, cost accounting, turns)
  transcript.jsonl— Canonical machine-readable turn records (1 JSON per committed turn)
  transcript.md   — Human-readable multi-language chronological transcript
  summary.txt     — Concise operational performance & billing summary
"""
import asyncio
import io
import json

import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncIterator, List, Optional

import qrcode
from starlette.types import ASGIApp, Receive, Scope, Send
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from app.audio import AudioCapture, AudioStatus, list_input_devices
from app.broadcast import CaptionBroadcaster, CaptionEvent
from app.config import (
    audio_cfg,
    church_cfg,
    gemini_cfg,
    get_app_root,
    logging_cfg,
    network_cfg,
    save_audio_device,
    save_auto_stop_timeout,
    save_operator_ui_language,
    save_translation_settings,
    translation_cfg,
)

from app.events import operator_events
from app.gemini_session import GeminiSession, SessionState, SessionStatus
from app.glossary import GlossaryCorrector
from app.languages import get_available_languages, get_language, is_valid_language_code
from app.logger import server_log
from app.model_resolver import model_resolver, verify_model_compatibility
from app.operator_auth import (
    clear_auth_cookie,
    create_session_token,
    is_auth_enabled,
    is_authenticated,
    log_auth_status_on_startup,
    set_auth_cookie,
    verify_password,
)
from app.translation_manager import TranslationManager

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
manager = TranslationManager(glossary=_glossary)
audio = manager.audio
broadcaster = manager.primary_broadcaster
session = manager.sessions.get("en") or manager._create_session_for_target("en", "ko")

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

        # Accept either "skc" or "skc.local" in config.yaml.
        short_name = hostname.strip().rstrip(".")
        if short_name.lower().endswith(".local"):
            short_name = short_name[:-6]

        if not short_name:
            raise ValueError("mDNS hostname is empty")

        mdns_name = f"{short_name}.local."

        _zc = Zeroconf()
        _zc_info = ServiceInfo(
            type_="_http._tcp.local.",
            name="SKC Live Translation._http._tcp.local.",
            addresses=[socket.inet_aton(ip_addr)],
            port=int(port),
            properties={
                b"path": b"/",
                b"live_path": b"/live",
                b"admin_path": b"/admin",
            },
            server=mdns_name,
        )
        _zc.register_service(_zc_info, allow_name_change=True)

        server_log.info(
            "Registered mDNS hostname: %s -> %s:%s",
            mdns_name.rstrip("."),
            ip_addr,
            port,
        )
    except Exception:
        server_log.exception("Could not register mDNS hostname via Zeroconf")


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


def _get_public_base_url() -> str:
    cfg = network_cfg()
    port = int(cfg.get("port", 8080))
    hostname = (cfg.get("hostname", "") or "").strip()
    ip_addr = _local_ip()

    if hostname:
        host = hostname.rstrip(".")
        if not host.lower().endswith(".local"):
            host = f"{host}.local"
    else:
        host = ip_addr

    if port == 80:
        return f"http://{host}"
    if port == 443:
        return f"https://{host}"

    return f"http://{host}:{port}"


def _get_admin_url() -> str:
    return f"{_get_public_base_url()}/admin"


def _get_live_urls() -> tuple[str, str, str]:
    cfg = network_cfg()
    port = int(cfg.get("port", 8080))
    hostname = (cfg.get("hostname", "") or "").strip()
    ip_addr = _local_ip()

    if hostname:
        host = hostname.rstrip(".")
        if not host.lower().endswith(".local"):
            host = f"{host}.local"
    else:
        host = ip_addr

    if port == 80:
        local_url = f"http://{host}/live"
        fallback_url = f"http://{ip_addr}/live"
    elif port == 443:
        local_url = f"https://{host}/live"
        fallback_url = f"https://{ip_addr}/live"
    else:
        local_url = f"http://{host}:{port}/live"
        fallback_url = f"http://{ip_addr}:{port}/live"

    pub_url = cfg.get("public_url")
    if pub_url:
        public_url = str(pub_url).rstrip('/')
    else:
        public_url = "https://live.starkvillekoreanchurch.org"

    return local_url, fallback_url, public_url



_tunnel_logged = False
_tunnel_failed_logged = False


def _get_active_attendee_share_url() -> tuple[str, str]:
    local_url, fallback_url, public_url = _get_live_urls()
    primary_url = public_url if public_url else local_url
    return primary_url, fallback_url



def _build_qr(url: str) -> bytes:
    from PIL import Image, ImageDraw
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
    from qrcode.image.styles.colormasks import SolidFillColorMask

    NAVY = (26, 42, 66)
    GOLD = (184, 148, 69)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,  # ~15% recovery is sufficient for a small logo
        box_size=14,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(radius_ratio=0.6),
        color_mask=SolidFillColorMask(back_color=(255, 255, 255), front_color=NAVY),
    ).convert("RGBA")

    # Recolor the 3 finder patterns by drawing gold squares directly over
    # the known module coordinates — no pixel scanning / color matching needed.
    # Recolor the 3 finder patterns with rounded corners
    draw = ImageDraw.Draw(img)
    bs, border, n = qr.box_size, qr.border, qr.modules_count
    corner_radius = int(bs * 1.2)

    for col, row in [(0, 0), (n - 7, 0), (0, n - 7)]:
        x1 = (col + border) * bs
        y1 = (row + border) * bs
        x2 = x1 + 7 * bs
        y2 = y1 + 7 * bs

        # Clear the full square first — erases the navy modules underneath,
        # including the corners the rounded gold square won't cover
        draw.rectangle([x1, y1, x2, y2], fill=(255, 255, 255, 255))

        # outer gold ring
        draw.rounded_rectangle([x1, y1, x2, y2], radius=corner_radius, fill=GOLD)
        # inner white ring
        draw.rounded_rectangle(
            [x1 + bs, y1 + bs, x1 + 6 * bs, y1 + 6 * bs],
            radius=corner_radius * 0.7, fill=(255, 255, 255, 255),
        )
        # gold core
        draw.rounded_rectangle(
            [x1 + 2 * bs, y1 + 2 * bs, x1 + 5 * bs, y1 + 5 * bs],
            radius=corner_radius * 0.4, fill=GOLD,
        )

    # Embed central logo — 5:4 (height:width) rounded rectangular buffer
    logo_cfg = church_cfg().get("logo", "branding/church-logo.png")
    custom_logo = get_app_root() / logo_cfg if logo_cfg else None
    if custom_logo and custom_logo.exists():
        logo_path = custom_logo
    else:
        logo_path = Path(__file__).parent / "pca-logo-white-small.webp"

    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        w, h = img.size

        logo_w = int(w * 0.20)
        logo_h = int(logo_w * 6 / 5)
        logo = logo.resize((logo_w, logo_h), Image.Resampling.LANCZOS)

        cx, cy = w // 2, h // 2
        logo_radius = int(logo_w * 0.25)   # tune to taste

        # Quiet-zone buffer: white rounded rectangle, 20% padding around the logo
        buf_half_w = logo_w // 2 + int(logo_w * 0.15)
        buf_half_h = logo_h // 2 + int(logo_h * 0.15)
        draw.rounded_rectangle(
            [cx - buf_half_w, cy - buf_half_h, cx + buf_half_w, cy + buf_half_h],
            radius=logo_radius, fill=(255, 255, 255, 255),
        )

        # Navy inner rounded rectangle so the white logo stands out
        inner_half_w = logo_w // 2 + int(logo_w * 0.06)
        inner_half_h = logo_h // 2 + int(logo_h * 0.06)
        draw.rounded_rectangle(
            [cx - inner_half_w, cy - inner_half_h, cx + inner_half_w, cy + inner_half_h],
            radius=logo_radius * 0.8, fill=(*NAVY, 255),
        )

        img.paste(logo, (cx - logo_w // 2, cy - logo_h // 2), logo)

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


def _write_session_log() -> Optional[str]:
    """Writes post-service session directory under logs/sessions/YYYYMMDD_HHMMSS/.

    Consolidated v3.0 format:
    Exactly 4 canonical files regardless of target count:
        session.json    — Machine-readable manifest (session metadata, cost accounting, turns)
        transcript.jsonl— Canonical machine-readable turn records (1 JSON per committed turn)
        transcript.md   — Human-readable multi-language chronological transcript
        summary.txt     — Concise operational performance & billing summary
    """
    try:
        for s in manager.sessions.values():
            s.flush_current_turn()

        runtime = _runtime_seconds()
        max_turns = max([len(s.transcript) for s in manager.sessions.values()], default=0)

        # Do not export zero-content sessions (e.g. initial teardown before first start, or instant cancel)
        if max_turns == 0 and runtime < 5.0:
            server_log.debug("Skipping session export: zero turns and negligible runtime (<5s)")
            return None
        if not manager.sessions and max_turns == 0:
            server_log.debug("Skipping session export: no active sessions or turns")
            return None

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = Path(logging_cfg().get("log_dir", "logs")) / "sessions" / ts
        session_dir.mkdir(parents=True, exist_ok=True)

        active_tgts = list(manager.active_targets) if manager.active_targets else ["en"]
        per_target_cost = _billed_seconds * _COST_PER_AUDIO_SEC
        total_cost = len(active_tgts) * per_target_cost

        session_end_dt = datetime.now()
        session_start_dt = (
            session_end_dt - timedelta(seconds=runtime)
            if runtime > 0
            else session_end_dt
        )

        primary_sess = manager.sessions.get(manager.primary_target)

        t0 = _service_start_time if _service_start_time is not None else None
        if t0 is None:
            for s in manager.sessions.values():
                if s.transcript:
                    t0 = s.transcript[0].timestamp
                    break
        if t0 is None:
            t0 = 0.0

        def turn_time_str(t: float) -> str:
            offset = max(0.0, t - t0)
            turn_dt = session_start_dt + timedelta(seconds=offset)
            return turn_dt.strftime("%H:%M:%S")

        # Collect consolidated turn records
        consolidated_turns: List[dict] = []
        for i in range(max_turns):
            rep_entry = None
            if primary_sess and i < len(primary_sess.transcript):
                rep_entry = primary_sess.transcript[i]
            else:
                for s in manager.sessions.values():
                    if i < len(s.transcript):
                        rep_entry = s.transcript[i]
                        break

            if not rep_entry:
                continue

            time_label = turn_time_str(rep_entry.timestamp)
            src_lang = rep_entry.source_lang or manager.expected_source_language
            src_text = rep_entry.source

            targets_dict = {}
            for tgt, sess in manager.sessions.items():
                if i < len(sess.transcript):
                    targets_dict[tgt] = sess.transcript[i].target

            consolidated_turns.append({
                "timestamp": time_label,
                "source": {
                    "lang": src_lang,
                    "text": src_text,
                },
                "targets": targets_dict,
            })

        src_info = get_language(manager.expected_source_language)
        src_name = src_info.name if src_info else manager.expected_source_language.upper()

        tgt_names = []
        for tgt in active_tgts:
            info = get_language(tgt)
            tgt_names.append(info.name if info else tgt.upper())

        # ── 1. session.json ───────────────────────────────────────────────
        session_manifest = {
            "session_id": ts,
            "spoken_language": manager.expected_source_language,
            "expected_source_language": manager.expected_source_language,
            "active_targets": active_tgts,
            "started_at": session_start_dt.isoformat(),
            "ended_at": session_end_dt.isoformat(),
            "runtime_seconds": round(runtime, 1),
            "audio_billed_seconds": round(_billed_seconds, 1),
            "estimated_total_cost_usd": round(total_cost, 4),
            "cost_by_target": {tgt: round(per_target_cost, 4) for tgt in active_tgts},
            "total_turns": len(consolidated_turns),
            "configured_model": gemini_cfg().get("model", model_resolver.preferred_model),
            "resolved_model": model_resolver.active_model,
            "model": model_resolver.active_model,
            "captions_by_target": {
                tgt: manager.broadcasters[tgt].caption_count
                for tgt in active_tgts if tgt in manager.broadcasters
            },
        }
        (session_dir / "session.json").write_text(json.dumps(session_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        # ── 2. transcript.jsonl ───────────────────────────────────────────
        jsonl_lines = [json.dumps(turn, ensure_ascii=False) for turn in consolidated_turns]
        (session_dir / "transcript.jsonl").write_text("\n".join(jsonl_lines) + ("\n" if jsonl_lines else ""), encoding="utf-8")

        # ── 3. transcript.md ──────────────────────────────────────────────
        md_sections = [
            "# Translation Transcript",
            "",
            f"Spoken language: {src_name}",
            f"Targets: {', '.join(tgt_names)}",
            "",
        ]

        for turn in consolidated_turns:
            time_label = turn["timestamp"]
            src_t = turn["source"]["text"]

            md_sections.append(f"## {time_label}")
            md_sections.append("")
            md_sections.append(f"**Spoken — {src_name}**")
            md_sections.append(src_t)
            md_sections.append("")

            for tgt in active_tgts:
                t_val = turn["targets"].get(tgt, "")
                t_info = get_language(tgt)
                if t_info and t_info.native_name and t_info.native_name != t_info.name:
                    tgt_header = f"**{t_info.native_name} ({t_info.name})**"
                elif t_info:
                    tgt_header = f"**{t_info.name}**"
                else:
                    tgt_header = f"**{tgt.upper()}**"

                md_sections.append(tgt_header)
                md_sections.append(t_val)
                md_sections.append("")

            md_sections.append("---")
            md_sections.append("")

        (session_dir / "transcript.md").write_text("\n".join(md_sections).rstrip() + "\n", encoding="utf-8")

        # ── 4. summary.txt ────────────────────────────────────────────────
        duration_min = int(runtime // 60)
        duration_sec = int(runtime % 60)
        summary_lines = [
            f"Session:        {ts}",
            f"Session ended:  {session_end_dt.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Duration:       {duration_min}m {duration_sec:02d}s ({runtime:.0f}s)",
            f"Spoken:         {src_name}",
            f"Active Targets: {', '.join(tgt_names)}",
            f"Committed turns:{len(consolidated_turns)}",
            f"Audio billed:   {_billed_seconds:.0f}s per target",
            f"Est. total cost:${total_cost:.4f} USD",
            f"Model:          {model_resolver.active_model}",
        ]
        (session_dir / "summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

        server_log.info("Session exported: %s (canonical 4-file format, %d turns, %d active targets)",
                        session_dir, len(consolidated_turns), len(manager.sessions))
        return f"logs/sessions/{ts}"
    except Exception as e:
        server_log.warning("Could not write session log: %s", e)
        return None




@asynccontextmanager
async def lifespan(app: FastAPI):
    global _qr_png_cache
    cfg = network_cfg()
    port = cfg.get("port", 8080)
    hostname = (cfg.get("hostname", "") or "").strip()
    ip_addr = _local_ip()

    log_auth_status_on_startup()

    if not getattr(app.state, "tunnel_manager", None):
        from app.tunnel import CloudflareTunnelManager
        app.state.tunnel_manager = CloudflareTunnelManager(
            port=port,
            enabled=cfg.get("enable_tunnel", True),
            public_url=cfg.get("public_url", ""),
        )
        app.state.tunnel_manager.start()

    if hostname:
        import threading
        threading.Thread(
            target=_register_zeroconf,
            args=(hostname, port, ip_addr),
            daemon=True
        ).start()

    primary_url, fallback_url, public_url_cfg = _get_live_urls()
    active_share_url, _ = _get_active_attendee_share_url()
    _qr_png_cache = _build_qr(active_share_url)
    server_log.info("QR code URL: %s", active_share_url)
    server_log.info("Local URL: %s", primary_url)
    server_log.info("Public URL: %s", public_url_cfg)
    server_log.info("Fallback URL: %s", fallback_url)

    # Launch non-blocking background discovery
    model_resolver.start_background_discovery()

    operator_events.add(
        "success", "System started",
        {"port": port, "primary_url": primary_url, "fallback_url": fallback_url}
    )

    async def _ping():
        while True:
            await asyncio.sleep(15)
            for b in manager.broadcasters.values():
                b._push(CaptionEvent(kind="ping"))

    asyncio.create_task(_ping())
    try:
        yield
    finally:
        tunnel_mgr = getattr(app.state, "tunnel_manager", None)
        if tunnel_mgr:
            tunnel_mgr.stop()
        _unregister_zeroconf()
        await manager.stop()


class PublicHostGuardMiddleware:
    """Strict default-deny boundary for the public attendee hostname."""
    _HTTP_GET = {"/", "/live", "/stream", "/logo.webp", "/logo.png", "/logo", "/api/languages"}
    _WEBSOCKETS = {"/audio-stream", "/ws/telemetry"}

    _WEBSOCKETS = {"/audio-stream", "/ws/telemetry"}

    def __init__(self, app: ASGIApp):
        self.app = app

    @classmethod
    def _get_public_hosts(cls) -> set[str]:
        cfg = network_cfg()
        pub_url = cfg.get("public_url", "")
        hosts = {"live.starkvillekoreanchurch.org"}
        if pub_url:
            from urllib.parse import urlparse
            parsed = urlparse(str(pub_url) if "://" in str(pub_url) else f"https://{pub_url}")
            if parsed.hostname:
                hosts.add(parsed.hostname.lower())
        return hosts

    def _is_public_host(self, scope: Scope) -> bool:
        headers = {key.lower(): value.decode("latin1") for key, value in scope.get("headers", [])}
        host = headers.get(b"host", "")
        forwarded_host = headers.get(b"x-forwarded-host", "")
        forwarded = headers.get(b"forwarded", "")
        candidates = [host, forwarded_host]
        candidates.extend(
            part.split("=", 1)[1]
            for part in forwarded.split(";")
            if part.lower().startswith("host=") and "=" in part
        )
        public_hosts = self._get_public_hosts()
        for cand in candidates:
            if not cand:
                continue
            cand_host = cand.split(":", 1)[0].strip().lower()
            if cand_host in public_hosts:
                return True
        return False

    async def _not_found(self, send: Send) -> None:
        body = b"Not Found"
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if not self._is_public_host(scope):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if scope["type"] == "http":
            is_allowed_path = (path in self._HTTP_GET) or path.startswith("/static/")
            if scope.get("method") != "GET" or not is_allowed_path:
                await self._not_found(send)
                return
            if path == "/":
                scope = dict(scope)
                scope["path"] = "/live"
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket" and path in self._WEBSOCKETS:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        await self._not_found(send)


app = FastAPI(lifespan=lifespan)
app.add_middleware(PublicHostGuardMiddleware)

# ── Mount static assets ───────────────────────────────────────────────────────
import sys
from fastapi.staticfiles import StaticFiles

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _static_dir = Path(sys._MEIPASS) / "app" / "static"
else:
    _static_dir = Path(__file__).parent / "static"
    if not _static_dir.exists():
        _static_dir = Path(__file__).parent.parent / "app" / "static"

if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── SSE caption stream ────────────────────────────────────────────────────────
async def _sse_generator(request: Request, q: asyncio.Queue, target_broadcaster: CaptionBroadcaster) -> AsyncIterator[str]:
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=20.0)
                text_val = event.source if event.kind == "source" else (event.target or event.text)
                payload = {
                    "kind": event.kind,
                    "text": text_val,
                    "target": event.target,
                    "source": event.source,
                    "ko": event.source or getattr(event, "ko", ""),
                    "source_lang": event.source_lang,
                    "target_lang": event.target_lang,
                }
                if event.kind == "commit":
                    runtime = _runtime_seconds()
                    m, s = divmod(int(runtime), 60)
                    payload["time_str"] = f"{m:02d}:{s:02d}"
                yield f"data: {json.dumps(payload)}\n\n"


            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        target_broadcaster.remove_client(q)



@app.get("/stream")
async def caption_stream(request: Request, lang: Optional[str] = None):
    if lang:
        clean_lang = lang.lower().strip()
        if not is_valid_language_code(clean_lang):
            return JSONResponse(
                {"error": "invalid_language", "message": f"'{lang}' is not a valid language code."},
                status_code=400,
            )
        if clean_lang not in manager.active_targets:
            return JSONResponse(
                {
                    "error": "target_not_active",
                    "target": clean_lang,
                    "active_targets": list(manager.active_targets),
                },
                status_code=404,
            )
        target_broadcaster = manager.get_broadcaster(clean_lang)
    else:
        target_broadcaster = manager.primary_broadcaster

    if not target_broadcaster:
        target_broadcaster = manager.primary_broadcaster

    q = target_broadcaster.add_client()
    return StreamingResponse(
        _sse_generator(request, q, target_broadcaster),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Binary WebSocket audio stream ─────────────────────────────────────────────
@app.websocket("/audio-stream")
async def audio_stream(ws: WebSocket, lang: Optional[str] = None):
    if lang:
        clean_lang = lang.lower().strip()
        if not is_valid_language_code(clean_lang) or clean_lang not in manager.active_targets:
            await ws.close(code=1008)
            return
        target_broadcaster = manager.get_broadcaster(clean_lang)
    else:
        target_broadcaster = manager.primary_broadcaster

    if not target_broadcaster:
        await ws.close(code=1008)
        return

    await ws.accept()
    q = target_broadcaster.add_audio_client()
    try:
        while True:
            try:
                pcm = await asyncio.wait_for(q.get(), timeout=10.0)
                await ws.send_bytes(pcm)
            except asyncio.TimeoutError:
                # Keepalive: send empty bytes frame during silence to prevent disconnect
                await ws.send_bytes(b"")
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        server_log.debug("WebSocket audio client disconnected: %s", e)
    finally:
        target_broadcaster.remove_audio_client(q)



# ── WebSocket telemetry stream ───────────────────────────────────────────────
@app.websocket("/ws/telemetry")
async def telemetry_stream(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")
            if msg_type == "latency_ping":
                await ws.send_json({
                    "type": "latency_pong",
                    "client_sent_ms": data.get("client_sent_ms"),
                })
            elif msg_type == "latency_report":
                hostname = str(data.get("hostname", ""))
                rtt_ms = float(data.get("rtt_ms", 0))
                client_id = str(data.get("client_id", ""))
                target_lang = str(data.get("target_lang", ""))
                if rtt_ms > 0:
                    manager.primary_broadcaster.record_rtt(
                        hostname, rtt_ms, client_id=client_id, target_lang=target_lang
                    )
                    if target_lang:
                        b = manager.get_broadcaster(target_lang)
                        if b and b != manager.primary_broadcaster:
                            b.record_rtt(
                                hostname, rtt_ms, client_id=client_id, target_lang=target_lang
                            )

    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        pass


# ── Operator Authentication API ───────────────────────────────────────────────
@app.get("/api/auth/status")
async def auth_status(request: Request):
    return {
        "auth_enabled": is_auth_enabled(),
        "authenticated": is_authenticated(request),
    }


@app.post("/api/auth/login")
async def auth_login(body: dict, response: Response):
    password = str(body.get("password", "")).strip()
    if not is_auth_enabled():
        return {"ok": True, "auth_enabled": False}
    if verify_password(password):
        token = create_session_token()
        set_auth_cookie(response, token)
        server_log.info("Operator login successful")
        operator_events.add("user", "Operator logged in")
        return {"ok": True, "token": token}
    server_log.warning("Operator login failed: invalid password")
    return Response(
        content=json.dumps({"ok": False, "error": "Invalid password"}),
        status_code=401,
        media_type="application/json",
    )


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    clear_auth_cookie(response)
    server_log.info("Operator logged out")
    operator_events.add("user", "Operator logged out")
    return {"ok": True}


# ── Operator control API ───────────────────────────────────────────────────────
async def _teardown():
    global _state, _paused, _pause_start
    saved_dir = _write_session_log()
    if saved_dir:
        operator_events.add("info", f"Session logs saved: {saved_dir}")
    await manager.stop()
    _paused = False
    _pause_start = None
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


def _check_auth(request: Request) -> Response | None:
    if request is not None and not is_authenticated(request):
        return Response(
            content=json.dumps({"ok": False, "error": "Unauthorized"}),
            status_code=401,
            media_type="application/json",
        )
    return None


@app.post("/api/start")
async def start_service(request: Request = None, body: dict = None, from_auto_restart: bool = False):
    if not from_auto_restart:
        if auth_err := _check_auth(request):
            return auth_err
    if body is None:
        if request is not None:
            try:
                body = await request.json()
            except Exception:
                body = {}
        else:
            body = {}
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
            t_cfg = translation_cfg()
            if "targets" in body:
                active_targets = body.get("targets")
                if not active_targets:
                    return JSONResponse(
                        status_code=400,
                        content={"ok": False, "error": "no_targets_selected", "message": "At least one target language must be selected."}
                    )
            else:
                active_targets = t_cfg.get("default_active_targets", ["en"])

            expected_src = body.get("expected_source_language") or t_cfg["expected_source_language"]

            await manager.start(
                device_index=device_index,
                active_targets=active_targets,
                expected_source_language=expected_src,
            )
            _service_start_time = time.monotonic()
            _billed_seconds = 0.0
            _paused = False
            _pause_start = None

            asyncio.create_task(_auto_stop_check())
            _state = ServiceState.RUNNING
            server_log.info("Service started with targets: %s", manager.active_targets)
            operator_events.add("success", f"Translation started ({', '.join(manager.active_targets).upper()})")
        except ValueError as ve:
            server_log.warning("Failed to start service: %s", ve)
            await _teardown()
            _state = ServiceState.STOPPED
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "invalid_configuration", "message": str(ve)}
            )
        except Exception as e:
            server_log.error("Failed to start service: %s", e)
            await _teardown()
            _state = ServiceState.STOPPED
            raise e
    return {"ok": True}


@app.post("/api/stop")
async def stop_service(request: Request = None):
    if auth_err := _check_auth(request):
        return auth_err
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
    if auth_err := _check_auth(request):
        return auth_err
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
async def pause_service(request: Request = None):
    if auth_err := _check_auth(request):
        return auth_err
    global _paused, _pause_start
    if _state == ServiceState.RUNNING and not _paused:
        _paused = True
        _pause_start = time.monotonic()
        await manager.pause_clean()
        server_log.info("Service paused (clean standby on locked model)")
        operator_events.add("user", "Translation paused (clean standby)")
    return {"ok": True, "paused": _paused}


@app.post("/api/resume")
async def resume_service(request: Request = None):
    if auth_err := _check_auth(request):
        return auth_err
    global _paused, _pause_start
    if _state == ServiceState.RUNNING and _paused:
        await manager.resume_clean()
        _paused = False
        _pause_start = None
        server_log.info("Service resumed (fresh Gemini sessions on locked model)")
        operator_events.add("user", "Translation resumed (fresh context)")
    return {"ok": True, "paused": _paused}


@app.post("/api/config/auto-drift-correction")
@app.post("/api/drift-correction")
async def set_auto_drift_correction(request: Request, body: dict):
    if auth_err := _check_auth(request):
        return auth_err
    enabled = bool(body.get("enabled", body.get("auto_drift_correction", False)))
    manager.set_auto_drift_correction(enabled)
    if session:
        session.set_auto_drift_correction(enabled)
        session.clear_drift_state()
    server_log.info("Auto drift correction set to: %s (runtime-only)", enabled)
    operator_events.add("config", f"Auto drift correction set to {'ON' if enabled else 'OFF'}")
    return {"ok": True, "auto_drift_correction": enabled, "enabled": enabled}


@app.post("/api/config/ui-language")
async def set_operator_ui_language_endpoint(request: Request, body: dict):
    if auth_err := _check_auth(request):
        return auth_err
    lang = str(body.get("default_ui_language") or body.get("lang") or "ko").strip().lower()
    clean_lang = "en" if lang == "en" else "ko"
    save_operator_ui_language(clean_lang)
    server_log.info("Operator default UI language saved to config.yaml: %s", clean_lang)
    operator_events.add("config", f"Operator default UI language set to: {clean_lang}")
    return {"ok": True, "default_ui_language": clean_lang}



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
                await start_service(request=None, body={"device_index": device_index}, from_auto_restart=True)
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
        for b in manager.broadcasters.values():
            b.set_unavailable()
        async with _state_lock:
            _state = ServiceState.FAILED

    except asyncio.CancelledError:
        server_log.info("Auto-restart loop cancelled")
        _auto_restart_attempt = 0
        _auto_restart_reason = ""
        raise


def _handle_session_state_change(*args, **kwargs):
    global _auto_restart_task
    if len(args) == 1:
        target = manager.primary_target
        s = args[0]
    elif len(args) >= 2:
        target, s = args[0], args[1]
    else:
        target = kwargs.get("target", manager.primary_target)
        s = kwargs.get("s") or kwargs.get("state")

    if s and s.status == SessionStatus.FAILED:
        b = manager.get_broadcaster(target)
        if b:
            b.set_unavailable()
        if _auto_restart_task and not _auto_restart_task.done():
            _auto_restart_task.cancel()
        # Non-retryable configuration errors should stop once and NOT trigger auto-restart loops
        if "Configuration error" in (s.last_event or ""):
            server_log.error("[%s] Session failed with non-retryable configuration error — skipping auto-restart", target)
            return
        _auto_restart_task = asyncio.create_task(_auto_stop_on_failure(f"[{target}] {s.last_event}"))


manager._on_session_state = _handle_session_state_change



@app.post("/api/config/auto-stop")
async def set_auto_stop(request: Request, body: dict):
    if auth_err := _check_auth(request):
        return auth_err
    minutes = int(body["minutes"])
    save_auto_stop_timeout(minutes)
    operator_events.add("user", f"Auto-stop set to {minutes} min")
    return {"ok": True, "minutes": minutes}


@app.post("/api/reconnect-public")
async def reconnect_public_link(request: Request = None):
    if auth_err := _check_auth(request):
        return auth_err
    tunnel_mgr = getattr(app.state, "tunnel_manager", None)
    if tunnel_mgr:
        tunnel_mgr.reconnect()
    operator_events.add("user", "Public attendee link check requested")
    return {"ok": True, "status": tunnel_mgr.status if tunnel_mgr else "unavailable"}


# ── Language Discovery & Target Selection APIs ────────────────────────────────
@app.get("/api/languages")
async def get_languages():
    cfg = translation_cfg()
    return {
        "expected_source": cfg["expected_source_language"],
        "available": [
            {
                "code": lang.code,
                "name": lang.name,
                "native_name": lang.native_name,
                "display_name": lang.display_name(),
            }
            for lang in get_available_languages()
        ],
        "supported_targets": cfg["supported_targets"],
        "selected_targets": cfg["default_active_targets"],
        "active_targets": list(manager.active_targets) if manager.is_running else cfg["default_active_targets"],
    }


@app.get("/api/translation/targets")
async def get_translation_targets(request: Request = None):
    if auth_err := _check_auth(request):
        return auth_err
    cfg = translation_cfg()
    return {
        "expected_source_language": cfg["expected_source_language"],
        "supported_targets": cfg["supported_targets"],
        "selected_targets": cfg["default_active_targets"],
        "active_targets": list(manager.active_targets) if manager.is_running else cfg["default_active_targets"],
        "is_running": manager.is_running,
        "is_paused": manager.is_paused,
    }


@app.post("/api/translation/targets")
@app.put("/api/translation/targets")
async def update_translation_targets(request: Request, body: dict):
    if auth_err := _check_auth(request):
        return auth_err
    if manager.is_running or manager.is_paused:
        return Response(
            content=json.dumps({
                "error": "translation_running",
                "message": "Stop translation before changing target languages.",
            }),
            status_code=409,
            media_type="application/json",
        )

    cfg = translation_cfg()
    raw_targets = body.get("targets") or body.get("default_active_targets") or cfg["default_active_targets"]
    raw_src = body.get("expected_source_language") or cfg["expected_source_language"]
    raw_supported = body.get("supported_targets") or cfg["supported_targets"]

    try:
        new_cfg = save_translation_settings(
            expected_source_language=raw_src,
            supported_targets=raw_supported,
            default_active_targets=raw_targets,
        )
        operator_events.add("config", f"Translation targets updated: {new_cfg['default_active_targets']}")
        return {"ok": True, "translation": new_cfg}
    except ValueError as e:
        return Response(
            content=json.dumps({"ok": False, "error": str(e)}),
            status_code=400,
            media_type="application/json",
        )


@app.get("/api/status")
async def get_status():
    global _tunnel_logged, _tunnel_failed_logged
    a = audio.state
    mgr_state = manager.state()
    primary_sess = manager.sessions.get(manager.primary_target)
    primary_sess_state = primary_sess.state if primary_sess else None

    runtime = _runtime_seconds()
    cost = _billed_seconds * _COST_PER_AUDIO_SEC
    local_url, fallback_url, public_url_cfg = _get_live_urls()
    active_share_url, _ = _get_active_attendee_share_url()
    ch = church_cfg()

    tunnel_mgr = getattr(app.state, "tunnel_manager", None)
    tunnel_enabled = tunnel_mgr.enabled if tunnel_mgr else False
    tunnel_ready = tunnel_mgr.is_ready if tunnel_mgr else False
    tunnel_url = tunnel_mgr.tunnel_url if tunnel_mgr else None
    public_attendee_url = tunnel_mgr.public_attendee_url if tunnel_mgr else public_url_cfg
    tunnel_error = tunnel_mgr.error_message if tunnel_mgr else None

    if tunnel_ready and public_attendee_url and not _tunnel_logged:
        _tunnel_logged = True
        operator_events.add("success", f"HTTPS Tunnel Ready: {public_attendee_url}")
        server_log.info("HTTPS Tunnel Ready: %s", public_attendee_url)
    elif tunnel_error and not _tunnel_failed_logged:
        _tunnel_failed_logged = True
        operator_events.add("warning", "Public HTTPS unavailable. Local translation remains ready.")

    telemetry = manager.primary_broadcaster.get_telemetry_stats()
    gemini_lat = round(primary_sess_state.last_latency_ms, 1) if primary_sess_state else 0.0
    local_rtt = telemetry.get("local_rtt_ms")
    public_rtt = telemetry.get("public_rtt_ms")

    est_local_delay_s = round((gemini_lat + (local_rtt or 5) + 200) / 1000.0, 2) if gemini_lat > 0 else None
    est_public_delay_s = round((gemini_lat + (public_rtt or 150) + 200) / 1000.0, 2) if gemini_lat > 0 else None

    total_clients = sum(b.client_count for b in manager.broadcasters.values()) if manager.broadcasters else broadcaster.client_count
    total_audio = sum(b.audio_client_count for b in manager.broadcasters.values()) if manager.broadcasters else broadcaster.audio_client_count

    t_cfg = translation_cfg()
    active_tgts = list(manager.active_targets) if manager.is_running else t_cfg["default_active_targets"]
    per_target_cost = _billed_seconds * _COST_PER_AUDIO_SEC
    total_cost = len(active_tgts) * per_target_cost

    session_states = mgr_state.get("sessions", {})
    for tgt, s_info in session_states.items():
        s_info["estimated_cost"] = round(per_target_cost, 4)

    translation_info = {
        "expected_source": manager.expected_source_language if manager.is_running else t_cfg["expected_source_language"],
        "selected_targets": t_cfg["default_active_targets"],
        "active_targets": active_tgts,
        "primary_target": manager.primary_target,
        "estimated_total_cost": round(total_cost, 4),
        "sessions": session_states,
    }

    return {
        "service_running": _state != ServiceState.STOPPED,
        "state": _state.value,
        "paused": _paused,
        "pause_duration_s": round(time.monotonic() - _pause_start, 1) if (_paused and _pause_start) else 0.0,
        "runtime_s": round(runtime, 1),
        "cost_usd": round(total_cost, 4),
        "billed_audio_s": round(_billed_seconds, 1),

        "auto_stop_timeout_min": audio_cfg().get("auto_stop_timeout_min", 10),
        "auto_drift_correction": primary_sess.auto_drift_correction if primary_sess else manager.auto_drift_correction,
        "session_epoch": primary_sess.session_epoch if primary_sess else 0,
        "device_index": audio_cfg().get("device_index", 0),
        "auto_restart_attempt": _auto_restart_attempt,
        "auto_restart_reason": _auto_restart_reason,
        "admin_url": _get_admin_url(),
        "church": {
            "name": ch.get("name", "Starkville Korean Church"),
            "short_name": ch.get("short_name", "SKC"),
            "default_ui_language": ch.get("default_ui_language", "ko"),
        },
        "telemetry": {
            "gemini_latency_ms": gemini_lat,
            "local_rtt_ms": local_rtt,
            "local_samples": telemetry.get("local_samples", 0),
            "local_listeners": telemetry.get("local_listeners", 0),
            "public_rtt_ms": public_rtt,
            "public_samples": telemetry.get("public_samples", 0),
            "public_listeners": telemetry.get("public_listeners", 0),
            "est_local_delay_s": est_local_delay_s,
            "est_public_delay_s": est_public_delay_s,
        },
        "audio": {
            "status": a.status,
            "level": round(a.level_rms, 1),
            "device": a.device_name,
        },
        "session": {
            "status": primary_sess_state.status if primary_sess_state else SessionStatus.STOPPED,
            "reconnect_count": primary_sess_state.reconnect_count if primary_sess_state else 0,
            "last_event": primary_sess_state.last_event if primary_sess_state else None,
            "latency_ms": gemini_lat,
            "model": model_resolver.active_model,
        },
        "translation": translation_info,
        "church": church_cfg(),
        "models": model_resolver.get_state(),

        "attendees": max(
            telemetry.get("total_listeners") or 0,
            total_clients,
            total_audio,
        ),
        "captions": manager.primary_broadcaster.caption_count,
        "last_caption_ago_s": manager.primary_broadcaster.last_caption_ago_s,
        "live_url_primary": active_share_url,
        "live_url_local": local_url,
        "live_url_fallback": fallback_url,
        "live_url_public": public_attendee_url,
        "tunnel_enabled": tunnel_enabled,
        "tunnel_ready": tunnel_ready,
        "tunnel_url": tunnel_url,
        "public_attendee_url": public_attendee_url,
        "tunnel_error": tunnel_error,
        "local_translation_status": "ready",
        "public_https_status": (tunnel_mgr.status if tunnel_mgr else ("available" if tunnel_ready else "unavailable")),
    }



@app.get("/api/devices")
async def get_devices(request: Request = None, rescan: bool = False):
    if auth_err := _check_auth(request):
        return auth_err
    should_reinit = rescan and (_state != ServiceState.RUNNING)
    return [
        {"index": d.index, "name": d.name,
         "channels": d.max_input_channels, "rate": int(d.default_sample_rate)}
        for d in list_input_devices(rescan=should_reinit)
    ]


@app.post("/api/devices/select")
async def select_device(request: Request, body: dict):
    if auth_err := _check_auth(request):
        return auth_err
    idx = int(body["index"])
    save_audio_device(idx)
    return {"ok": True, "index": idx}


@app.get("/api/models")
async def get_models_state(request: Request = None):
    if auth_err := _check_auth(request):
        return auth_err
    return model_resolver.get_state()


@app.post("/api/models/select")
@app.post("/api/models/mode")
async def select_model(request: Request, body: dict):
    if auth_err := _check_auth(request):
        return auth_err
    model = str(body.get("model") or body.get("preferred_model") or "").strip()
    if not model:
        return Response(
            content=json.dumps({"ok": False, "error": "Model name required"}),
            status_code=400,
            media_type="application/json",
        )
    try:
        model_resolver.set_preferred_model(model)
        operator_events.add("user", f"Preferred Gemini model set to '{model}'")
        return {"ok": True, "state": model_resolver.get_state()}
    except Exception as e:
        return Response(
            content=json.dumps({"ok": False, "error": str(e)}),
            status_code=400,
            media_type="application/json",
        )


@app.post("/api/models/test")
async def test_model(request: Request, body: dict):
    if auth_err := _check_auth(request):
        return auth_err
    model_name = str(body.get("model", "")).strip()
    if not model_name:
        return Response(
            content=json.dumps({"ok": False, "error": "Model name required"}),
            status_code=400,
            media_type="application/json",
        )
    is_compat, caps, msg = await verify_model_compatibility(model_name)
    return {"ok": is_compat, "capabilities": caps, "message": msg, "model": model_name}


@app.post("/api/models/dismiss-alert")
async def dismiss_model_alert(request: Request, body: dict):
    if auth_err := _check_auth(request):
        return auth_err
    model_name = str(body.get("model", "")).strip()
    if model_name:
        model_resolver.dismiss_alert(model_name)
    return {"ok": True}


@app.get("/api/qr.png")
async def qr_png(type: str = "primary"):
    active_url, fallback_url = _get_active_attendee_share_url()
    local_url, _, public_url = _get_live_urls()

    if type == "local":
        target_url = local_url
    elif type == "public":
        target_url = public_url
    else:
        target_url = active_url

    qr_bytes = _build_qr(target_url)
    return Response(
        content=qr_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/logo.webp")
@app.get("/logo.png")
@app.get("/logo")
async def get_logo():
    cfg = church_cfg()
    logo_rel = cfg.get("logo", "")
    app_root = get_app_root()
    if logo_rel:
        custom_logo = app_root / logo_rel
        if custom_logo.exists():
            media_type = "image/png"
            if custom_logo.suffix.lower() in [".jpg", ".jpeg"]:
                media_type = "image/jpeg"
            elif custom_logo.suffix.lower() == ".webp":
                media_type = "image/webp"
            with open(custom_logo, "rb") as f:
                content = f.read()
            return Response(content=content, media_type=media_type)

    logo_path = Path(__file__).parent / "pca-logo-white-small.webp"
    if not logo_path.exists():
        return Response(status_code=404)
    with open(logo_path, "rb") as f:
        content = f.read()
    return Response(content=content, media_type="image/webp")


@app.get("/api/events")
async def get_events(request: Request = None, since: int = -1):
    if auth_err := _check_auth(request):
        return auth_err
    return {"events": operator_events.since(since), "latest_id": operator_events.latest_id}


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/help", response_class=HTMLResponse)
async def help_page():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        help_path = Path(sys._MEIPASS) / "how_to_use.html"
    else:
        help_path = Path(__file__).parent.parent / "how_to_use.html"
    if not help_path.exists():
        return HTMLResponse("Help file not found", status_code=404)
    return HTMLResponse(help_path.read_text(encoding="utf-8"))


@app.get("/live", response_class=HTMLResponse)
async def attendee_page():
    if getattr(sys, "frozen", False):
        return _ATTENDEE_HTML_CACHE
    return _read_template("attendee.html")


@app.get("/admin", response_class=HTMLResponse)
async def operator_page():
    default_lang = church_cfg().get("default_ui_language", "ko")
    if getattr(sys, "frozen", False):
        return _read_template("operator.html", default_ui_lang=default_lang)
    return _read_template("operator.html", default_ui_lang=default_lang)


@app.get("/", response_class=HTMLResponse)
async def root_redirect():
    return RedirectResponse(url="/live", status_code=307)


def _read_template(filename: str, **kwargs) -> str:
    import sys
    from jinja2 import Environment, FileSystemLoader
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        template_dir = Path(sys._MEIPASS) / "app" / "templates"
    else:
        template_dir = Path(__file__).parent / "templates"
        if not template_dir.exists():
            template_dir = Path(__file__).parent.parent / "app" / "templates"

    try:
        env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=False)
        return env.get_template(filename).render(**kwargs)
    except Exception as e:
        server_log.error("Failed to render template %s: %s", filename, str(e))
        return f"Error: Template {filename} failed to render: {e}"


# Cache templates in production
import sys
_ATTENDEE_HTML_CACHE = _read_template("attendee.html")

