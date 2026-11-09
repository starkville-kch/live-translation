"""Permanent startup diagnostics, without changing process globals or clocks."""
import json
from pathlib import Path

import pytest

from app.startup_timing import MAX_LOG_BYTES, StartupTrace


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def monotonic(self):
        return self.elapsed

    def wall(self):
        return 1794153600 + self.elapsed


def records(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_summary_measures_imports_and_is_written_once(tmp_path):
    clock = Clock()
    path = tmp_path / "startup.log"
    trace = StartupTrace(path, launched_at=clock.wall() - 2,
                         wall_clock=clock.wall, monotonic=clock.monotonic)
    with trace.timed("application_imports"):
        clock.elapsed += 5
    trace.phase("server_listening", port=8080)
    with trace.timed("browser_open"):
        clock.elapsed += 0.2
    trace.complete("browser launch requested")
    trace.complete()
    summary = records(path)[-1]
    assert summary["phase"] == "startup_summary"
    assert summary["slowest_phase"] == "application_imports"
    assert summary["slowest_seconds"] == 5
    assert summary["elapsed_seconds"] == 7.2
    assert "browser launch requested after 7.2s" in summary["summary"]
    assert "slowest phase application_imports 5.0s" in summary["summary"]
    assert len([row for row in records(path) if row["phase"] == "startup_summary"]) == 1
    assert all("T" in row["time"] for row in records(path))


def test_pre_python_gap_can_be_slowest_and_optional_work_is_excluded(tmp_path):
    clock = Clock()
    path = tmp_path / "startup.log"
    trace = StartupTrace(path, launched_at=clock.wall() - 30,
                         wall_clock=clock.wall, monotonic=clock.monotonic)
    with trace.timed("application_imports"):
        clock.elapsed += 1
    trace.phase("server_listening")
    with trace.timed("public_https_probe"):
        clock.elapsed += 40
    trace.complete("browser launch requested")
    with trace.timed("audio_device_enumeration"):
        clock.elapsed += 2
    rows = records(path)
    summary = next(row for row in rows if row["phase"] == "startup_summary")
    assert summary["slowest_phase"] == "before_python"
    assert summary["slowest_seconds"] == 30
    assert next(row for row in rows if row["phase"] == "public_https_probe_done")["background"]
    assert rows[-1]["after_startup"]
    assert rows[-1]["background"]


def test_new_launch_appends_with_its_own_summary(tmp_path):
    path = tmp_path / "startup.log"
    for _ in range(2):
        trace = StartupTrace(path)
        trace.phase("python_main_start")
        trace.complete("browser launch requested")
    assert [row["phase"] for row in records(path)] == [
        "python_main_start", "startup_summary", "python_main_start", "startup_summary",
    ]


def test_rotation_keeps_only_one_bounded_backup(tmp_path):
    path = tmp_path / "startup.log"
    backup = path.with_suffix(".log.1")
    full_log = b"{}\n" * (MAX_LOG_BYTES // 3)
    path.write_bytes(full_log)
    backup.write_bytes(b"older log")
    trace = StartupTrace(path)
    trace.phase("python_main_start")
    assert backup.read_bytes() == full_log
    trace.complete("browser launch requested")
    assert path.stat().st_size < MAX_LOG_BYTES
    assert backup.stat().st_size <= MAX_LOG_BYTES
    assert sorted(file.name for file in tmp_path.iterdir()) == ["startup.log", "startup.log.1"]


def test_failure_records_duration_and_does_not_hide_original_error(tmp_path):
    clock = Clock()
    path = tmp_path / "startup.log"
    trace = StartupTrace(path, wall_clock=clock.wall, monotonic=clock.monotonic)
    with pytest.raises(ValueError, match="configuration invalid"):
        with trace.timed("configuration_load"):
            clock.elapsed += 3
            raise ValueError("configuration invalid")
    trace.complete()
    failed, summary = records(path)[-2:]
    assert failed["error_type"] == "ValueError"
    assert failed["duration_seconds"] == 3
    assert summary["slowest_phase"] == "configuration_load"
    assert summary["outcome"] == "startup ended before browser launch request"


def test_unwritable_log_cannot_prevent_startup(tmp_path):
    path = tmp_path / "directory"
    path.mkdir()
    trace = StartupTrace(path)
    with trace.timed("application_imports"):
        pass
    trace.complete()
