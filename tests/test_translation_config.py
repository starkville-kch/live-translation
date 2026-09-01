"""
tests/test_translation_config.py — Unit Tests for Translation Configuration & Backward Compatibility
"""
import tempfile
from pathlib import Path
import pytest
import yaml

from app.config import (
    translation_cfg,
    save_translation_settings,
    validate_translation_settings,
    _load,
)


def test_translation_config_defaults_and_backward_compatibility():
    # If no translation block exists, returns safe fallback
    cfg = translation_cfg()
    assert cfg["expected_source_language"] == "ko"
    assert "en" in cfg["supported_targets"]
    assert "en" in cfg["default_active_targets"]


def test_translation_config_validation():
    # Valid configuration
    validate_translation_settings("ko", ["en", "uk", "zh"], ["en", "uk"])
    validate_translation_settings("en", ["uk", "zh"], ["uk"])

    # Invalid source code
    with pytest.raises(ValueError, match="Invalid expected source language code"):
        validate_translation_settings("unknown_lang_xyz", ["en"], ["en"])

    # Empty supported targets
    with pytest.raises(ValueError, match="At least one supported target"):
        validate_translation_settings("ko", [], [])

    # Invalid target code
    with pytest.raises(ValueError, match="Invalid supported target language code"):
        validate_translation_settings("ko", ["invalid_target"], ["invalid_target"])

    # Duplicate target codes
    with pytest.raises(ValueError, match="Duplicate supported target language code"):
        validate_translation_settings("ko", ["en", "en"], ["en"])

    # Source cannot be target
    with pytest.raises(ValueError, match="cannot be in supported translation targets"):
        validate_translation_settings("ko", ["ko", "en"], ["en"])

    # Active target not in supported list
    with pytest.raises(ValueError, match="is not in supported targets list"):
        validate_translation_settings("ko", ["en"], ["uk"])

    # Duplicate active target codes
    with pytest.raises(ValueError, match="Duplicate default active target code"):
        validate_translation_settings("ko", ["en", "uk"], ["en", "en"])


def test_save_translation_settings_atomic(tmp_path: Path):
    temp_yaml = tmp_path / "config.yaml"
    initial_data = {
        "church": {"name": "Test Church"},
        "translation": {
            "expected_source_language": "ko",
            "supported_targets": ["en"],
            "default_active_targets": ["en"],
        },
    }
    with open(temp_yaml, "w", encoding="utf-8") as f:
        yaml.dump(initial_data, f)

    res = save_translation_settings(
        expected_source_language="en",
        supported_targets=["uk", "zh"],
        default_active_targets=["uk", "zh"],
        config_path=temp_yaml,
    )

    assert res["expected_source_language"] == "en"
    assert res["supported_targets"] == ["uk", "zh"]
    assert res["default_active_targets"] == ["uk", "zh"]

    # Verify saved file content
    with open(temp_yaml, "r", encoding="utf-8") as f:
        saved = yaml.safe_load(f)
    assert saved["translation"]["expected_source_language"] == "en"
    assert saved["translation"]["supported_targets"] == ["uk", "zh"]
