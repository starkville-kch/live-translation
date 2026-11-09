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
      model: "gemini-3.5-live-translate-preview"
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
``gemini_model()``         — returns the configured fixed model name
``save_church_identity()`` — persists church identity & hostname back to config.yaml
``save_audio_device()``    — persists a new device index back to config.yaml
``save_auto_stop_timeout()``— persists auto-stop timeout minutes back to config.yaml
``update_gemini_api_key()``— atomically updates GEMINI_API_KEY in .env
``mask_api_key()``         — returns masked preview of API key
"""
import os
import sys
import tempfile
import time
import yaml
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from app.startup_timing import phase, timed

try:
    from ruamel.yaml import YAML
    _HAS_RUAMEL = True
except ImportError:
    _HAS_RUAMEL = False

# When running as a PyInstaller frozen exe, __file__ points inside the temp
# extraction folder. All user-editable files (config.yaml, .env) live next
# to the exe itself, so use sys.executable's resolved parent directory.
_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"
_TEMPLATE_CONFIG_PATH = _ROOT / "config" / "config.template.yaml"
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
        "supported_targets": ["ko", "en", "uk", "zh", "es"],
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
        "model": "gemini-3.5-live-translate-preview",
        "voice": "orus",
    },
    "logging": {
        "backup_count": 5,
        "log_dir": "logs",
        "max_bytes": 10485760,
    },
    "intranet": {
        "host": "0.0.0.0",
        "hostname": "skc",
        "port": 8080,
    },
    "internet": {
        "enable_tunnel": False,
        "public_url": "",
    },
}



import re


def _update_yaml_scalar(content: str, section: str, key: str, value: Any) -> str:
    """Update a single scalar key inside a top-level YAML section while preserving comments and alignment."""
    val_str = str(value)
    if isinstance(value, bool):
        val_str = "true" if value else "false"

    sec_pattern = re.compile(rf"(^{section}:.*?)(?=^[a-zA-Z0-9_-]+:|\Z)", re.MULTILINE | re.DOTALL)
    match = sec_pattern.search(content)
    if not match:
        tail = f"\n{section}:\n  {key}: {val_str}\n"
        return content.rstrip() + tail

    sec_text = match.group(1)
    key_pattern = re.compile(
        rf"^(?P<indent>[ \t]*{key}:[ \t]*)(?P<val>[^\r\n#]+?)(?P<spaces>[ \t]*)(?P<comment>#[^\r\n]*)?$",
        re.MULTILINE,
    )
    m = key_pattern.search(sec_text)
    if m:
        indent = m.group("indent")
        comment = m.group("comment") or ""
        spaces = m.group("spaces") or "  "
        if comment:
            old_len = len(indent) + len(m.group("val")) + len(spaces)
            new_prefix_len = len(indent) + len(val_str)
            if old_len > new_prefix_len:
                spaces = " " * (old_len - new_prefix_len)
            else:
                spaces = "  "
            new_line = f"{indent}{val_str}{spaces}{comment}"
        else:
            new_line = f"{indent}{val_str}"
        new_sec = sec_text[:m.start()] + new_line + sec_text[m.end():]
        return content[:match.start(1)] + new_sec + content[match.end(1):]
    else:
        new_sec = sec_text.rstrip() + f"\n  {key}: {val_str}\n"
        return content[:match.start(1)] + new_sec + content[match.end(1):]


def _update_yaml_list(content: str, section: str, key: str, items: list) -> str:
    """Update a sequence/list key inside a top-level YAML section while preserving section header comments."""
    lines = [f"  {key}:"]
    for item in items:
        lines.append(f"    - {item}")
    block = "\n".join(lines)

    sec_pattern = re.compile(rf"(^{section}:.*?)(?=^[a-zA-Z0-9_-]+:|\Z)", re.MULTILINE | re.DOTALL)
    match = sec_pattern.search(content)
    if not match:
        tail = f"\n{section}:\n{block}\n"
        return content.rstrip() + tail

    sec_text = match.group(1)
    key_pattern = re.compile(
        rf"^(?P<indent>[ \t]*){key}:(?:[ \t]*\[[^\r\n#]*\](?P<flow_comment>[ \t]*#[^\r\n]*)?|[ \t]*(?P<comment>#[^\r\n]*)?(?:\r?\n(?P<subindent>[ \t]+)-[^\r\n]*)*)",
        re.MULTILINE,
    )
    m = key_pattern.search(sec_text)
    if m:
        indent = m.group("indent")
        comment = (m.group("comment") or m.group("flow_comment") or "").strip()
        item_indent = indent + "  "
        hdr = f"{indent}{key}:"
        if comment:
            hdr += f" {comment}"
        out_lines = [hdr]
        for item in items:
            out_lines.append(f"{item_indent}- {item}")
        new_block = "\n".join(out_lines)
        new_sec = sec_text[:m.start()] + new_block + sec_text[m.end():]
        return content[:match.start(1)] + new_sec + content[match.end(1):]
    else:
        new_sec = sec_text.rstrip() + f"\n{block}\n"
        return content[:match.start(1)] + new_sec + content[match.end(1):]


def _atomic_yaml_write(path: Path, data: dict) -> None:
    """Atomically write YAML data using a temporary file in the same directory,
    preserving existing comments and section formatting whenever possible."""
    path.parent.mkdir(parents=True, exist_ok=True)

    base_text = ""
    if path.exists() and path.stat().st_size > 0:
        try:
            base_text = path.read_text(encoding="utf-8")
        except Exception:
            base_text = ""
    elif path.resolve() == _CONFIG_PATH.resolve() and _TEMPLATE_CONFIG_PATH.exists():
        try:
            base_text = _TEMPLATE_CONFIG_PATH.read_text(encoding="utf-8")
        except Exception:
            base_text = ""

    written_text = ""
    if base_text:
        try:
            text = base_text
            for sec, val in data.items():
                if isinstance(val, dict):
                    for k, v in val.items():
                        if isinstance(v, list):
                            text = _update_yaml_list(text, sec, k, v)
                        else:
                            text = _update_yaml_scalar(text, sec, k, v)
            written_text = text
        except Exception:
            written_text = ""
    if base_text:
        try:
            text = base_text
            for sec, val in data.items():
                if isinstance(val, dict):
                    for k, v in val.items():
                        if isinstance(v, list):
                            text = _update_yaml_list(text, sec, k, v)
                        else:
                            text = _update_yaml_scalar(text, sec, k, v)
            written_text = text
        except Exception:
            written_text = ""

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    )
    try:
        if written_text:
            temp_file.write(written_text)
        else:
            yaml.dump(data, temp_file, default_flow_style=False, allow_unicode=True)

        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()

        for attempt in range(5):
            try:
                os.replace(temp_file.name, str(path))
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
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

    if _TEMPLATE_CONFIG_PATH.exists():
        try:
            import shutil
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_TEMPLATE_CONFIG_PATH, config_path)
            return config_path
        except Exception:
            pass

    _atomic_yaml_write(config_path, DEFAULT_CONFIG)
    return config_path


def _load(path: Path | None = None) -> dict:
    with timed("config_load"):
        config_path = _ensure_config_file(path)
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    if not data:
        _atomic_yaml_write(config_path, DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    return data


_cfg = _load()
phase("config_loaded")


def get_app_root() -> Path:
    """Return the base directory for user files (next to EXE in frozen mode, or project root)."""
    return _ROOT


def gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set in environment or .env file")
    return key


def church_cfg() -> dict:
    c = _cfg.get("church", {})
    return {
        "name": c.get("name", "Starkville Korean Church"),
        "short_name": c.get("short_name", "SKC"),
        "logo": c.get("logo", "branding/church-logo.png"),
        "default_ui_language": c.get("default_ui_language", "ko"),
    }



def audio_cfg() -> dict:
    return _cfg.get("audio", {})


def network_cfg() -> dict:
    """Return unified network configuration supporting either:
    1. Split sections: 'intranet' (host, port, hostname) and 'internet' (enable_tunnel, public_url)
    2. Legacy unified section: 'network' (host, port, hostname, enable_tunnel, public_url)
    """
    net = dict(_cfg.get("network", {}))
    if "intranet" in _cfg:
        net.update(_cfg.get("intranet", {}))
    if "internet" in _cfg:
        net.update(_cfg.get("internet", {}))
    return net


def logging_cfg() -> dict:
    return _cfg.get("logging", {})


def gemini_cfg() -> dict:
    g = _cfg.get("gemini", {})
    return {"model": g.get("model", "gemini-3.5-live-translate-preview"), "voice": g.get("voice", "orus")}


def gemini_model() -> str:
    return gemini_cfg()["model"]


def save_church_identity(
    name: str,
    short_name: str,
    hostname: str,
    logo_rel_path: str = "",
    default_ui_language: str = "",
) -> None:
    """Save church identity, hostname, and default UI language to config.yaml atomically."""
    if "church" not in _cfg:
        _cfg["church"] = {}
    _cfg["church"]["name"] = name.strip()
    _cfg["church"]["short_name"] = short_name.strip()
    if logo_rel_path:
        _cfg["church"]["logo"] = logo_rel_path.strip()

    if default_ui_language:
        _cfg["church"]["default_ui_language"] = str(default_ui_language).strip().lower()

    if "intranet" in _cfg:
        _cfg["intranet"]["hostname"] = hostname.strip()
    elif "network" in _cfg:
        _cfg["network"]["hostname"] = hostname.strip()
    else:
        _cfg["intranet"] = {"hostname": hostname.strip()}

    _atomic_yaml_write(_CONFIG_PATH, _cfg)


def save_operator_ui_language(lang: str) -> None:
    """Save operator's default UI language to config.yaml atomically."""
    if "church" not in _cfg:
        _cfg["church"] = {}
    clean_lang = "en" if str(lang).strip().lower() == "en" else "ko"
    _cfg["church"]["default_ui_language"] = clean_lang
    _atomic_yaml_write(_CONFIG_PATH, _cfg)


def save_public_url(public_url: str, enable_tunnel: bool = True) -> None:
    """Save public_url and enable_tunnel to config.yaml atomically."""
    if "internet" in _cfg:
        _cfg["internet"]["public_url"] = public_url.strip()
        _cfg["internet"]["enable_tunnel"] = bool(enable_tunnel)
    elif "network" in _cfg:
        _cfg["network"]["public_url"] = public_url.strip()
        _cfg["network"]["enable_tunnel"] = bool(enable_tunnel)
    else:
        _cfg["internet"] = {"public_url": public_url.strip(), "enable_tunnel": bool(enable_tunnel)}
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






def mask_api_key(key: str) -> str:
    """Mask an API key for safe display (e.g. AIzaSy••••••••4xQ9)."""
    clean_key = (key or "").strip()
    if not clean_key:
        return "Not configured"
    if len(clean_key) <= 10:
        return "••••••••"
    return f"{clean_key[:6]}••••••••{clean_key[-4:]}"


def update_env_var(var_name: str, new_value: str, env_path: Path | None = None) -> None:
    """Atomically update or append an environment variable in .env while preserving existing lines."""
    target_path = env_path or _ENV_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    clean_val = new_value.strip()

    lines = []
    key_found = False
    if target_path.exists():
        with open(target_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    prefix_check = f"{var_name}="
    export_check = f"export {var_name}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix_check) or stripped.startswith(export_check):
            prefix = "export " if stripped.startswith("export ") else ""
            new_lines.append(f"{prefix}{var_name}={clean_val}\n")
            key_found = True
        else:
            new_lines.append(line)

    if not key_found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{var_name}={clean_val}\n")

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
    os.environ[var_name] = clean_val


def update_gemini_api_key(new_key: str, env_path: Path | None = None) -> None:
    """Atomically update or append GEMINI_API_KEY in .env."""
    update_env_var("GEMINI_API_KEY", new_key, env_path=env_path)


def update_tunnel_token(new_token: str, env_path: Path | None = None) -> None:
    """Atomically update or append CLOUDFLARE_TUNNEL_TOKEN in .env."""
    update_env_var("CLOUDFLARE_TUNNEL_TOKEN", new_token, env_path=env_path)


def update_operator_password(new_password: str, env_path: Path | None = None) -> None:
    """Atomically update or append SKC_OPERATOR_PASSWORD in .env."""
    update_env_var("SKC_OPERATOR_PASSWORD", new_password, env_path=env_path)


def translation_cfg() -> dict:
    """Output targets; legacy source-language restrictions are ignored."""
    raw = _cfg.get("translation", {})
    supported = list(dict.fromkeys(str(code).strip().lower() for code in raw.get("supported_targets", ["en", "ko", "zh"])))
    for code in ("ko", "en"):
        if code not in supported:
            supported.insert(0, code)
    active = [code for code in raw.get("default_active_targets", ["en"]) if code in supported] or ["en"]
    return {"supported_targets": supported, "default_active_targets": list(dict.fromkeys(active))}


def validate_translation_settings(supported_targets: list[str], default_active_targets: list[str]) -> None:
    from app.languages import is_valid_language_code
    if not supported_targets or not default_active_targets:
        raise ValueError("At least one supported and active target is required")
    for values in (supported_targets, default_active_targets):
        if len(values) != len(set(values)) or any(not is_valid_language_code(code) for code in values):
            raise ValueError("Invalid or duplicate target language")
    if any(code not in supported_targets for code in default_active_targets):
        raise ValueError("Active targets must be in supported_targets")


def save_translation_settings(supported_targets: list[str], default_active_targets: list[str], config_path: Path | None = None) -> dict:
    """Save output choices without changing config-only source acceptance."""
    validate_translation_settings(supported_targets, default_active_targets)
    target_path = _ensure_config_file(config_path)
    data = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}
    translation = data.setdefault("translation", {})
    translation["supported_targets"] = list(supported_targets)
    translation["default_active_targets"] = list(default_active_targets)
    _atomic_yaml_write(target_path, data)
    if config_path is None or target_path.resolve() == _CONFIG_PATH.resolve():
        global _cfg
        _cfg = _load(target_path)
        return translation_cfg()
    return dict(translation)

