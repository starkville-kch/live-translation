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
import sys
import yaml
from pathlib import Path
from dotenv import load_dotenv

# When running as a PyInstaller frozen exe, __file__ points inside the temp
# extraction folder. All user-editable files (config.yaml, .env) live next
# to the exe itself, so use sys.executable's directory in that case.
_ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"

load_dotenv(_ROOT / ".env")


def _print_error_box(title: str, lines: list[str]) -> None:
    w = 72
    print()
    print("╔" + "═" * w + "╗")
    print("║" + title.center(w).upper() + "║")
    print("╠" + "═" * w + "╣")
    print("║" + " " * w + "║")
    for line in lines:
        print("║  " + line.ljust(w - 4) + "  ║")
    print("║" + " " * w + "║")
    print("╚" + "═" * w + "╝")
    print()


DEFAULT_CONFIG = {
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
        "enable_tunnel": True,
        "host": "0.0.0.0",
        "hostname": "skc-live.local",
        "port": 8080,
    },
}


def _load() -> dict:
    if not _CONFIG_PATH.exists():
        try:
            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
            print(f"[INFO] Created default 'config.yaml' at:\n       {_CONFIG_PATH}\n")
            return DEFAULT_CONFIG
        except Exception as err:
            exe_name = Path(sys.executable).name if getattr(sys, "frozen", False) else "main.py"
            lines = [
                "Could not create default 'config.yaml':",
                f"  {err}",
                "",
                "Expected location:",
                f"  {_CONFIG_PATH}",
                "",
                "HOW TO FIX THIS:",
                f"Please ensure '{_ROOT}' has write permissions or place",
                "a 'config.yaml' file there manually.",
            ]
            _print_error_box("ERROR: Missing config.yaml File", lines)
            if getattr(sys, "frozen", False):
                try:
                    input("Press Enter to exit...")
                except (EOFError, KeyboardInterrupt):
                    pass
            sys.exit(1)

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else DEFAULT_CONFIG
    except Exception as err:
        lines = [
            "Failed to parse 'config.yaml':",
            f"  {err}",
            "",
            "Please check the YAML formatting in your config.yaml file.",
        ]
        _print_error_box("ERROR: Invalid config.yaml File", lines)
        if getattr(sys, "frozen", False):
            try:
                input("Press Enter to exit...")
            except (EOFError, KeyboardInterrupt):
                pass
        sys.exit(1)


_cfg = _load()


DEFAULT_ENV_TEMPLATE = """# Starkville Korean Church (PCA) - Live Translation System
# Please paste your Google Gemini API Key below:
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
"""


def gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or key.strip() == "YOUR_GEMINI_API_KEY_HERE":
        env_path = _ROOT / ".env"
        created_note = ""
        if not env_path.exists():
            try:
                env_path.write_text(DEFAULT_ENV_TEMPLATE, encoding="utf-8")
                created_note = "Created instructional '.env' file"
            except Exception:
                pass

        lines = [
            "GEMINI_API_KEY is not set (or is using the placeholder).",
            "",
            f"Location: {env_path}",
            f"Note:     {created_note}" if created_note else f"Found:    {env_path}",
            "",
            "HOW TO FIX THIS:",
            "1. Open the '.env' file in a text editor.",
            "2. Replace 'YOUR_GEMINI_API_KEY_HERE' with your real key:",
            "   GEMINI_API_KEY=AIzaSyYourActualKeyHere",
        ]
        _print_error_box("ERROR: Missing GEMINI_API_KEY", lines)
        if getattr(sys, "frozen", False):
            try:
                input("Press Enter to exit...")
            except (EOFError, KeyboardInterrupt):
                pass
        sys.exit(1)
    return key


def audio_cfg() -> dict:
    return _cfg.get("audio", {})


def network_cfg() -> dict:
    cfg = dict(_cfg.get("network", {}))
    env_public_url = os.environ.get("CLOUDFLARE_PUBLIC_URL", "").strip()
    if env_public_url:
        cfg["public_url"] = env_public_url
    return cfg



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
