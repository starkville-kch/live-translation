"""Bounded startup trace available before application imports."""
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


MAX_LOG_BYTES = 1024 * 1024


class StartupTrace:
    """Keep one launch summary and at most two 1 MiB log files."""

    def __init__(self, path: Path, *, launched_at: float | None = None,
                 wall_clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.perf_counter):
        self.path = path
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._python_started = monotonic()
        self._wall_started = wall_clock()
        self._launch_started = self._wall_started if launched_at is None else launched_at
        self._before_python = max(0.0, self._wall_started - self._launch_started)
        self._slowest = ("before_python", self._before_python)
        self._listening = False
        self._complete = False
        self._timed_phases: set[str] = set()
        self._lock = threading.RLock()

    def _elapsed(self) -> float:
        return self._before_python + self._monotonic() - self._python_started

    def phase(self, name: str, **details: str | float | int | bool) -> None:
        """Append a dated phase without allowing diagnostics to stop startup."""
        with self._lock:
            record = {
                "time": datetime.fromtimestamp(self._wall_clock()).astimezone().isoformat(),
                "pid": os.getpid(),
                "elapsed_seconds": round(self._elapsed(), 3),
                "phase": name,
                **details,
            }
            if self._complete:
                record["after_startup"] = True
            if name == "server_listening":
                self._listening = True
            line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size + len(line) > MAX_LOG_BYTES:
                    self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
                with self.path.open("ab") as log:
                    log.write(line)
            except OSError:
                pass


    @contextmanager
    def timed(self, name: str):
        """Measure a call, excluding optional post-listening work from the summary."""
        with self._lock:
            record = not self._complete or name not in self._timed_phases
            self._timed_phases.add(name)
        if not record:
            yield
            return
        started = self._monotonic()
        background = self._listening and name != "browser_open"
        self.phase(f"{name}_start", background=background)
        error_type = ""
        try:
            yield
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            duration = self._monotonic() - started
            with self._lock:
                if not self._complete and not background and duration > self._slowest[1]:
                    self._slowest = (name, duration)
                self.phase(f"{name}_{'failed' if error_type else 'done'}",
                           duration_seconds=round(duration, 3), background=background,
                           **({"error_type": error_type} if error_type else {}))

    def complete(self, outcome: str = "startup ended before browser launch request") -> None:
        """End the launch entry once, including the slowest measured phase."""
        with self._lock:
            if self._complete:
                return
            date = datetime.fromtimestamp(self._launch_started).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            name, duration = self._slowest
            self.phase("startup_summary", outcome=outcome, slowest_phase=name,
                       slowest_seconds=round(duration, 3),
                       summary=f"Startup {date}: {outcome} after {self._elapsed():.1f}s "
                               f"(slowest phase {name} {duration:.1f}s)")
            self._complete = True


_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
_path = Path(os.environ.get("SKC_STARTUP_LOG", str(_root / "logs" / "startup.log")))
try:
    _launched_at = float(os.environ["SKC_LAUNCH_EPOCH_MS"]) / 1000
except (KeyError, ValueError):
    _launched_at = None
_trace = StartupTrace(_path, launched_at=_launched_at)
phase = _trace.phase
timed = _trace.timed
complete = _trace.complete
