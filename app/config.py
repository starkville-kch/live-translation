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


def save_gemini_model(model: str) -> None:
    if "gemini" not in _cfg:
        _cfg["gemini"] = {}
    _cfg["gemini"]["model"] = model
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(_cfg, f, default_flow_style=False, allow_unicode=True)
