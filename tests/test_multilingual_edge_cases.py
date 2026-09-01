"""
tests/test_multilingual_edge_cases.py — Unit Tests for Multilingual Edge Cases
=============================================================================
Tests:
1. No target selected → Start rejected clearly (HTTP 400)
2. Spoken language changed to an already-selected target → rejected cleanly
3. Primary target removed → next selected target becomes Primary deterministically
4. One target only → attendee and monitor selectors contract
5. Primary ordering persists across config save and restart
"""
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
    """Edge Case 1: Start request with empty targets list must be rejected clearly with 400."""
    res = client.post("/api/start", json={"targets": [], "expected_source_language": "ko"})
    assert res.status_code == 400
    data = res.json()
    assert data["ok"] is False
    assert data["error"] == "no_targets_selected"
    assert "At least one target language must be selected" in data["message"]


def test_spoken_language_in_targets_rejected_clearly(client):
    """Edge Case 2: If expected source language is in active targets, start must reject with 400."""
    res = client.post("/api/start", json={"targets": ["ko", "en"], "expected_source_language": "ko"})
    assert res.status_code == 400
    data = res.json()
    assert data["ok"] is False
    assert "cannot be in active target languages" in data["message"]


def test_primary_target_removed_next_target_becomes_primary_deterministically(client):
    """Edge Case 3: When primary target is removed, the next selected target deterministically becomes Primary."""
    # Start with English (Primary), Ukrainian, Chinese
    res = client.post("/api/start", json={"targets": ["en", "uk", "zh"], "expected_source_language": "ko"})
    assert res.status_code == 200
    assert manager.primary_target == "en"

    client.post("/api/stop")

    # Remove 'en'; next target 'uk' must deterministically become Primary
    res2 = client.post("/api/start", json={"targets": ["uk", "zh"], "expected_source_language": "ko"})
    assert res2.status_code == 200
    assert manager.primary_target == "uk"
    assert manager.active_targets == ["uk", "zh"]

    client.post("/api/stop")


def test_one_target_only_metadata_contract(client):
    """Edge Case 4: Single target returns active_targets of length 1, signalling UI to hide selectors."""
    # When single target is configured
    res = client.post("/api/start", json={"targets": ["en"], "expected_source_language": "ko"})
    assert res.status_code == 200

    res_status = client.get("/api/status")
    assert res_status.status_code == 200
    st = res_status.json()
    assert len(st["translation"]["active_targets"]) == 1
    assert st["translation"]["active_targets"] == ["en"]
    assert st["translation"]["primary_target"] == "en"

    res_lang = client.get("/api/languages")
    assert res_lang.status_code == 200
    lang_data = res_lang.json()
    assert len(lang_data["active_targets"]) == 1

    client.post("/api/stop")


def test_primary_ordering_persists_after_restart(tmp_path):
    """Edge Case 5: Primary target ordering (e.g. ['zh', 'en']) persists across save and restart."""
    test_config = tmp_path / "config.yaml"
    test_config.write_text(
        "translation:\n"
        "  expected_source_language: ko\n"
        "  supported_targets: [en, uk, zh]\n"
        "  default_active_targets: [en]\n",
        encoding="utf-8",
    )

    # Save Chinese as primary target [zh, en]
    updated = save_translation_settings(
        expected_source_language="ko",
        supported_targets=["en", "uk", "zh"],
        default_active_targets=["zh", "en"],
        config_path=test_config,
    )
    assert updated["default_active_targets"] == ["zh", "en"]
    assert updated["default_active_targets"][0] == "zh"

    # Reload configuration directly (simulating fresh server restart)
    from app.config import _load
    reloaded_cfg = _load(test_config)
    reloaded_targets = reloaded_cfg["translation"]["default_active_targets"]
    assert reloaded_targets == ["zh", "en"]
    assert reloaded_targets[0] == "zh"


def test_spoken_language_switch_lifecycle(tmp_path, client):
    """
    Changing spoken ko -> en:
    - removes 'en' from today's active targets
    - 'en' remains in supported_targets
    - switching spoken back en -> ko makes 'en' selectable again
    """
    import yaml
    test_config = tmp_path / "config.yaml"
    test_config.write_text(
        "translation:\n"
        "  expected_source_language: ko\n"
        "  supported_targets: [en, ko, zh, es]\n"
        "  default_active_targets: [en, zh]\n",
        encoding="utf-8",
    )

    with patch("app.config._CONFIG_PATH", test_config), \
         patch("app.config._cfg", yaml.safe_load(test_config.read_text(encoding="utf-8"))):

        # Initial state: spoken=ko, supported=[en, ko, zh, es], active=[en, zh]
        cfg1 = translation_cfg()
        assert cfg1["expected_source_language"] == "ko"
        assert cfg1["supported_targets"] == ["en", "ko", "zh", "es"]
        assert cfg1["default_active_targets"] == ["en", "zh"]

        # 1. Switch spoken to 'en' via API
        # Active targets must not contain 'en', so operator sets targets to ['ko', 'zh']
        res = client.post(
            "/api/translation/targets",
            json={
                "expected_source_language": "en",
                "supported_targets": ["en", "ko", "zh", "es"],
                "targets": ["ko", "zh"],
            },
        )
        assert res.status_code == 200
        data = res.json()["translation"]
        assert data["expected_source_language"] == "en"
        # ✓ en remains in supported_targets
        assert "en" in data["supported_targets"]
        # ✓ en is not in today's active targets
        assert "en" not in data["default_active_targets"]
        assert data["default_active_targets"] == ["ko", "zh"]

        # 2. Switch spoken back from 'en' -> 'ko'
        # 'en' is immediately available in supported_targets and can be selected again
        res2 = client.post(
            "/api/translation/targets",
            json={
                "expected_source_language": "ko",
                "supported_targets": ["en", "ko", "zh", "es"],
                "targets": ["en", "zh"],
            },
        )
        assert res2.status_code == 200
        data2 = res2.json()["translation"]
        assert data2["expected_source_language"] == "ko"
        assert "en" in data2["supported_targets"]
        assert data2["default_active_targets"] == ["en", "zh"]
