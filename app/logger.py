"""
app/logger.py — Centralised Logging Setup
==========================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Configures three named loggers, each writing to a dedicated rotating file
plus a shared INFO-level console handler:

Named loggers
-------------
``session_log``
    Gemini Live API session lifecycle events, GoAway signals, reconnection
    attempts, latency measurements, and every Korean/English caption turn.
    Written to ``logs/session.log`` (DEBUG and above, JSON Lines format).

``audio_log``
    PyAudio device open/close events, stream errors, and silence detection
    state changes.  Written to ``logs/ops.log`` (INFO and above, JSON Lines).

``server_log``
    FastAPI lifecycle events (start/stop), operator API calls, and session
    export notifications.  Written to ``logs/ops.log`` (INFO and above, JSON Lines).

Output formats
--------------
Log files — aligned plain text (no color codes):
    2026-07-11 05:16:02.881  INFO      session     Session connected

Console — ANSI-colored, fixed-width columns:
    05:16:02  INFO      session     Session connected

Console shows INFO and above; DEBUG (e.g. per-token caption deltas) goes to
files only to keep the terminal readable during a live service.

Log rotation
------------
Each log file rotates at ``max_bytes`` (default 10 MB) and keeps
``backup_count`` (default 5) backup files.
"""
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

from app.config import logging_cfg


# ── Formatters ────────────────────────────────────────────────────────────────

class _FileFormatter(logging.Formatter):
    """Plain-text aligned columns for log files (no ANSI escape codes).

    Columns
    -------
    YYYY-MM-DD HH:MM:SS.mmm  LEVEL     logger      message
    ───────────────────────  ────────  ──────────  ────────────────────────
    2026-07-11 05:16:02.881  INFO      session     Session connected
    2026-07-11 05:16:03.012  WARNING   audio       Stream read error: …
    """
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        ts    = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S") \
                + f".{int(record.msecs):03d}"
        level = f"{record.levelname:<9}"
        name  = f"{record.name:<11}"
        line  = f"{ts}  {level}  {name}  {record.message}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ANSI escape helpers
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_LEVEL_COLOR = {
    "DEBUG":    "\033[90m",   # dark grey
    "INFO":     "\033[36m",   # cyan
    "WARNING":  "\033[33m",   # amber
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
}
_LOGGER_COLOR = {
    "session": "\033[34m",    # blue
    "audio":   "\033[32m",    # green
    "server":  "\033[35m",    # magenta
}


class _ConsoleFormatter(logging.Formatter):
    """ANSI-colored, fixed-width column output for terminal readability.

    Columns
    -------
    HH:MM:SS  LEVEL     logger      message
    ────────  ────────  ──────────  ──────────────────────────
    05:16:02  INFO      session     Session connected
    05:16:03  WARNING   audio       Stream read error: [Errno 5]
    """
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        ts       = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        lc       = _LEVEL_COLOR.get(record.levelname, "")
        nc       = _LOGGER_COLOR.get(record.name, "\033[37m")
        level    = f"{lc}{record.levelname:<9}{_RESET}"
        name     = f"{nc}{record.name:<11}{_RESET}"
        msg      = record.message
        line     = f"{_BOLD}{ts}{_RESET}  {level}  {name}  {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ── Handlers ──────────────────────────────────────────────────────────────────

_cfg     = logging_cfg()
_log_dir = Path(_cfg.get("log_dir", "logs"))
_log_dir.mkdir(exist_ok=True)

_file_fmt    = _FileFormatter()
_console_fmt = _ConsoleFormatter()


def _rotating(filename: str) -> logging.Handler:
    """Create a size-rotating file handler that writes aligned plain text."""
    h = logging.handlers.RotatingFileHandler(
        _log_dir / filename,
        maxBytes=_cfg.get("max_bytes", 10 * 1024 * 1024),
        backupCount=_cfg.get("backup_count", 5),
        encoding="utf-8",
    )
    h.setFormatter(_file_fmt)
    return h


# ops.log    — server lifecycle + audio device events (INFO and above)
# session.log — Gemini session events + caption text (DEBUG and above)
_ops_handler     = _rotating("ops.log")
_session_handler = _rotating("session.log")

_console = logging.StreamHandler()
_console.setFormatter(_console_fmt)
_console.setLevel(logging.INFO)   # DEBUG stays in files only

logging.basicConfig(level=logging.DEBUG, handlers=[_console])

# ── Named loggers ─────────────────────────────────────────────────────────────

session_log = logging.getLogger("session")
session_log.addHandler(_session_handler)
session_log.addHandler(_console)        # also echo to console at INFO+
session_log.propagate = False           # don't forward to root handler

audio_log = logging.getLogger("audio")
audio_log.addHandler(_ops_handler)
audio_log.addHandler(_console)
audio_log.propagate = False

server_log = logging.getLogger("server")
server_log.addHandler(_ops_handler)
server_log.addHandler(_console)
server_log.propagate = False
