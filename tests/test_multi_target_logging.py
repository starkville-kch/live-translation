"""
tests/test_multi_target_logging.py — Tests for Target-Aware Session Logs, Manifest, and Cost Accounting
"""
import asyncio
import json
import shutil
import time
from datetime import datetime, timezone
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
        asyncio.run(manager.start(active_targets=["en", "uk"]))

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

        # Verify canonical 4-file layout
        files = {p.name for p in s_dir.iterdir()}
        assert {"session.json", "transcript.jsonl", "transcript.md", "summary.txt"}.issubset(files)

        # 1. session.json manifest
        manifest_file = s_dir / "session.json"
        assert manifest_file.exists()
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert manifest["spoken_language"] == "auto"
        assert "expected_source_language" not in manifest
        assert manifest["active_targets"] == ["en", "uk"]
        assert manifest["total_turns"] == 2
        assert "cost_by_target" in manifest
        assert "en" in manifest["cost_by_target"]
        assert "uk" in manifest["cost_by_target"]

        # 2. transcript.jsonl (canonical machine-readable records)
        jsonl_file = s_dir / "transcript.jsonl"
        assert jsonl_file.exists()
        lines = [json.loads(line) for line in jsonl_file.read_text(encoding="utf-8").strip().split("\n")]
        assert len(lines) == 2
        assert lines[0]["source"]["text"] == "안녕하세요 여러분"
        assert lines[0]["source"]["lang"] == "ko"
        assert lines[0]["targets"]["en"] == "Hello everyone"
        assert lines[0]["targets"]["uk"] == "Всім привіт"

        assert lines[1]["source"]["text"] == "오늘 예배에 오신 것을 환영합니다"
        assert lines[1]["targets"]["en"] == "Welcome to today's service"
        assert lines[1]["targets"]["uk"] == "Ласкаво просимо на сьогоднішнє служіння"

        # 3. transcript.md (human-readable chronological transcript)
        md_file = s_dir / "transcript.md"
        assert md_file.exists()
        md_text = md_file.read_text(encoding="utf-8")
        assert "# Translation Transcript" in md_text
        assert "Spoken language: Original speech" in md_text
        assert "**Spoken — Original speech" in md_text
        assert "**English**" in md_text
        assert "**Українська (Ukrainian)**" in md_text
        assert "Hello everyone" in md_text
        assert "Всім привіт" in md_text

        # 4. summary.txt (operational summary)
        summary_file = s_dir / "summary.txt"
        assert summary_file.exists()
        summary_text = summary_file.read_text(encoding="utf-8")
        assert "Spoken:         Original speech" in summary_text
        assert "Active Targets: English, Ukrainian" in summary_text
        assert "Committed turns:2" in summary_text

        # Retired legacy files MUST NOT exist
        assert not (s_dir / "source.txt").exists()
        assert not (s_dir / "ko.txt").exists()
        assert not (s_dir / "en.txt").exists()
        assert not (s_dir / "target_en.txt").exists()
        assert not (s_dir / "target_uk.txt").exists()
        assert not (s_dir / "aligned.txt").exists()
        assert not (s_dir / "aligned_en.txt").exists()
        assert not (s_dir / "aligned_uk.txt").exists()


def test_multi_target_cost_accounting():
    client = TestClient(app)

    with patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):
        res_start = client.post("/api/start", json={"targets": ["en", "uk"]})
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


def test_committed_name_corrections_and_multilingual_turn_export(clean_sessions_and_logs):
    manager._is_running = True
    manager._start_time = time.monotonic() - 60
    manager._started_at = datetime.now().astimezone()
    manager.active_targets = ["en"]
    session = manager._create_session_for_target("en")
    manager.sessions = {"en": session}
    session._observe_source_language("ko")
    session._current_source = "스타필드 교회"
    session._current_target = "Starfield Church"
    session.flush_current_turn()
    session._observe_source_language("zh")
    session._current_source = "欢迎"
    session._current_target = "Welcome"
    session.flush_current_turn()
    _write_session_log()
    session_dir = next((clean_sessions_and_logs / "sessions").iterdir())
    records = [json.loads(line) for line in (session_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["source"]["text"] == "스타크빌 한인교회"
    assert records[0]["targets"]["en"] == "Starkville Korean Church"
    assert records[1]["source"] == {"lang": "zh", "text": "欢迎"}
    assert records[1]["targets"]["en"] == "Welcome"
    exported = (session_dir / "transcript.md").read_text(encoding="utf-8")
    assert "欢迎" in exported
    assert "Welcome" in exported
    assert "youtube_chapters.json" not in {path.name for path in session_dir.iterdir()}


def test_real_api_stop_generates_session_logs(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)

    with patch("app.server.logging_cfg", return_value={"log_dir": str(log_dir)}), \
         patch.object(GeminiSession, "start", new_callable=AsyncMock), \
         patch.object(AudioCapture, "start", MagicMock()), \
         patch.object(AudioCapture, "stop", MagicMock()):

        # Start session via API
        res_start = client.post("/api/start", json={"targets": ["en"]})
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

        files = {p.name for p in s_dir.iterdir()}
        assert {"session.json", "transcript.jsonl", "transcript.md", "summary.txt"}.issubset(files)

        assert "Hello everyone" in (s_dir / "transcript.jsonl").read_text(encoding="utf-8")
        assert "안녕하세요 여러분" in (s_dir / "transcript.jsonl").read_text(encoding="utf-8")
        assert "Hello everyone" in (s_dir / "transcript.md").read_text(encoding="utf-8")
        assert "안녕하세요 여러분" in (s_dir / "transcript.md").read_text(encoding="utf-8")


def test_empty_session_does_not_export_logs(tmp_path):
    """Zero-turn and negligible runtime sessions must not create empty log folders."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with patch("app.server.logging_cfg", return_value={"log_dir": str(log_dir)}):
        asyncio.run(manager.stop())
        result = _write_session_log()
        assert result is None
        assert not (log_dir / "sessions").exists() or len(list((log_dir / "sessions").glob("*"))) == 0


def test_export_keeps_pause_on_service_timeline(clean_sessions_and_logs):
    manager._is_running = True
    manager._start_time = time.monotonic() - 120
    manager._started_at = datetime(2026, 11, 8, 11, tzinfo=timezone.utc)
    manager._paused_duration_s = 30
    manager.active_targets = ["en"]
    session = manager._create_session_for_target("en")
    manager.sessions = {"en": session}
    session._transcript = [TranscriptEntry(
        timestamp=manager.start_monotonic + 100,
        source="Original", target="Translation", source_lang="ko+en", target_lang="en",
    )]
    _write_session_log()
    session_dir = next((clean_sessions_and_logs / "sessions").iterdir())
    manifest = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    turn = json.loads((session_dir / "transcript.jsonl").read_text(encoding="utf-8"))
    assert 90 <= manifest["runtime_seconds"] < 91
    assert 120 <= manifest["elapsed_seconds"] < 121
    assert turn["offset_seconds"] == 100
    assert turn["timestamp"] == "11:01:40"
    assert datetime.fromisoformat(manifest["started_at"]).tzinfo is not None
    assert datetime.fromisoformat(manifest["ended_at"]).tzinfo is not None


