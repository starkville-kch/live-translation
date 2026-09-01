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
      model_mode: recommended  # 'recommended' | 'auto' | 'manual'
      preferred_model: null    # explicit manual selection
      fallback_model: "gemini-3.5-live-translate-preview"
      voice: "orus"

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
``gemini_cfg()``           — returns the ``gemini`` section dict
``gemini_model()``         — returns configured fallback or preferred model name
``save_church_identity()`` — persists church identity & hostname back to config.yaml
``save_audio_device()``    — persists a new device index back to config.yaml
``save_auto_stop_timeout()``— persists auto-stop timeout minutes back to config.yaml
``save_gemini_model_mode()``— persists model_mode and preferred_model back to config.yaml
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
        "default_ui_language": "ko",
    },

    "translation": {
        "expected_source_language": "ko",
        "supported_targets": ["en", "uk", "zh"],
        "default_active_targets": ["en"],
    },
    "audio": {
        "auto_stop_timeout_min": 10,
        "channels": 1,
        "chunk_ms": 100,
        "device_index": 1,
        "sample_rate": 16000,
    },
    "gemini": {
        "auto_drift_correction": False,
        "context_seed": True,
        "fallback_model": "gemini-3.5-live-translate-preview",
        "preferred_model": "gemini-3.5-live-translate-preview",
        "voice": "orus",
    },
    "logging": {
        "backup_count": 5,
        "log_dir": "logs",
        "max_bytes": 10485760,
    },
    "network": {
        "enable_tunnel": True,
        "host": "0.0.0.0",
        "hostname": "skc",
        "port": 8080,
        "public_url": "https://live.starkvillekoreanchurch.org",
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
        "default_ui_language": "ko",
    })



def audio_cfg() -> dict:
    return _cfg.get("audio", {})


def network_cfg() -> dict:
    return _cfg.get("network", {})


def logging_cfg() -> dict:
    return _cfg.get("logging", {})


def gemini_cfg() -> dict:
    g = _cfg.get("gemini", {})
    return {
        "auto_drift_correction": bool(g.get("auto_drift_correction", False)),
        "preferred_model": g.get("preferred_model", "gemini-3.5-live-translate-preview"),
        "fallback_model": g.get("fallback_model", "gemini-3.5-live-translate-preview"),
        "voice": g.get("voice", "orus"),
        "context_seed": g.get("context_seed", True),
    }


def gemini_model() -> str:
    g = gemini_cfg()
    return g.get("preferred_model") or g.get("fallback_model", "gemini-3.5-live-translate-preview")


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


def save_public_url(public_url: str, enable_tunnel: bool = True) -> None:
    """Save public_url and enable_tunnel to config.yaml atomically."""
    if "network" not in _cfg:
        _cfg["network"] = {}
    _cfg["network"]["public_url"] = public_url.strip()
    _cfg["network"]["enable_tunnel"] = bool(enable_tunnel)
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


def save_gemini_preferred_model(preferred_model: str) -> None:
    """Save preferred_model to config.yaml atomically."""
    if "gemini" not in _cfg:
        _cfg["gemini"] = {}
    _cfg["gemini"]["preferred_model"] = preferred_model.strip()
    _atomic_yaml_write(_CONFIG_PATH, _cfg)


def save_auto_drift_correction(enabled: bool) -> None:
    """Save auto_drift_correction setting to config.yaml atomically."""
    if "gemini" not in _cfg:
        _cfg["gemini"] = {}
    _cfg["gemini"]["auto_drift_correction"] = bool(enabled)
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


def translation_cfg() -> dict:
    """Return the translation configuration with backward-compatibility defaults."""
    raw = _cfg.get("translation")
    if not isinstance(raw, dict):
        return {
            "expected_source_language": "ko",
            "supported_targets": ["en", "uk", "zh"],
            "default_active_targets": ["en"],
        }
    src = str(raw.get("expected_source_language", "ko")).lower().strip() or "ko"
    supported = [str(t).lower().strip() for t in raw.get("supported_targets", ["en", "uk", "zh"]) if str(t).strip()]
    if not supported:
        supported = ["en"]
    active = [str(t).lower().strip() for t in raw.get("default_active_targets", ["en"]) if str(t).strip()]
    if not active:
        active = [supported[0]]
    # Ensure active is a subset of supported
    active = [t for t in active if t in supported] or [supported[0]]
    return {
        "expected_source_language": src,
        "supported_targets": list(dict.fromkeys(supported)),
        "default_active_targets": list(dict.fromkeys(active)),
    }


def validate_translation_settings(
    expected_source_language: str,
    supported_targets: list[str],
    default_active_targets: list[str],
) -> None:
    """Validate translation language configuration against the catalog."""
    from app.languages import is_valid_language_code

    src = (expected_source_language or "").lower().strip()
    if not src or not is_valid_language_code(src):
        raise ValueError(f"Invalid expected source language code: {expected_source_language}")

    if not supported_targets:
        raise ValueError("At least one supported target language must be specified.")

    clean_supported = []
    for t in supported_targets:
        code = str(t).lower().strip()
        if not code or not is_valid_language_code(code):
            raise ValueError(f"Invalid supported target language code: {t}")
        if code in clean_supported:
            raise ValueError(f"Duplicate supported target language code: {code}")
        if code == src:
            raise ValueError(f"Source language '{src}' cannot be in supported translation targets.")
        clean_supported.append(code)

    if not default_active_targets:
        raise ValueError("At least one default active target language must be specified.")

    clean_active = []
    for t in default_active_targets:
        code = str(t).lower().strip()
        if code not in clean_supported:
            raise ValueError(f"Default active target '{code}' is not in supported targets list {clean_supported}.")
        if code in clean_active:
            raise ValueError(f"Duplicate default active target code: {code}")
        clean_active.append(code)


def save_translation_settings(
    expected_source_language: str,
    supported_targets: list[str],
    default_active_targets: list[str],
    config_path: Path | None = None,
) -> dict:
    """Validate and atomically persist translation settings back to config.yaml."""
    validate_translation_settings(expected_source_language, supported_targets, default_active_targets)

    target_path = _ensure_config_file(config_path)
    with open(target_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    data["translation"] = {
        "expected_source_language": expected_source_language.lower().strip(),
        "supported_targets": [t.lower().strip() for t in supported_targets],
        "default_active_targets": [t.lower().strip() for t in default_active_targets],
    }

    _atomic_yaml_write(target_path, data)

    global _cfg
    _cfg = _load(target_path)
    return translation_cfg()

