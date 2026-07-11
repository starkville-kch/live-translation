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

    audio:
      device_index: 2          # PyAudio input device index (set by --list)
      sample_rate: 48000       # native device rate; resampled to 16kHz internally
      chunk_ms: 100            # capture chunk size in milliseconds

    network:
      host: "0.0.0.0"          # bind address
      port: 8000

    gemini:
      model: "gemini-3.5-live-translate-preview"   # auto-updated by resolve_live_model()

    logging:
      log_dir: "logs"
      max_bytes: 10485760      # 10 MB per log file
      backup_count: 5

Public API
----------
``gemini_api_key()``   — returns GEMINI_API_KEY or raises RuntimeError
``audio_cfg()``        — returns the ``audio`` section dict
``network_cfg()``      — returns the ``network`` section dict
``logging_cfg()``      — returns the ``logging`` section dict
``gemini_model()``     — returns the currently configured Gemini model name
``save_audio_device()``— persists a new device index back to config.yaml
``save_gemini_model()``— persists a new model name back to config.yaml
"""
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"


def _load() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


_cfg = _load()


def gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set in environment or .env file")
    return key


def audio_cfg() -> dict:
    return _cfg.get("audio", {})


def network_cfg() -> dict:
    return _cfg.get("network", {})


def logging_cfg() -> dict:
    return _cfg.get("logging", {})


def gemini_model() -> str:
    return _cfg.get("gemini", {}).get("model", "gemini-3.1-flash-live-preview")


def save_audio_device(index: int) -> None:
    _cfg["audio"]["device_index"] = index
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(_cfg, f, default_flow_style=False, allow_unicode=True)


def save_auto_stop_timeout(minutes: int) -> None:
    if "audio" not in _cfg:
        _cfg["audio"] = {}
    _cfg["audio"]["auto_stop_timeout_min"] = minutes
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(_cfg, f, default_flow_style=False, allow_unicode=True)


def save_gemini_model(model: str) -> None:
    if "gemini" not in _cfg:
        _cfg["gemini"] = {}
    _cfg["gemini"]["model"] = model
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(_cfg, f, default_flow_style=False, allow_unicode=True)
