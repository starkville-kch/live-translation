"""
tests/test_setup_config.py — Unit Tests for Setup & Config
===========================================================
Tests for:
- API key masking
- Atomic .env updates preserving existing variables and comments
- Atomic config.yaml church identity updates
- Mocked Gemini client authentication and model availability checks
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import yaml

from app.config import (
    mask_api_key,
    update_gemini_api_key,
    _atomic_yaml_write,
    DEFAULT_CONFIG,
    get_app_root,
)


def test_mask_api_key():
    assert mask_api_key("") == "Not configured"
    assert mask_api_key("short") == "••••••••"
    key = "AIzaSy1234567890abcdef4xQ9"
    masked = mask_api_key(key)
    assert masked == "AIzaSy••••••••4xQ9"
    assert "1234567890abcdef" not in masked


def test_update_gemini_api_key_creates_and_preserves():
    with tempfile.TemporaryDirectory() as tmpdir:
        env_path = Path(tmpdir) / ".env"
        # Seed existing variables and comments
        env_path.write_text(
            "# Church translation environment\n"
            "CUSTOM_VAR=preserved_value\n"
            "GEMINI_API_KEY=old_secret_key_12345\n"
            "ANOTHER_VAR=keep_me\n",
            encoding="utf-8",
        )

        new_key = "AIzaSy_new_key_9876543210"
        update_gemini_api_key(new_key, env_path=env_path)

        content = env_path.read_text(encoding="utf-8")
        assert f"GEMINI_API_KEY={new_key}" in content
        assert "old_secret_key_12345" not in content
        assert "CUSTOM_VAR=preserved_value" in content
        assert "ANOTHER_VAR=keep_me" in content
        assert "# Church translation environment" in content


def test_update_gemini_api_key_new_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        env_path = Path(tmpdir) / ".env"
        new_key = "AIzaSy_fresh_key_12345"
        update_gemini_api_key(new_key, env_path=env_path)

        content = env_path.read_text(encoding="utf-8")
        assert f"GEMINI_API_KEY={new_key}" in content


def test_atomic_yaml_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = Path(tmpdir) / "config.yaml"
        sample_data = {
            "church": {
                "name": "Grace Korean Church",
                "short_name": "GKC",
                "logo": "branding/church-logo.png",
            },
            "network": {
                "hostname": "gkc",
                "port": 8080,
            },
        }
        _atomic_yaml_write(cfg_path, sample_data)

        assert cfg_path.exists()
        with open(cfg_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        assert loaded["church"]["name"] == "Grace Korean Church"
        assert loaded["network"]["hostname"] == "gkc"


def test_get_app_root():
    root = get_app_root()
    assert isinstance(root, Path)
    assert root.exists()


def test_mocked_gemini_validation_success():
    """Verify setup_gui connection logic succeeds when model is present."""
    from setup_gui import SetupApp
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = SetupApp(root)
        app.configured_model = "gemini-3.5-live-translate-preview"

        mock_model_1 = MagicMock()
        mock_model_1.name = "models/gemini-3.5-live-translate-preview"
        mock_model_2 = MagicMock()
        mock_model_2.name = "models/gemini-2.5-flash"

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.list.return_value = [mock_model_1, mock_model_2]
            mock_client_cls.return_value = mock_client

            results = {}

            def _capture_test_complete(key_valid, model_valid, error_msg, model_note):
                results["key_valid"] = key_valid
                results["model_valid"] = model_valid
                results["model_note"] = model_note

            app._on_test_complete = _capture_test_complete
            app._run_connection_test("AIzaSy_test_key_12345678")
            root.update()

            assert results.get("key_valid") is True
            assert results.get("model_valid") is True
            assert "gemini-3.5-live-translate-preview" in results.get("model_note", "")
    finally:
        root.destroy()


def test_mocked_gemini_validation_invalid_key_sanitization():
    """Verify raw API key is never leaked in error messages when authentication fails."""
    from setup_gui import SetupApp
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = SetupApp(root)
        raw_key = "AIzaSy_secret_leak_test_9999"

        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.side_effect = Exception(f"API_KEY_INVALID: Key {raw_key} expired.")

            results = {}

            def _capture_test_complete(key_valid, model_valid, error_msg, model_note):
                results["key_valid"] = key_valid
                results["error_msg"] = error_msg

            app._on_test_complete = _capture_test_complete
            app._run_connection_test(raw_key)
            root.update()

            assert results.get("key_valid") is False
            # Raw key must NOT be in error message
            assert raw_key not in results.get("error_msg", "")
            assert "••••••••" in results.get("error_msg", "")
    finally:
        root.destroy()
