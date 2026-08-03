"""Central logging configuration with volunteer-safe console output."""
from __future__ import annotations

import copy
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from app.config import logging_cfg

_SENSITIVE = re.compile(
    r"(?i)(?:authorization\s*:\s*)?bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:AIza[A-Za-z0-9_-]+)|(?:CLOUDFLARE_TUNNEL_TOKEN\s*=\s*[^\s,;]+)|"
    r"(?:[?&](?:key|api_key|apikey|token|secret|password|authorization)=[^&#\s]+)|"
    r"(?:\b(?:key|api_key|apikey|token|secret|password|authorization)\s*[:=]\s*[^\s,;]+)|"
    r"(?:[A-Za-z0-9+/=]{30,}\.[A-Za-z0-9+/=._-]{10,})"
)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:])")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")


def redact(value: object) -> str:
    """Render and redact arbitrary logging values without changing record args."""
    if isinstance(value, (dict, list, tuple)):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = repr(value)
    text = str(value)
    safe = {
        "http://127.0.0.1:8080/api/status": "__SAFE_LOCAL_HEALTH__",
        "http://skc-live.local:8080/live": "__SAFE_LOCAL_LIVE__",
        "https://live.starkvillekoreanchurch.org/live": "__SAFE_PUBLIC_LIVE__",
        "gemini-3.5-live-translate-preview": "__SAFE_MODEL__",
    }
    for original, marker in safe.items():
        text = text.replace(original, marker)
    text = _SENSITIVE.sub("[REDACTED]", text)
    text = _IPV4.sub("[REDACTED]", text)
    text = _IPV6.sub("[REDACTED]", text)
    text = _EMAIL.sub("[REDACTED]", text)
    for original, marker in safe.items():
        text = text.replace(marker, original)
    return text


class _SafeFormatter(logging.Formatter):
    def __init__(self, console: bool = False):
        super().__init__()
        self.console = console

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = redact(record.getMessage())
        except Exception:
            message = "[REDACTED]"
        if self.console:
            return message
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} | {record.levelname:<7} | {record.name} | {message}"
        if record.exc_info:
            line += "\n" + redact(self.formatException(record.exc_info))
        return line


class _VolunteerFilter(logging.Filter):
    """Allow only explicitly volunteer-facing warnings/errors to the console."""
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING:
            return False
        try:
            return redact(record.getMessage()).startswith(("[WARNING]", "[ERROR]"))
        except Exception:
            return False


def configure_logging() -> Path:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Avoid duplicate handlers when tests/import reload the module.
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    cfg = logging_cfg()
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    log_dir = base / cfg.get("log_dir", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"live-translation-{datetime.now():%Y-%m-%d}.log"
    file_handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_SafeFormatter())
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.addFilter(_VolunteerFilter())
    console.setFormatter(_SafeFormatter(console=True))
    root.addHandler(file_handler)
    root.addHandler(console)
    return path


LOG_PATH = configure_logging()
session_log = logging.getLogger("session")
audio_log = logging.getLogger("audio")
server_log = logging.getLogger("server")
ops_log = logging.getLogger("ops")
for _logger in (session_log, audio_log, server_log, ops_log, logging.getLogger("httpx"),
                logging.getLogger("httpcore"), logging.getLogger("uvicorn"),
                logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access"),
                logging.getLogger("websockets"), logging.getLogger("zeroconf")):
    _logger.propagate = True
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "websockets", "zeroconf"):
    logging.getLogger(_name).setLevel(logging.WARNING)
