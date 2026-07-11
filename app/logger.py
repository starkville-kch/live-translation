import logging
import logging.handlers
from pathlib import Path
from app.config import logging_cfg

_cfg = logging_cfg()
_log_dir = Path(_cfg.get("log_dir", "logs"))
_log_dir.mkdir(exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
_console_fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")


def _rotating(filename: str) -> logging.Handler:
    h = logging.handlers.RotatingFileHandler(
        _log_dir / filename,
        maxBytes=_cfg.get("max_bytes", 10 * 1024 * 1024),
        backupCount=_cfg.get("backup_count", 5),
        encoding="utf-8",
    )
    h.setFormatter(_fmt)
    return h


# ops.log  — server lifecycle + audio device events (INFO and above)
# session.log — Gemini session events + caption text (DEBUG and above)

_ops_handler     = _rotating("ops.log")
_session_handler = _rotating("session.log")
_console         = logging.StreamHandler()
_console.setFormatter(_console_fmt)
_console.setLevel(logging.INFO)  # keep console clean; DEBUG goes to files only

logging.basicConfig(level=logging.DEBUG, handlers=[_console])

session_log = logging.getLogger("session")
session_log.addHandler(_session_handler)
session_log.propagate = False  # don't also send to root/console handler

audio_log = logging.getLogger("audio")
audio_log.addHandler(_ops_handler)
audio_log.propagate = False

server_log = logging.getLogger("server")
server_log.addHandler(_ops_handler)
server_log.propagate = False
