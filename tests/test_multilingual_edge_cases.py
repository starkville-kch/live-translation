"""Output selection, primary ordering, and config-only source acceptance."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.audio import AudioCapture
from app.config import save_translation_settings, translation_cfg
from app.gemini_session import GeminiSession
from app.server import app, manager


@pytest.fixture
def client(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with patch("app.server.logging_cfg", return_value={"log_dir": str(log_dir)}), \
         patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.stop())
        yield TestClient(app)
        asyncio.run(manager.stop())


def test_no_target_selected_start_rejected_clearly(client):
    result = client.post("/api/start", json={"targets": []})
    assert result.status_code == 400
    assert result.json()["ok"] is False
    assert result.json()["error"] == "no_targets_selected"


def test_expected_source_languages_remain_selectable_as_outputs(client):
    result = client.post("/api/start", json={"targets": ["ko", "en"]})
    assert result.status_code == 200
    assert manager.active_targets == ["ko", "en"]
    client.post("/api/stop")


def test_primary_target_removed_next_target_becomes_primary_deterministically(client):
    assert client.post("/api/start", json={"targets": ["en", "uk", "zh"]}).status_code == 200
    assert manager.primary_target == "en"
    client.post("/api/stop")
    assert client.post("/api/start", json={"targets": ["uk", "zh"]}).status_code == 200
    assert manager.primary_target == "uk"
    assert manager.active_targets == ["uk", "zh"]
    client.post("/api/stop")


def test_one_target_only_metadata_contract(client):
    assert client.post("/api/start", json={"targets": ["en"]}).status_code == 200
    status = client.get("/api/status").json()
    assert status["translation"]["active_targets"] == ["en"]
    assert status["translation"]["primary_target"] == "en"
    assert client.get("/api/languages").json()["active_targets"] == ["en"]
    client.post("/api/stop")


def test_primary_ordering_persists_after_restart(tmp_path):
    test_config = tmp_path / "config.yaml"
    test_config.write_text(
        "translation:\n"
        "  expected_source_languages: [ko, en]\n"
        "  supported_targets: [en, uk, zh]\n"
        "  default_active_targets: [en]\n", encoding="utf-8",
    )
    updated = save_translation_settings(
        supported_targets=["en", "uk", "zh"], default_active_targets=["zh", "en"], config_path=test_config,
    )
    assert updated["default_active_targets"] == ["zh", "en"]
    from app.config import _load
    loaded = _load(test_config)["translation"]
    assert loaded["default_active_targets"] == ["zh", "en"]
    assert loaded["expected_source_languages"] == ["ko", "en"]


def test_legacy_source_restrictions_are_ignored_during_output_changes(tmp_path, client):
    import yaml

    test_config = tmp_path / "config.yaml"
    test_config.write_text(
        "translation:\n"
        "  expected_source_languages: [ko, en]\n"
        "  supported_targets: [en, ko, zh, es]\n"
        "  default_active_targets: [en, zh]\n", encoding="utf-8",
    )
    with patch("app.config._CONFIG_PATH", test_config), \
         patch("app.config._cfg", yaml.safe_load(test_config.read_text(encoding="utf-8"))):
        result = client.post("/api/translation/targets", json={
            "expected_source_language": "es", "expected_source_languages": ["es"],
            "supported_targets": ["en", "ko", "zh", "es"], "targets": ["ko", "zh"],
        })
        assert result.status_code == 200
        assert result.json()["translation"]["default_active_targets"] == ["ko", "zh"]
        assert "expected_source_languages" not in translation_cfg()
        assert yaml.safe_load(test_config.read_text(encoding="utf-8"))["translation"]["expected_source_languages"] == ["ko", "en"]
