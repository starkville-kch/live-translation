import copy

import pytest
import yaml

from app import config


def test_source_languages_are_unrestricted_and_model_is_pinned():
    settings = config.translation_cfg()
    assert "expected_source_languages" not in settings
    assert "expected_source_language" not in settings
    assert "drift_window" not in settings
    assert config.gemini_model() == config.gemini_cfg()["model"]
    assert "fallback_model" not in config.gemini_cfg()
    assert "auto_drift_correction" not in config.gemini_cfg()


def test_output_selection_allows_expected_source_language():
    config.validate_translation_settings(["ko", "en", "uk"], ["ko", "en"])


@pytest.mark.parametrize("supported,active", [([], []), (["en"], []), (["invalid"], ["invalid"]), (["en", "en"], ["en"]), (["en"], ["uk"]), (["en"], ["en", "en"])])
def test_invalid_output_selection_is_rejected(supported, active):
    with pytest.raises(ValueError):
        config.validate_translation_settings(supported, active)


def test_output_save_preserves_guest_languages_and_live_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("# keep\ntranslation:\n  expected_source_languages: [es, en] # guest\n  supported_targets:\n    - en\n  default_active_targets:\n    - en\n", encoding="utf-8")
    before = copy.deepcopy(config._cfg)
    config.save_translation_settings(["en", "ko", "zh"], ["en", "zh"], config_path=path)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["translation"]["expected_source_languages"] == ["es", "en"]
    assert saved["translation"]["default_active_targets"] == ["en", "zh"]
    assert "# keep" in path.read_text(encoding="utf-8")
    assert "# guest" in path.read_text(encoding="utf-8")
    assert config._cfg == before
