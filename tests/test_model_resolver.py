"""
tests/test_model_resolver.py — Unit & Integration Tests for Gemini Live Translation Model Resolver
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import DEFAULT_CONFIG, gemini_cfg, save_gemini_preferred_model
from app.model_resolver import (
    ModelResolver,
    classify_candidate,
    load_runtime_state,
    parse_model_version,
    sort_models_by_version,
    verify_model_compatibility,
)


def test_classify_candidate():
    # 1. High confidence by name
    ok, reason = classify_candidate("gemini-3.5-live-translate-preview")
    assert ok is True
    assert "Live Translate" in reason

    ok, reason = classify_candidate("models/gemini-4.0-live-translate")
    assert ok is True

    # 2. Candidate by description
    ok, reason = classify_candidate("gemini-live-next", description="Real-time speech translation model")
    assert ok is True

    # 3. Candidate by display name
    ok, reason = classify_candidate("gemini-live-model", display_name="Gemini Live Real-time Translation")
    assert ok is True

    # 4. General conversational models excluded
    ok, _ = classify_candidate("gemini-2.5-flash")
    assert ok is False

    ok, _ = classify_candidate("gemini-1.5-pro")
    assert ok is False

    # 5. Specific banned dialogue model
    ok, _ = classify_candidate("gemini-3.1-flash-live-preview")
    assert ok is False


def test_version_sorting():
    models = [
        "gemini-3.5-live-translate-preview",
        "gemini-4.1-live-translate-preview",
        "gemini-4.0-live-translate-preview",
        "gemini-4.0-live-translate",
        "gemini-3.5-live-translate",
    ]
    sorted_m = sort_models_by_version(models)
    # Expected order:
    # 4.1 preview > 4.0 stable > 4.0 preview > 3.5 stable > 3.5 preview
    assert sorted_m[0] == "gemini-4.1-live-translate-preview"
    assert sorted_m[1] == "gemini-4.0-live-translate"
    assert sorted_m[2] == "gemini-4.0-live-translate-preview"
    assert sorted_m[3] == "gemini-3.5-live-translate"
    assert sorted_m[4] == "gemini-3.5-live-translate-preview"

    # Explicit cross-version check: 4.0 preview > 3.5 stable
    assert sort_models_by_version([
        "gemini-3.5-live-translate",
        "gemini-4.0-live-translate-preview",
    ]) == [
        "gemini-4.0-live-translate-preview",
        "gemini-3.5-live-translate",
    ]

    # Explicit same-version check: 4.0 stable > 4.0 preview
    assert sort_models_by_version([
        "gemini-4.0-live-translate-preview",
        "gemini-4.0-live-translate",
    ]) == [
        "gemini-4.0-live-translate",
        "gemini-4.0-live-translate-preview",
    ]


def test_preferred_model_selected(tmp_path, monkeypatch):
    state_file = tmp_path / "var" / "runtime" / "model_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "last_known_good_model": "gemini-3.5-live-translate-preview",
        "last_verified_at": "2026-08-23T22:00:00Z",
        "seen_models": ["gemini-3.5-live-translate-preview"],
        "dismissed_alerts": [],
    }), encoding="utf-8")
    monkeypatch.setattr("app.model_resolver._get_runtime_state_path", lambda: state_file)

    saved_preferred = "gemini-4.0-live-translate"
    monkeypatch.setattr("app.model_resolver.gemini_cfg", lambda: {
        "preferred_model": saved_preferred,
        "fallback_model": "gemini-3.5-live-translate-preview",
        "voice": "orus",
    })

    resolver = ModelResolver()
    seq = resolver.get_candidate_sequence()
    assert seq[0] == "gemini-4.0-live-translate"
    assert resolver.active_model == "gemini-4.0-live-translate"


def test_preferred_failure_uses_lkg(tmp_path, monkeypatch):
    state_file = tmp_path / "var" / "runtime" / "model_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "last_known_good_model": "gemini-3.5-live-translate-verified",
        "last_verified_at": "2026-08-23T22:00:00Z",
        "seen_models": ["gemini-3.5-live-translate-verified"],
        "dismissed_alerts": [],
    }), encoding="utf-8")
    monkeypatch.setattr("app.model_resolver._get_runtime_state_path", lambda: state_file)

    monkeypatch.setattr("app.model_resolver.gemini_cfg", lambda: {
        "preferred_model": "gemini-4.0-failing-candidate",
        "fallback_model": "gemini-3.5-live-translate-preview",
        "voice": "orus",
    })

    resolver = ModelResolver()
    seq = resolver.get_candidate_sequence()
    assert seq == [
        "gemini-4.0-failing-candidate",
        "gemini-3.5-live-translate-verified",
        "gemini-3.5-live-translate-preview",
    ]
    # If preferred fails on connect, fallback candidate #2 is LKG
    assert seq[1] == "gemini-3.5-live-translate-verified"


def test_lkg_failure_uses_fallback(tmp_path, monkeypatch):
    state_file = tmp_path / "var" / "runtime" / "model_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "last_known_good_model": "gemini-old-broken-lkg",
        "seen_models": [],
    }), encoding="utf-8")
    monkeypatch.setattr("app.model_resolver._get_runtime_state_path", lambda: state_file)

    monkeypatch.setattr("app.model_resolver.gemini_cfg", lambda: {
        "preferred_model": "gemini-broken-pref",
        "fallback_model": "gemini-3.5-live-translate-preview",
        "voice": "orus",
    })

    resolver = ModelResolver()
    seq = resolver.get_candidate_sequence()
    assert seq[2] == "gemini-3.5-live-translate-preview"


def test_session_locking(tmp_path, monkeypatch):
    state_file = tmp_path / "var" / "runtime" / "model_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_known_good_model": "gemini-3.5-live-translate-preview"}), encoding="utf-8")
    monkeypatch.setattr("app.model_resolver._get_runtime_state_path", lambda: state_file)

    resolver = ModelResolver()
    resolver.lock_session("gemini-4.0-live-translate", is_fallback=True, reason="Preferred failed")
    assert resolver.locked_model == "gemini-4.0-live-translate"
    assert resolver.active_model == "gemini-4.0-live-translate"


def test_model_change_rejected_while_locked(tmp_path, monkeypatch):
    state_file = tmp_path / "var" / "runtime" / "model_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_known_good_model": "gemini-3.5-live-translate-preview"}), encoding="utf-8")
    monkeypatch.setattr("app.model_resolver._get_runtime_state_path", lambda: state_file)

    resolver = ModelResolver()
    resolver.lock_session("gemini-3.5-live-translate-preview")

    with pytest.raises(RuntimeError, match="Cannot change model while a translation session is running"):
        resolver.set_preferred_model("gemini-4.0-live-translate")


def test_stop_unlocks_model_selection(tmp_path, monkeypatch):
    state_file = tmp_path / "var" / "runtime" / "model_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_known_good_model": "gemini-3.5-live-translate-preview"}), encoding="utf-8")
    monkeypatch.setattr("app.model_resolver._get_runtime_state_path", lambda: state_file)

    resolver = ModelResolver()
    resolver.lock_session("gemini-4.0-live-translate")
    assert resolver.locked_model is not None

    resolver.unlock_session()
    assert resolver.locked_model is None


def test_record_verified_success_persists_runtime_only(tmp_path, monkeypatch):
    state_file = tmp_path / "var" / "runtime" / "model_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_known_good_model": "gemini-3.5-live-translate-preview"}), encoding="utf-8")
    monkeypatch.setattr("app.model_resolver._get_runtime_state_path", lambda: state_file)

    config_written = False

    def mock_save_pref(pref):
        nonlocal config_written
        config_written = True

    monkeypatch.setattr("app.model_resolver.save_gemini_preferred_model", mock_save_pref)

    resolver = ModelResolver()
    resolver.record_verified_success("gemini-4.0-live-translate")

    with open(state_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["last_known_good_model"] == "gemini-4.0-live-translate"
    assert data["last_verified_at"] is not None
    assert "gemini-4.0-live-translate" in data["seen_models"]
    assert config_written is False


def test_mocked_compatibility_handshake_success():
    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    class AsyncContextManager:
        async def __aenter__(self):
            return mock_live_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_client.aio.live.connect.return_value = AsyncContextManager()

    async def _run():
        return await verify_model_compatibility(
            "gemini-3.5-live-translate-preview",
            client=mock_client,
            api_key="AIzaSyTestMockKey1234567890",
        )

    ok, caps, msg = asyncio.run(_run())
    assert ok is True
    assert caps.get("live_connection") is True
    assert caps.get("translation_config") is True
    assert "Handshake successful" in msg


def test_mocked_compatibility_handshake_sanitizes_error():
    mock_client = MagicMock()
    fake_key = "AIzaSySecretApiKey12345"

    class FailingContextManager:
        async def __aenter__(self):
            raise ValueError(f"Invalid auth token or parameter for key {fake_key}")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_client.aio.live.connect.return_value = FailingContextManager()

    async def _run():
        return await verify_model_compatibility(
            "gemini-invalid-model",
            client=mock_client,
            api_key=fake_key,
        )

    ok, caps, msg = asyncio.run(_run())
    assert ok is False
    assert fake_key not in msg
    assert "••••••••" in msg


def test_models_api_endpoints():
    from fastapi.testclient import TestClient
    from app.server import app

    client = TestClient(app)

    # 1. GET /api/models returns state with available models
    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "available_models" in data
    assert "preferred_model" in data
    assert "fallback_model" in data

    # 2. POST /api/models/select updates preferred model
    resp_select = client.post("/api/models/select", json={"model": "gemini-3.5-live-translate-preview"})
    assert resp_select.status_code == 200
    assert resp_select.json().get("ok") is True

    # 3. POST /api/models/dismiss-alert dismisses notification
    resp_dismiss = client.post("/api/models/dismiss-alert", json={"model": "gemini-4.0-test"})
    assert resp_dismiss.status_code == 200
    assert resp_dismiss.json().get("ok") is True
