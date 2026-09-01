"""
tests/test_multi_target_logging.py — Tests for Target-Aware Session Logs, Manifest, and Cost Accounting
"""
import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.audio import AudioCapture
from app.config import logging_cfg
from app.gemini_session import GeminiSession, SessionStatus, TranscriptEntry
from app.server import app, manager, _write_session_log


@pytest.fixture(autouse=True)
def clean_sessions_and_logs(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with patch("app.server.logging_cfg", return_value={"log_dir": str(log_dir)}), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        asyncio.run(manager.stop())
        yield log_dir
        asyncio.run(manager.stop())


def test_target_aware_session_manifest_and_transcripts(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    with patch("app.server.logging_cfg", return_value={"log_dir": str(log_dir)}), \
         patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):

        # Start multi-target session
        asyncio.run(manager.start(active_targets=["en", "uk"], expected_source_language="ko"))

        sess_en = manager.sessions["en"]
        sess_uk = manager.sessions["uk"]

        # Populate transcripts
        sess_en._transcript = [
            TranscriptEntry(timestamp=100.0, source="안녕하세요 여러분", target="Hello everyone", source_lang="ko", target_lang="en"),
            TranscriptEntry(timestamp=105.0, source="오늘 예배에 오신 것을 환영합니다", target="Welcome to today's service", source_lang="ko", target_lang="en"),
        ]
        sess_uk._transcript = [
            TranscriptEntry(timestamp=100.0, source="안녕하세요 여러분", target="Всім привіт", source_lang="ko", target_lang="uk"),
            TranscriptEntry(timestamp=105.0, source="오늘 예배에 오신 것을 환영합니다", target="Ласкаво просимо на сьогоднішнє служіння", source_lang="ko", target_lang="uk"),
        ]

        # Trigger log export
        _write_session_log()

        # Find session dir
        session_dirs = list((log_dir / "sessions").glob("*"))
        assert len(session_dirs) == 1
        s_dir = session_dirs[0]

        # 1. session.json manifest
        manifest_file = s_dir / "session.json"
        assert manifest_file.exists()
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert manifest["expected_source_language"] == "ko"
        assert manifest["active_targets"] == ["en", "uk"]
        assert "cost_by_target" in manifest
        assert "en" in manifest["cost_by_target"]
        assert "uk" in manifest["cost_by_target"]

        # 2. source.txt
        src_file = s_dir / "source.txt"
        assert src_file.exists()
        src_content = src_file.read_text(encoding="utf-8")
        assert "안녕하세요 여러분" in src_content
        assert "오늘 예배에 오신 것을 환영합니다" in src_content

        # 3. target_en.txt and target_uk.txt
        tgt_en_file = s_dir / "target_en.txt"
        tgt_uk_file = s_dir / "target_uk.txt"
        assert tgt_en_file.exists()
        assert tgt_uk_file.exists()
        assert "Hello everyone" in tgt_en_file.read_text(encoding="utf-8")
        assert "Всім привіт" in tgt_uk_file.read_text(encoding="utf-8")

        # 4. aligned_en.txt and aligned_uk.txt
        aligned_en_file = s_dir / "aligned_en.txt"
        aligned_uk_file = s_dir / "aligned_uk.txt"
        assert aligned_en_file.exists()
        assert aligned_uk_file.exists()

        en_aligned_text = aligned_en_file.read_text(encoding="utf-8")
        assert "[source] 안녕하세요 여러분" in en_aligned_text
        assert "[target] Hello everyone" in en_aligned_text

        uk_aligned_text = aligned_uk_file.read_text(encoding="utf-8")
        assert "[source] 안녕하세요 여러분" in uk_aligned_text
        assert "[target] Всім привіт" in uk_aligned_text


def test_multi_target_cost_accounting():
    client = TestClient(app)

    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        res_start = client.post("/api/start", json={"targets": ["en", "uk"], "expected_source_language": "ko"})
        assert res_start.status_code == 200

        res_st = client.get("/api/status")
        assert res_st.status_code == 200
        data = res_st.json()

        assert "translation" in data
        t = data["translation"]
        assert t["active_targets"] == ["en", "uk"]
        assert "sessions" in t
        assert "en" in t["sessions"]
        assert "uk" in t["sessions"]
        assert "estimated_cost" in t["sessions"]["en"]
        assert "estimated_cost" in t["sessions"]["uk"]
        assert "estimated_total_cost" in t

        client.post("/api/stop")


def test_glossary_isolation_across_languages():
    mock_glossary = MagicMock()
    mock_glossary.correct = MagicMock(side_effect=lambda src, tgt: f"{tgt} (CORRECTED)")

    mgr = manager
    mgr._glossary = mock_glossary

    # ko -> en session gets glossary
    sess_en = mgr._create_session_for_target("en", "ko")
    assert sess_en._glossary is not None

    # ko -> uk session does NOT get ko->en glossary
    sess_uk = mgr._create_session_for_target("uk", "ko")
    assert sess_uk._glossary is None

    # en -> uk session does NOT get glossary
    sess_en_uk = mgr._create_session_for_target("uk", "en")
    assert sess_en_uk._glossary is None

    # en -> zh session does NOT get glossary
    sess_en_zh = mgr._create_session_for_target("zh", "en")
    assert sess_en_zh._glossary is None


def test_real_api_stop_generates_session_logs(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)

    with patch("app.server.logging_cfg", return_value={"log_dir": str(log_dir)}), \
         patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):

        # Start session via API
        res_start = client.post("/api/start", json={"targets": ["en"], "expected_source_language": "ko"})
        assert res_start.status_code == 200

        sess_en = manager.sessions["en"]
        sess_en._transcript = [
            TranscriptEntry(timestamp=100.0, source="안녕하세요 여러분", target="Hello everyone", source_lang="ko", target_lang="en"),
        ]

        # Stop session via API
        res_stop = client.post("/api/stop")
        assert res_stop.status_code == 200

        # Check that session log directory was created and contains all expected files
        session_dirs = list((log_dir / "sessions").glob("*"))
        assert len(session_dirs) == 1
        s_dir = session_dirs[0]

        assert (s_dir / "session.json").exists()
        assert (s_dir / "source.txt").exists()
        assert (s_dir / "target_en.txt").exists()
        assert (s_dir / "aligned_en.txt").exists()

        assert "Hello everyone" in (s_dir / "target_en.txt").read_text(encoding="utf-8")
        assert "안녕하세요 여러분" in (s_dir / "source.txt").read_text(encoding="utf-8")


