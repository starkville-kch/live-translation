"""
app/config.py — Configuration & Environment Loader
====================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Single source of truth for all runtime configuration.

Startup sequence
----------------
1. ``load_dotenv()`` reads the ``.env`` file in the project root and injects
   ``GEMINI_API_KEY`` (and any other overrides) into ``os.environ``.
2. ``_load()`` parses ``config.yaml`` once and caches it in ``_cfg``.
3. Public helper functions return sub-sections of ``_cfg`` by name.

config.yaml schema (abbreviated)
----------------------------------
::

    church:
      name: "Starkville Korean Church"
      short_name: "SKC"
      logo: "branding/church-logo.png"

    audio:
      device_index: 2          # PyAudio input device index (set by --list)
      sample_rate: 48000       # native device rate; resampled to 16kHz internally
      chunk_ms: 100            # capture chunk size in milliseconds

    network:
      host: "0.0.0.0"          # bind address
      hostname: "skc"          # mDNS hostname base
      port: 80

    gemini:
      model: "gemini-3.5-live-translate-preview"   # auto-updated by resolve_live_model()

    logging:
      log_dir: "logs"
      max_bytes: 10485760      # 10 MB per log file
      backup_count: 5

Public API
----------
``gemini_api_key()``       — returns GEMINI_API_KEY or raises RuntimeError
``church_cfg()``           — returns the ``church`` section dict
``audio_cfg()``            — returns the ``audio`` section dict
``network_cfg()``          — returns the ``network`` section dict
``logging_cfg()``          — returns the ``logging`` section dict
``gemini_model()``         — returns the currently configured Gemini model name
``save_church_identity()`` — persists church identity & hostname back to config.yaml
``save_audio_device()``    — persists a new device index back to config.yaml
``save_auto_stop_timeout()``— persists auto-stop timeout minutes back to config.yaml
``save_gemini_model()``    — persists a new model name back to config.yaml
``update_gemini_api_key()``— atomically updates GEMINI_API_KEY in .env
``mask_api_key()``         — returns masked preview of API key
"""
import os
import sys
import tempfile
import yaml
from pathlib import Path
from dotenv import load_dotenv

# When running as a PyInstaller frozen exe, __file__ points inside the temp
# extraction folder. All user-editable files (config.yaml, .env) live next
# to the exe itself, so use sys.executable's resolved parent directory.
_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"
_ENV_PATH = _ROOT / ".env"

load_dotenv(_ENV_PATH)

DEFAULT_CONFIG = {
    "church": {
        "name": "Starkville Korean Church",
        "short_name": "SKC",
        "logo": "branding/church-logo.png",
    },
    "audio": {
        "auto_stop_timeout_min": 10,
        "channels": 1,
        "chunk_ms": 100,
        "device_index": 1,
        "sample_rate": 16000,
    },
    "gemini": {
        "context_seed": True,
        "model": "gemini-3.5-live-translate-preview",
    },
    "logging": {
        "backup_count": 5,
        "log_dir": "logs",
        "max_bytes": 10485760,
    },
    "network": {
        "host": "0.0.0.0",
        "hostname": "skc",
        "port": 80,
    },
}


def _atomic_yaml_write(path: Path, data: dict) -> None:
    """Atomically write YAML data using a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    )
    try:
        yaml.dump(data, temp_file, default_flow_style=False, allow_unicode=True)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(temp_file.name, str(path))
    except Exception:
        if os.path.exists(temp_file.name):
            try:
                os.remove(temp_file.name)
            except OSError:
                pass
        raise


def _ensure_config_file(path: Path | None = None) -> Path:
    config_path = path or _CONFIG_PATH
    if config_path.exists() and config_path.stat().st_size > 0:
        return config_path

    _atomic_yaml_write(config_path, DEFAULT_CONFIG)
    return config_path


def _load(path: Path | None = None) -> dict:
    config_path = _ensure_config_file(path)
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not data:
        _atomic_yaml_write(config_path, DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    return data


_cfg = _load()


def get_app_root() -> Path:
    """Return the base directory for user files (next to EXE in frozen mode, or project root)."""
    return _ROOT


def gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set in environment or .env file")
    return key


def church_cfg() -> dict:
    return _cfg.get("church", {
        "name": "Starkville Korean Church",
        "short_name": "SKC",
        "logo": "branding/church-logo.png",
    })


def audio_cfg() -> dict:
    return _cfg.get("audio", {})


def network_cfg() -> dict:
    return _cfg.get("network", {})


def logging_cfg() -> dict:
    return _cfg.get("logging", {})


def gemini_model() -> str:
    return _cfg.get("gemini", {}).get("model", "gemini-3.5-live-translate-preview")


def save_church_identity(
    name: str,
    short_name: str,
    hostname: str,
    logo_rel_path: str = "",
) -> None:
    """Save church identity and hostname to config.yaml atomically."""
    if "church" not in _cfg:
        _cfg["church"] = {}
    _cfg["church"]["name"] = name.strip()
    _cfg["church"]["short_name"] = short_name.strip()
    if logo_rel_path:
        _cfg["church"]["logo"] = logo_rel_path.strip()

    if "network" not in _cfg:
        _cfg["network"] = {}
    _cfg["network"]["hostname"] = hostname.strip()

    _atomic_yaml_write(_CONFIG_PATH, _cfg)


def save_audio_device(index: int) -> None:
    if "audio" not in _cfg:
        _cfg["audio"] = {}
    _cfg["audio"]["device_index"] = index
    _atomic_yaml_write(_CONFIG_PATH, _cfg)


def save_auto_stop_timeout(minutes: int) -> None:
    if "audio" not in _cfg:
        _cfg["audio"] = {}
    _cfg["audio"]["auto_stop_timeout_min"] = minutes
    _atomic_yaml_write(_CONFIG_PATH, _cfg)


def save_gemini_model(model: str) -> None:
    if "gemini" not in _cfg:
        _cfg["gemini"] = {}
    _cfg["gemini"]["model"] = model
    _atomic_yaml_write(_CONFIG_PATH, _cfg)


def mask_api_key(key: str) -> str:
    """Mask an API key for safe display (e.g. AIzaSy••••••••4xQ9)."""
    clean_key = (key or "").strip()
    if not clean_key:
        return "Not configured"
    if len(clean_key) <= 10:
        return "••••••••"
    return f"{clean_key[:6]}••••••••{clean_key[-4:]}"


def update_gemini_api_key(new_key: str, env_path: Path | None = None) -> None:
    """Atomically update or append GEMINI_API_KEY in .env while preserving existing lines."""
    target_path = env_path or _ENV_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    clean_key = new_key.strip()

    lines = []
    key_found = False
    if target_path.exists():
        with open(target_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("GEMINI_API_KEY=") or stripped.startswith("export GEMINI_API_KEY="):
            prefix = "export " if stripped.startswith("export ") else ""
            new_lines.append(f"{prefix}GEMINI_API_KEY={clean_key}\n")
            key_found = True
        else:
            new_lines.append(line)

    if not key_found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"GEMINI_API_KEY={clean_key}\n")

    # Atomic write via temp file
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(target_path.parent),
        delete=False,
        suffix=".tmp",
    )
    try:
        temp_file.writelines(new_lines)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(temp_file.name, str(target_path))
    except Exception:
        if os.path.exists(temp_file.name):
            try:
                os.remove(temp_file.name)
            except OSError:
                pass
        raise

    # Also update in-memory os.environ
    os.environ["GEMINI_API_KEY"] = clean_key
