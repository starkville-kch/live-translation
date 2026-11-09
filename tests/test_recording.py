import asyncio
import json
import logging
import math
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from app.audio import AudioCapture, channel_diagnostics
from app.events import operator_events
from app.logger import audio_log
from app.recording import WeeklyRecording


RATE = 48000
CHANNELS = 2
CHUNK = np.column_stack((
    np.sin(np.arange(4800) * 2 * np.pi * 440 / RATE) * 10000,
    np.sin(np.arange(4800) * 2 * np.pi * 880 / RATE) * 5000,
)).astype(np.int16).tobytes()


def mp3_frames(path: Path) -> list[dict]:
    """Read complete MP3 frame headers without requiring an external decoder."""
    data = path.read_bytes()
    frames = []
    offset = 0
    while offset + 4 <= len(data):
        header = int.from_bytes(data[offset:offset + 4], "big")
        assert header >> 21 == 0x7FF
        version = (header >> 19) & 3
        assert ((header >> 17) & 3) == 1
        rates = (44100, 48000, 32000)
        rate = rates[(header >> 10) & 3] // (1 if version == 3 else 2 if version == 2 else 4)
        bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if version == 3 else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
        bitrate = bitrates[(header >> 12) & 15]
        size = (144000 if version == 3 else 72000) * bitrate // rate + ((header >> 9) & 1)
        if offset + size > len(data):
            break
        frames.append({"rate": rate, "channels": 1 if (header >> 6) & 3 == 3 else 2, "bitrate": bitrate})
        offset += size
    assert frames
    return frames


def test_recording_keeps_native_format_through_translation_pause(tmp_path, caplog):
    async def run():
        recorder = WeeklyRecording(tmp_path)
        recorder.start()
        capture = AudioCapture()
        capture._recording = recorder
        capture._loop = asyncio.get_running_loop()
        capture.pause()
        recorder.submit(CHUNK, RATE, CHANNELS)
        capture._route_chunk(b"\x01\x00" * 1600)
        await asyncio.sleep(0.01)
        assert capture._queue.empty()
        capture.resume()
        capture._route_chunk(b"\x02\x00" * 1600)
        await asyncio.sleep(0.01)
        assert await capture._queue.get() == b"\x02\x00" * 1600
        recorder.submit(CHUNK, RATE, CHANNELS)
        recorder.stop()
        frames = mp3_frames(Path(recorder.info["path"]))
        assert all(frame == {"rate": RATE, "channels": CHANNELS, "bitrate": 128} for frame in frames)
        metadata = json.loads(next(tmp_path.glob("*.json")).read_text())
        assert metadata["status"] == "saved"
        assert metadata["frames"] == 9600

    audio_log.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="audio"):
            asyncio.run(run())
    finally:
        audio_log.removeHandler(caplog.handler)
    writes = [record.getMessage() for record in caplog.records if "MP3 backup writing:" in record.getMessage()]
    assert len(writes) == 1
    assert "sample_rate=48000 channels=2 bit_rate_kbps=128" in writes[0]


def test_retention_keeps_three_weeks_and_expires_only_older_files(tmp_path):
    start = datetime(2026, 11, 29, 11, tzinfo=timezone.utc)
    old = tmp_path / "2026-11-01_1100.mp3"
    old.write_bytes(b"older than twenty-one days")
    sunday = tmp_path / "2026-11-08_1100.mp3"
    sunday.write_bytes(b"exactly twenty-one days old")
    midweek = tmp_path / "2026-11-11_1100.mp3"
    midweek.write_bytes(b"eighteen days old")
    notes = tmp_path / "notes.txt"
    notes.write_text("keep")
    recorder = WeeklyRecording(tmp_path, start)
    recorder.start()
    recorder.submit(CHUNK, RATE, CHANNELS)
    recorder.stop()
    assert not old.exists()
    assert sunday.read_bytes() == b"exactly twenty-one days old"
    assert midweek.read_bytes() == b"eighteen days old"
    assert notes.read_text() == "keep"
    assert recorder.info["path"].endswith("2026-11-29_1100.mp3")


def test_same_minute_restarts_do_not_overwrite(tmp_path):
    start = datetime(2026, 11, 8, 11, tzinfo=timezone.utc)
    for _ in range(2):
        recorder = WeeklyRecording(tmp_path, start)
        recorder.start()
        recorder.submit(CHUNK, RATE, CHANNELS)
        recorder.stop()
        assert recorder.info["status"] == "saved"
    assert {path.name for path in tmp_path.glob("*.mp3")} == {"2026-11-08_1100.mp3", "2026-11-08_1100_2.mp3"}


def test_empty_or_failed_recording_keeps_previous_backup(tmp_path):
    previous = tmp_path / "2026-11-01_1100.mp3"
    previous.write_bytes(b"previous week")
    empty = WeeklyRecording(tmp_path)
    empty.start()
    empty.stop()
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("occupied")
    failed = WeeklyRecording(blocked)
    failed.start()
    failed.submit(CHUNK, RATE, CHANNELS)
    failed.stop()
    assert failed.info["status"] == "failed"
    assert failed.info["error_kind"] == "storage"
    assert failed.info["error_type"] == "FileExistsError"
    assert previous.exists()


@pytest.mark.parametrize("failure", [
    ImportError("DLL load failed while importing lameenc: Access is denied."),
    PermissionError(5, "Access is denied", "lameenc.pyd"),
])
def test_encoder_load_failure_preserves_cause_without_stopping_capture(tmp_path, failure, caplog):
    threads = []

    def unavailable_encoder():
        threads.append(threading.current_thread())
        raise failure

    last_event = operator_events.latest_id
    recorder = WeeklyRecording(tmp_path, encoder_factory=unavailable_encoder)
    assert threads == []
    audio_log.addHandler(caplog.handler)
    try:
        recorder.start()
        recorder.submit(CHUNK, RATE, CHANNELS)
        recorder.stop()
    finally:
        audio_log.removeHandler(caplog.handler)
    info = recorder.info
    assert threads and threads[0] is not threading.current_thread()
    assert info["status"] == "failed"
    assert info["error_kind"] == "encoder_unavailable"
    assert info["error_type"] == type(failure).__name__
    assert info["error"] == str(failure)
    assert info["directory"] == str(tmp_path)
    assert not list(tmp_path.iterdir())
    recorder.submit(CHUNK, RATE, CHANNELS)
    assert recorder._queue.empty()
    event = operator_events.since(last_event)[-1]
    assert "백업이 없습니다" in event["message"]
    assert "Translation continues" in event["message"]
    assert "disk space" not in event["message"]
    assert event["details"]["error"] == str(failure)
    assert event["details"]["error_type"] == type(failure).__name__
    assert any(str(failure) in record.getMessage() for record in caplog.records)


def test_storage_finalization_keeps_the_original_failure(tmp_path):
    start = datetime(2026, 11, 8, 11, tzinfo=timezone.utc)
    (tmp_path / "2026-11-08_1100.json").mkdir()
    last_event = operator_events.latest_id
    recorder = WeeklyRecording(tmp_path, start)
    recorder.start()
    recorder.submit(CHUNK, RATE, CHANNELS)
    recorder.stop()
    info = recorder.info
    assert info["status"] == "failed"
    assert info["error_kind"] == "storage"
    assert info["error"] in info["finalization_error"]
    assert info["error_type"] in info["finalization_error"]
    events = operator_events.since(last_event)
    assert len(events) == 1
    assert "파일 저장 오류" in events[0]["message"]
    assert "Translation continues" in events[0]["message"]
    assert events[0]["details"]["error"] == info["error"]


def test_retention_failure_does_not_stop_current_recording(tmp_path):
    class LockedOldRecording(WeeklyRecording):
        def _expire_old_recordings(self) -> None:
            raise PermissionError("Previous backup is locked")

    previous = tmp_path / "2026-11-01_1100.mp3"
    previous.write_bytes(b"previous week")
    recorder = LockedOldRecording(tmp_path, datetime(2026, 11, 8, 11, tzinfo=timezone.utc))
    recorder.start()
    for _ in range(3):
        recorder.submit(CHUNK, RATE, CHANNELS)
    recorder.stop()
    assert previous.read_bytes() == b"previous week"
    assert recorder.info["status"] == "saved"
    assert recorder.info["frames"] == 14400
    assert recorder.info["retention_error"] == "Previous backup is locked"
    mp3_frames(Path(recorder.info["path"]))


def test_capture_gap_and_dropped_tail_preserve_native_timeline(tmp_path):
    recorder = WeeklyRecording(tmp_path / "gap")
    recorder.start()
    recorder.submit(CHUNK, RATE, CHANNELS, recorder._origin + 0.1)
    recorder.submit(CHUNK, RATE, CHANNELS, recorder._origin + 1.1)
    recorder.stop()
    assert recorder.info["frames"] == 52800
    assert recorder.info["gaps"] == [{"offset_seconds": 0.1, "duration_seconds": 0.9}]
    mp3_frames(Path(recorder.info["path"]))
    overflow = WeeklyRecording(tmp_path / "tail")
    for index in range(120):
        overflow.submit(CHUNK, RATE, CHANNELS, overflow._origin + (index + 1) * 0.1)
    assert overflow.info["dropped_chunks"] == 20
    overflow.start()
    overflow.stop()
    assert overflow.info["frames"] == 576000
    assert overflow.info["gaps"] == [{"offset_seconds": 10.0, "duration_seconds": 2.0}]
    mp3_frames(Path(overflow.info["path"]))


def test_channel_diagnostics_distinguish_phase_and_silence():
    samples = np.array([-10000, 0, 10000, 0], dtype=np.int16)
    levels, correlation = channel_diagnostics(np.column_stack((samples, samples)).tobytes(), 2)
    assert correlation == 1.0
    assert abs(levels[0] - 20 * math.log10(math.sqrt(50_000_000) / 32768)) < 1e-6
    assert channel_diagnostics(np.column_stack((samples, -samples)).tobytes(), 2)[1] == -1.0
    silence, silent_correlation = channel_diagnostics(b"\0\0" * 100, 1)
    assert silence == [float("-inf")]
    assert silent_correlation is None
