"""Best-effort checks for the separately installed Cloudflared Windows service."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from typing import Optional

from app.logger import server_log
from app.cloudflared_service import CloudflaredService

DEFAULT_PUBLIC_URL = "https://live.starkvillekoreanchurch.org/live"


class CloudflareTunnelManager:
    def __init__(
        self,
        port: int = 8080,
        enabled: bool = True,
        public_url: str = DEFAULT_PUBLIC_URL,
    ) -> None:
        self.port = port
        self.enabled = enabled
        self._public_url = (public_url or DEFAULT_PUBLIC_URL).strip()
        if not self._public_url.endswith("/live"):
            self._public_url = f"{self._public_url.rstrip('/')}/live"
        self._tunnel_url: Optional[str] = self._public_url.rsplit("/live", 1)[0]
        self._is_ready = False
        self._status = "reconnecting" if enabled else "unavailable"
        self._error_message: Optional[str] = None
        self._stop = threading.Event()
        self._service = CloudflaredService()
        self._check_now = threading.Event()
        self._last_warning = 0.0
        self._worker_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def tunnel_url(self) -> Optional[str]:
        with self._lock:
            return self._tunnel_url

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._is_ready

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def error_message(self) -> Optional[str]:
        with self._lock:
            return self._error_message

    @property
    def public_attendee_url(self) -> Optional[str]:
        return self._public_url if self.is_ready else None

    def _service_state(self) -> str:
        if sys.platform != "win32":
            return "unavailable"
        try:
            result = subprocess.run(
                ["sc", "query", "cloudflared"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=3,
            )
            output = result.stdout.upper()
            if "RUNNING" in output:
                return "running"
            if "STOPPED" in output or "START_PENDING" in output:
                return "stopped"
            if "FAILED" in output or "DOES NOT EXIST" in output or result.returncode:
                return "missing"
        except Exception:
            server_log.debug("Cloudflared service query failed", exc_info=True)
        return "unavailable"

    def _start_service(self) -> bool:
        try:
            subprocess.run(
                ["sc", "start", "cloudflared"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=3,
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self._service_state() == "running":
                    return True
                time.sleep(0.5)
        except Exception:
            server_log.warning("Cloudflared service start failed", exc_info=True)
        return False

    def _public_check(self) -> bool:
        try:
            with urllib.request.urlopen(self._public_url, timeout=15) as response:
                # GET is intentional: /live is not a HEAD route. Discard the
                # body after following redirects so this remains lightweight.
                response.read(1)
                return 200 <= response.status < 400
        except urllib.error.HTTPError as exc:
            if exc.code == 405:
                server_log.info("Public attendee route reachable; GET health transition required")
                return True
            server_log.debug("Public attendee link check returned HTTP %s", exc.code)
            return False
        except Exception:
            # Windows ships curl.exe; prefer its IPv4/TLS stack when Python's
            # SSL provider cannot validate the Cloudflare edge certificate.
            if sys.platform == "win32":
                try:
                    result = subprocess.run(
                        ["curl.exe", "-4", "-L", "--max-time", "15", "-o", "NUL", "-s", "-w", "%{http_code}", self._public_url],
                        capture_output=True, text=True, timeout=18,
                    )
                    code = int(result.stdout.strip() or "0")
                    return 200 <= code < 400 or code == 405
                except Exception:
                    pass
            server_log.debug("Public attendee link check failed", exc_info=True)
            return False

    def _set_status(self, status: str, ready: bool = False) -> None:
        with self._lock:
            was_ready = self._is_ready
            self._is_ready, self._status = ready, status
            self._error_message = None if ready else "public link unavailable"
        if ready and not was_ready:
            server_log.info("Public attendee link recovered: %s", self._public_url)

    def start(self) -> None:
        if not self.enabled:
            return
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(target=self._run, daemon=True, name="CloudflareHealthWorker")
        self._worker_thread.start()

    def _run(self) -> None:
        first = True
        self._service.start()
        while not self._stop.is_set():
            state = self._service._query()
            if state == "running" and self._public_check():
                self._set_status("available", ready=True)
            else:
                self._set_status("unavailable" if state in {"missing", "unavailable", "stopped"} else "reconnecting")
                # A first probe can race service startup; defer the warning
                # until a later retry so a healthy link is not reported as
                # unavailable merely because it took a few seconds to answer.
                if not first and time.monotonic() - self._last_warning >= 60:
                    server_log.warning("[WARNING] Public attendee link is not ready.")
                    server_log.warning("[ACTION] Local translation is still ready.")
                    self._last_warning = time.monotonic()
            first = False
            self._check_now.wait(30)
            self._check_now.clear()

    def reconnect(self) -> None:
        """Safe operator action: trigger an immediate bounded check."""
        if not self.enabled:
            return
        if not self._worker_thread or not self._worker_thread.is_alive():
            self.start()
        else:
            self._check_now.set()

    def stop(self) -> None:
        self._stop.set()
