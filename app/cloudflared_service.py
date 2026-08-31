"""Best-effort lifecycle control for the dedicated Windows cloudflared service."""
from __future__ import annotations

import subprocess
import sys
import threading
import time

from app.logger import server_log


class CloudflaredService:
    def __init__(self, name: str = "cloudflared") -> None:
        self.name = name
        self._started_by_app = False
        self._warned_unavailable = False

    @property
    def is_windows(self) -> bool:
        return sys.platform == "win32"

    def _query(self) -> str:
        if not self.is_windows:
            return "non-windows"
        try:
            result = subprocess.run(
                ["sc", "query", self.name], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=3,
            )
            output = result.stdout.upper()
            if "RUNNING" in output:
                return "running"
            if "STOPPED" in output or "START_PENDING" in output:
                return "stopped"
            if "DOES NOT EXIST" in output or result.returncode:
                return "missing"
        except Exception:
            server_log.debug("Cloudflared service query failed", exc_info=True)
        return "unknown"

    def _run(self, action: str) -> bool:
        try:
            result = subprocess.run(
                ["sc", action, self.name], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=3,
            )
            server_log.debug("Cloudflared service command completed: %s rc=%s", action, result.returncode)
            return result.returncode == 0
        except Exception:
            server_log.warning("Cloudflared service command failed: %s", action, exc_info=True)
            return False

    def start(self) -> None:
        if not self.is_windows:
            return
        state = self._query()
        if state == "running":
            self._started_by_app = True
            self._warned_unavailable = False
            server_log.info("Cloudflared service is running")
            return
        if state != "stopped" or not self._run("start"):
            if not self._warned_unavailable:
                server_log.info("Tunnel Down while computer is off is normal.")
                server_log.info("Repair is needed only when the laptop is on, internet is connected, the local app is healthy, and Cloudflared still cannot run/connect.")
                server_log.warning("[WARNING] Public connection service is unavailable.")
                server_log.warning("[ACTION] Local translation is still ready.")
                self._warned_unavailable = True
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._query() == "running":
                self._started_by_app = True
                self._warned_unavailable = False
                server_log.info("Cloudflared service started")
                print("[READY] Public connection service is running.")
                return
            time.sleep(0.25)
        if not self._warned_unavailable:
            server_log.warning("[WARNING] Public connection service is unavailable.")
            server_log.warning("[ACTION] Local translation is still ready.")
            self._warned_unavailable = True

    def start_background(self) -> None:
        if self.is_windows:
            threading.Thread(target=self.start, daemon=True, name="CloudflaredServiceStart").start()

    def stop(self) -> None:
        """Compatibility no-op: Windows owns service shutdown."""
        return
