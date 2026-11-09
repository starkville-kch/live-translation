"""Bounded native-PCM MP3 backup, independent of translation pause state."""
import json
import queue
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.events import operator_events
from app.logger import audio_log


def _create_encoder():
    # Load the native DLL in the recording worker, not during server startup.
    import lameenc
    return lameenc.Encoder()


class WeeklyRecording:
    """Encode capture audio at 128 kbps and retain generated files for 21 days."""

    def __init__(self, directory: Path, started_at: datetime | None = None, *, encoder_factory=_create_encoder):
        self.directory = directory
        self.started_at = started_at or datetime.now().astimezone()
        self._encoder_factory = encoder_factory
        self._origin = time.monotonic()
        self._latest_end = 0.0
        self._queue: queue.Queue[tuple[bytes, float, int, int]] = queue.Queue(maxsize=100)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._first_write_logged = False
        self._info: dict = {
            "status": "waiting", "started_at": self.started_at.isoformat(),
            "format": "mp3", "bit_rate_kbps": 128,
            "sample_rate": None, "channels": None, "sample_width": 2,
            "frames": 0, "dropped_chunks": 0, "gaps": [],
        }

    @property
    def info(self) -> dict:
        return {**self._info, "gaps": list(self._info["gaps"])}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._write, daemon=True)
        self._thread.start()

    def submit(self, data: bytes, sample_rate: int, channels: int, captured_at: float | None = None) -> None:
        """Queue native PCM without disk access or waiting on translation."""
        if self._stop.is_set() or self._info["status"] == "failed":
            return
        end = captured_at if captured_at is not None else time.monotonic()
        self._latest_end = max(self._latest_end, end - self._origin)
        offset = max(0.0, end - self._origin - len(data) / (sample_rate * channels * 2))
        try:
            self._queue.put_nowait((data, offset, sample_rate, channels))
        except queue.Full:
            self._info["dropped_chunks"] += 1

    def stop(self) -> None:
        if not self._stop.is_set():
            self._latest_end = max(self._latest_end, time.monotonic() - self._origin)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                self._info["status"] = "finishing"
                audio_log.warning("Audio backup is still finishing its disk writes")

    def _expire_old_recordings(self) -> None:
        """Delete only recognized generated files older than 21 days."""
        cutoff = (self.started_at - timedelta(days=21)).replace(tzinfo=None)
        for path in self.directory.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}(?:_\d+)?\.(mp3|json)", path.name):
                stamp, pattern = path.stem[:15], "%Y-%m-%d_%H%M"
            elif re.fullmatch(r"\d{8}_\d{6}_\d{6}\.(wav|json)", path.name):
                # Backups from the previous WAV implementation use this format.
                stamp, pattern = path.stem, "%Y%m%d_%H%M%S_%f"
            else:
                continue
            try:
                recorded = datetime.strptime(stamp, pattern)
            except ValueError:
                continue
            if recorded < cutoff:
                path.unlink()

    def _pad_to(self, output, encoder, offset: float) -> None:
        rate, channels = self._info["sample_rate"], self._info["channels"]
        gap = round(offset * rate) - self._info["frames"]
        if gap <= rate // 4:
            return
        self._info["gaps"].append({
            "offset_seconds": self._info["frames"] / rate,
            "duration_seconds": gap / rate,
        })
        remaining = gap
        while remaining:
            frames = min(remaining, rate)
            self._write_encoded(output, encoder.encode(b"\0\0" * frames * channels))
            remaining -= frames
        self._info["frames"] += gap

    def _write_encoded(self, output, data: bytes) -> None:
        written = output.write(data)
        if written and not self._first_write_logged:
            self._first_write_logged = True
            audio_log.info("MP3 backup writing: path=%s sample_rate=%s channels=%s bit_rate_kbps=128",
                           self._info["path"], self._info["sample_rate"], self._info["channels"])

    def _failed(self, kind: str, exc: Exception) -> None:
        if kind == "encoder_unavailable":
            message = "MP3 인코더 로딩 실패로 백업이 없습니다. 번역은 계속됩니다. / No MP3 backup: encoder loading failed. Translation continues."
        elif kind == "storage":
            message = "파일 저장 오류로 MP3 백업이 불완전합니다. 번역은 계속됩니다. / MP3 backup incomplete: file storage failed. Translation continues."
        else:
            message = "녹음 처리 오류로 MP3 백업이 불완전합니다. 번역은 계속됩니다. / MP3 backup incomplete: encoding failed. Translation continues."
        details = {"error_kind": kind, "error_type": type(exc).__name__, "error": str(exc), "directory": str(self.directory)}
        detail = f"{type(exc).__name__}: {exc}"
        self._info.update(status="failed", error_message=message, **details)
        audio_log.warning("%s kind=%s directory=%s %s", message, kind, self.directory, detail)
        operator_events.add("warning", message, details)

    def _write(self) -> None:
        output = encoder = metadata = None
        retention_applied = False
        failure_kind = "encoder_unavailable"
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    data, offset, rate, channels = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if output is None:
                    encoder = self._encoder_factory()
                    encoder.set_bit_rate(128)
                    encoder.set_in_sample_rate(rate)
                    encoder.set_out_sample_rate(rate)
                    encoder.set_channels(channels)
                    encoder.set_quality(2)
                    encoder.silence()
                    failure_kind = "recording"
                    self.directory.mkdir(parents=True, exist_ok=True)
                    stem = self.started_at.strftime("%Y-%m-%d_%H%M")
                    path = self.directory / f"{stem}.mp3"
                    suffix = 2
                    while path.exists():
                        path = self.directory / f"{stem}_{suffix}.mp3"
                        suffix += 1
                    # Complete MP3 frames remain on disk if the process is killed.
                    output = path.open("xb", buffering=0)
                    metadata = path.with_suffix(".json")
                    self._info.update(status="recording", path=str(path), sample_rate=rate, channels=channels)
                    metadata.write_text(json.dumps(self.info, indent=2), encoding="utf-8")
                if (rate, channels) != (self._info["sample_rate"], self._info["channels"]):
                    raise ValueError("Capture format changed during MP3 recording")
                self._pad_to(output, encoder, offset)
                self._write_encoded(output, encoder.encode(data))
                self._info["frames"] += len(data) // (channels * 2)
                if not retention_applied:
                    try:
                        self._expire_old_recordings()
                    except OSError as exc:
                        self._info["retention_error"] = str(exc)
                        audio_log.warning("Could not remove older audio backups; recording continues: %s", exc)
                        operator_events.add("warning", "Could not remove older audio backups; current recording continues")
                    retention_applied = True
            if output is not None:
                self._pad_to(output, encoder, self._latest_end)
                self._write_encoded(output, encoder.flush())
            self._info["status"] = "saved" if output else "empty"
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            if isinstance(exc, OSError) and failure_kind != "encoder_unavailable":
                failure_kind = "storage"
            self._failed(failure_kind, exc)
        finally:
            try:
                if output is not None:
                    output.close()
                if metadata is not None:
                    metadata.write_text(json.dumps(self.info, indent=2), encoding="utf-8")
            except OSError as exc:
                if self._info["status"] != "failed":
                    self._failed("storage", exc)
                else:
                    self._info["finalization_error"] = f"{type(exc).__name__}: {exc}"
                    audio_log.warning("Could not finalize failed audio backup: %s", exc)
