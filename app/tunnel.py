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

DEFAULT_PUBLIC_URL = "https://live.starkvillekoreanchurch.org"


class CloudflareTunnelManager:
    def __init__(
        self,
        port: int = 8080,
        enabled: bool = True,
        public_url: str = DEFAULT_PUBLIC_URL,
    ) -> None:
        self.port = port
        self.enabled = enabled
        self._public_url = (public_url or DEFAULT_PUBLIC_URL).strip().rstrip("/")
        self._tunnel_url: Optional[str] = self._public_url

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
        return self._service._query()

    def _start_service(self) -> bool:
        return self._service.start() or self._service._query() == "running"

    def _public_check(self) -> bool:
        # Probe using standard browser User-Agent so Cloudflare WAF bot management doesn't block with 403
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            req = urllib.request.Request(self._public_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read(1)
                return 200 <= response.status < 400
        except urllib.error.HTTPError as exc:
            if exc.code == 405 or (200 <= exc.code < 400):
                return True
            server_log.debug("Public attendee link check returned HTTP %s", exc.code)
        except Exception:
            pass

        # Windows curl fallback with User-Agent
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["curl.exe", "-4", "-L", "-A", "Mozilla/5.0", "--max-time", "10", "-o", "NUL", "-s", "-w", "%{http_code}", self._public_url],
                    capture_output=True, text=True, timeout=12,
                )
                code = int(result.stdout.strip() or "0")
                return 200 <= code < 400 or code == 405
            except Exception:
                server_log.debug("Public attendee curl check failed", exc_info=True)

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
