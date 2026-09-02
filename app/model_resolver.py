"""
app/model_resolver.py — Centralized Gemini Live Translation Model Resolver
==========================================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Discovers, filters, tests, selects, and locks Gemini Live Translation models.

Terminology:
- Discovered: Returned by Gemini Models API (models.list)
- Candidate: Appears to be a Live Translate model via name/displayName/description
- Compatible: Required Live Translate handshake succeeds
- Verified: Real translation session produced audio & caption output
- Last Known Good (LKG): Most recently verified model (in var/runtime/model_state.json)
- Fallback: Administrator-configured baseline (in config.yaml)
- Locked: Model fixed for the active church service session
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import types

from app.config import (
    gemini_api_key,
    gemini_cfg,
    get_app_root,
    save_gemini_preferred_model,
)
from app.logger import server_log, session_log

BANNED_MODELS = {
    "gemini-3.1-flash-live-preview",  # crashes on continuous audio (banned)
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-2.5-pro",
}


def _get_runtime_state_path() -> Path:
    app_root = get_app_root()
    return app_root / "var" / "runtime" / "model_state.json"


def load_runtime_state() -> dict:
    state_path = _get_runtime_state_path()
    fallback = gemini_cfg().get("fallback_model", "gemini-3.5-live-translate-preview")
    default_state = {
        "last_known_good_model": fallback,
        "last_verified_at": None,
        "seen_models": [fallback],
        "dismissed_alerts": [],
    }
    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**default_state, **data}
        except Exception as e:
            server_log.warning("Could not read model_state.json: %s", e)
    return default_state


def atomic_save_runtime_state(state: dict) -> None:
    state_path = _get_runtime_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(state_path.parent),
        delete=False,
        suffix=".tmp",
    )
    try:
        json.dump(state, temp_file, indent=2)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(temp_file.name, str(state_path))
    except Exception as e:
        if os.path.exists(temp_file.name):
            try:
                os.remove(temp_file.name)
            except OSError:
                pass
        server_log.error("Failed to save model_state.json: %s", e)


def classify_candidate(name: str, display_name: str = "", description: str = "") -> tuple[bool, str]:
    """Classify if a model is a genuine Live Translate candidate."""
    clean_name = name.removeprefix("models/").strip().lower()
    if clean_name in BANNED_MODELS:
        return False, "Excluded: general conversational / non-translation model"

    desc = (description or "").lower()
    disp = (display_name or "").lower()

    if "live" in clean_name and "translate" in clean_name:
        return True, "Name matches Live Translate pattern"

    if "live" in clean_name and ("real-time translation" in desc or "speech translation" in desc or "translation" in desc):
        return True, "Live model with translation capability in description"

    if "live" in clean_name and ("real-time translation" in disp or "translation" in disp):
        return True, "Live model with translation in display name"

    return False, "Does not match Live Translate criteria"


def parse_model_version(name: str) -> tuple[tuple[int, ...], bool]:
    """Extract version numbers and preview status for sorting (4.1 > 4.0 > 3.5, stable > preview)."""
    clean_name = name.removeprefix("models/").lower()
    # Extract version digits (e.g. "3.5" or "4.0")
    prefix_part = clean_name.split("live")[0] if "live" in clean_name else clean_name
    nums = tuple(int(x) for x in re.findall(r"\d+", prefix_part))
    if not nums:
        nums = (0,)
    is_preview = "preview" in clean_name or "exp" in clean_name or "experimental" in clean_name
    return nums, not is_preview


def sort_models_by_version(models: list[str]) -> list[str]:
    """Sort models descending by version: newest stable > newest preview > older."""
    return sorted(models, key=parse_model_version, reverse=True)


async def verify_model_compatibility(
    model_name: str,
    client: genai.Client | None = None,
    api_key: str | None = None,
    voice_name: str = "orus",
) -> tuple[bool, dict[str, Any], str]:
    """Perform a lightweight Live Translate handshake to verify model compatibility."""
    key = api_key or gemini_api_key()
    c = client or genai.Client(api_key=key)

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        translation_config=types.TranslationConfig(
            target_language_code="en",
            echo_target_language=True,
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name),
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    try:
        # Fast handshake timeout (5.0s)
        async with asyncio.timeout(5.0):
            async with c.aio.live.connect(model=model_name, config=config) as _:
                return (
                    True,
                    {
                        "live_connection": True,
                        "translation_config": True,
                        "audio_output": True,
                        "transcription": True,
                    },
                    "Handshake successful — model is Compatible",
                )
    except asyncio.TimeoutError:
        return False, {}, "Connection timed out during handshake"
    except Exception as e:
        err_msg = str(e)
        if key in err_msg:
            err_msg = err_msg.replace(key, "••••••••")
        return False, {}, f"Compatibility handshake failed: {err_msg}"


class ModelResolver:
    """Central manager for Gemini Live Translation model discovery, selection, and locking."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._locked_session_model: str | None = None
        self._runtime_state = load_runtime_state()
        self._available_candidates: list[str] = [self.fallback_model]
        if self.last_known_good_model not in self._available_candidates:
            self._available_candidates.append(self.last_known_good_model)

        self._active_model: str = self.preferred_model
        self._is_fallback_active: bool = False
        self._fallback_reason: str = ""
        self._discovery_task: asyncio.Task | None = None
        self._discovery_status: str = "Ready"
        self._new_model_alert: dict | None = None

    @property
    def preferred_model(self) -> str:
        return gemini_cfg().get("preferred_model") or self.fallback_model

    @property
    def fallback_model(self) -> str:
        return gemini_cfg().get("fallback_model", "gemini-3.5-live-translate-preview")

    @property
    def last_known_good_model(self) -> str:
        return self._runtime_state.get("last_known_good_model") or self.fallback_model

    @property
    def locked_model(self) -> str | None:
        return self._locked_session_model

    @property
    def active_model(self) -> str:
        if self._locked_session_model:
            return self._locked_session_model
        return self.preferred_model

    @property
    def available_models(self) -> list[str]:
        models = list(self._available_candidates)
        if self.preferred_model and self.preferred_model not in models:
            models.append(self.preferred_model)
        if self.last_known_good_model not in models:
            models.append(self.last_known_good_model)
        if self.fallback_model not in models:
            models.append(self.fallback_model)
        return models

    def set_preferred_model(self, preferred_model: str) -> None:
        """Update preferred_model in config.yaml."""
        if self._locked_session_model:
            raise RuntimeError("Cannot change model while a translation session is running.")
        save_gemini_preferred_model(preferred_model)
        server_log.info("Preferred Gemini model updated: %s", preferred_model)

    def get_candidate_sequence(self) -> list[str]:
        """Return the priority order of model candidates to try: preferred -> LKG -> fallback."""
        candidates = [self.preferred_model, self.last_known_good_model, self.fallback_model]
        seen = set()
        deduped = []
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                deduped.append(c)
        return deduped

    def lock_session(self, model: str, is_fallback: bool = False, reason: str = "") -> None:
        """Lock the model for the active service session."""
        self._locked_session_model = model
        self._is_fallback_active = is_fallback
        self._fallback_reason = reason
        server_log.info("Session model LOCKED: %s (fallback=%s, reason='%s')", model, is_fallback, reason)

    def unlock_session(self) -> None:
        """Unlock the model on service stop."""
        if self._locked_session_model is None:
            return
        prev = self._locked_session_model
        self._locked_session_model = None
        server_log.info("Session model UNLOCKED (was: %s)", prev)

    def record_verified_success(self, model: str) -> None:
        """Record a model as Verified (Last Known Good) after receiving real translated output."""
        now_iso = datetime.now(timezone.utc).isoformat()
        self._runtime_state["last_known_good_model"] = model
        self._runtime_state["last_verified_at"] = now_iso
        if model not in self._runtime_state["seen_models"]:
            self._runtime_state["seen_models"].append(model)
        atomic_save_runtime_state(self._runtime_state)
        session_log.info("Model marked VERIFIED and recorded as Last Known Good: %s", model)

    def dismiss_alert(self, model_name: str) -> None:
        """Dismiss a new model alert."""
        if model_name not in self._runtime_state.get("dismissed_alerts", []):
            if "dismissed_alerts" not in self._runtime_state:
                self._runtime_state["dismissed_alerts"] = []
            self._runtime_state["dismissed_alerts"].append(model_name)
            atomic_save_runtime_state(self._runtime_state)
        self._new_model_alert = None

    def start_background_discovery(self) -> None:
        """Launch non-blocking model discovery in background."""
        try:
            loop = asyncio.get_running_loop()
            if self._discovery_task and not self._discovery_task.done():
                return
            self._discovery_task = loop.create_task(self._run_discovery())
        except RuntimeError:
            pass  # Event loop not running yet

    async def _run_discovery(self) -> None:
        try:
            self._discovery_status = "Discovering models..."
            key = gemini_api_key()
            client = genai.Client(api_key=key)

            # Paginated model discovery
            candidates = []
            seen_in_state = set(self._runtime_state.get("seen_models", []))
            dismissed = set(self._runtime_state.get("dismissed_alerts", []))
            new_candidates = []

            # Use SDK iterator (handles pagination automatically)
            for model_obj in client.models.list():
                raw_name = getattr(model_obj, "name", "")
                disp_name = getattr(model_obj, "display_name", "")
                desc = getattr(model_obj, "description", "")
                is_candidate, _ = classify_candidate(raw_name, disp_name, desc)
                if is_candidate:
                    m_clean = raw_name.removeprefix("models/").strip()
                    candidates.append(m_clean)
                    if m_clean not in seen_in_state:
                        new_candidates.append(m_clean)
                        seen_in_state.add(m_clean)

            if candidates:
                sorted_candidates = sort_models_by_version(candidates)
                self._available_candidates = sorted_candidates

                # Check if there's a new model alert to surface
                for nc in new_candidates:
                    if nc not in dismissed and nc != self.fallback_model:
                        self._new_model_alert = {
                            "model": nc,
                            "status": "Compatible — not yet verified",
                            "message": f"New compatible translation model found: {nc}",
                        }
                        break

                self._runtime_state["seen_models"] = list(seen_in_state)
                atomic_save_runtime_state(self._runtime_state)

            self._discovery_status = "Ready"
            server_log.info("Model discovery completed. Found %d candidates: %s", len(self._available_candidates), self._available_candidates)

        except Exception as e:
            self._discovery_status = f"Discovery unavailable ({e})"
            server_log.warning("Background model discovery failed: %s", e)

    def get_state(self) -> dict[str, Any]:
        """Return state snapshot for API / operator UI."""
        return {
            "configured_model": self.preferred_model,
            "preferred_model": self.preferred_model,
            "active_model": self.active_model,
            "resolved_model": self.active_model,
            "last_known_good_model": self.last_known_good_model,
            "fallback_model": self.fallback_model,
            "is_locked": self._locked_session_model is not None,
            "is_fallback": self._is_fallback_active,
            "fallback_reason": self._fallback_reason,
            "available_models": self.available_models,
            "seen_models": self._runtime_state.get("seen_models", []),
            "discovery_status": self._discovery_status,
            "new_model_alert": self._new_model_alert,
        }


# Global singleton instance
model_resolver = ModelResolver()
