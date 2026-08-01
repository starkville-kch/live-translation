"""
app/tunnel.py — Cloudflare HTTPS Tunnel Manager
================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Manages launching, monitoring, and cleaning up a cloudflared HTTPS tunnel.
Supports both Production Named Tunnels (Windows Service / token / custom domain)
and temporary Quick Tunnels (trycloudflare.com).
"""
import atexit
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Optional

_DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
_URL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


class CloudflareTunnelManager:
    """Manages the cloudflared subprocess lifecycle and exposes tunnel URLs."""

    def __init__(self, port: int = 8080, enabled: bool = True) -> None:
        self.port = port
        self.enabled = enabled
        self._tunnel_url: Optional[str] = None
        self._is_ready: bool = False
        self._error_message: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        if self.enabled:
            atexit.register(self.stop)

    @property
    def deploy_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent
        return Path(__file__).parent.parent

    @property
    def tunnel_url(self) -> Optional[str]:
        with self._lock:
            return self._tunnel_url

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._is_ready

    @property
    def error_message(self) -> Optional[str]:
        with self._lock:
            return self._error_message

    @property
    def public_attendee_url(self) -> Optional[str]:
        with self._lock:
            if self._is_ready and self._tunnel_url:
                return f"{self._tunnel_url}/live"
            return None

    def _resolve_binary(self) -> Optional[Path]:
        local_exe = self.deploy_dir / "cloudflared.exe"
        if local_exe.exists():
            return local_exe

        which_path = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
        if which_path:
            return Path(which_path)

        return None

    def _download_binary(self) -> Optional[Path]:
        target_path = self.deploy_dir / "cloudflared.exe"
        print(f"[INFO] cloudflared.exe not found. Attempting download from:\n       {_DOWNLOAD_URL}")
        try:
            req = urllib.request.Request(
                _DOWNLOAD_URL,
                headers={"User-Agent": "SKC-Live-Translation/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                target_path.write_bytes(data)
            print(f"[INFO] Successfully downloaded cloudflared.exe to:\n       {target_path}")
            return target_path
        except Exception as err:
            err_msg = f"Failed to download cloudflared.exe: {err}"
            with self._lock:
                self._error_message = err_msg
            print(f"\n[WARNING] {err_msg}")
            print("┌────────────────────────────────────────────────────────────────────────┐")
            print("│ MANUALLY INSTALL CLOUDFLARED:                                          │")
            print("│ 1. Download cloudflared-windows-amd64.exe from:                        │")
            print("│    https://github.com/cloudflare/cloudflared/releases/latest          │")
            print("│ 2. Rename to 'cloudflared.exe' and place in directory:                │")
            print(f"│    {str(target_path):<67} │")
            print("└────────────────────────────────────────────────────────────────────────┘\n")
            return None

    def _is_windows_service_running(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            res = subprocess.run(
                ["sc", "query", "cloudflared"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            return "STATE" in res.stdout and "RUNNING" in res.stdout
        except Exception:
            return False

    def start(self) -> None:
        """Launches tunnel resolution in a background thread so app startup is non-blocking."""
        if not self.enabled:
            return

        self._worker_thread = threading.Thread(
            target=self._run_startup_worker,
            daemon=True,
            name="CloudflareTunnelWorker"
        )
        self._worker_thread.start()

    def _run_startup_worker(self) -> None:
        from app.config import network_cfg
        cfg = network_cfg()
        pub_url = cfg.get("public_url") or os.environ.get("CLOUDFLARE_PUBLIC_URL")
        token = os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "").strip()

        # Mode 1: Production Named Tunnel domain (Windows Service running or pre-configured domain)
        if pub_url and (self._is_windows_service_running() or not token):
            clean_url = pub_url.rstrip("/")
            with self._lock:
                self._tunnel_url = clean_url
                self._is_ready = True
                self._error_message = None
            print(f"\n[SUCCESS] Production Named Cloudflare Tunnel active:\n          {clean_url}/live\n")
            return

        # Mode 2: Launch cloudflared.exe process (Token or Quick Tunnel)
        binary = self._resolve_binary()
        if not binary:
            binary = self._download_binary()

        if not binary or not binary.exists():
            print("[INFO] Continuing without Cloudflare HTTPS tunnel (local network access active).")
            return

        if token:
            cmd = [str(binary), "tunnel", "run", "--token", token]
        else:
            cmd = [str(binary), "tunnel", "--url", f"http://127.0.0.1:{self.port}"]

        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            with self._lock:
                self._process = proc

            print(f"[INFO] Started Cloudflare Tunnel process (PID {proc.pid}).")
            if token and pub_url:
                clean_url = pub_url.rstrip("/")
                with self._lock:
                    self._tunnel_url = clean_url
                    self._is_ready = True
                    self._error_message = None
                print(f"\n[SUCCESS] Token-based Cloudflare Tunnel active:\n          {clean_url}/live\n")
            else:
                self._read_stderr(proc)
        except Exception as err:
            err_msg = f"Failed to launch cloudflared process: {err}"
            with self._lock:
                self._error_message = err_msg
            print(f"[WARNING] {err_msg}")

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        if not proc or not proc.stderr:
            return

        stderr_lines = []
        try:
            for line in proc.stderr:
                if not line:
                    continue
                stderr_lines.append(line.strip())
                if len(stderr_lines) > 20:
                    stderr_lines.pop(0)

                match = _URL_REGEX.search(line)
                if match:
                    found_url = match.group(0)
                    with self._lock:
                        if not self._is_ready:
                            self._tunnel_url = found_url
                            self._is_ready = True
                            self._error_message = None
                    print(f"\n[SUCCESS] HTTPS Cloudflare Quick Tunnel established:\n          {found_url}/live\n")
        except Exception as err:
            with self._lock:
                if not self._is_ready:
                    self._error_message = f"Error reading tunnel output: {err}"

        # Capture early process exit
        returncode = proc.poll()
        if returncode is not None and returncode != 0:
            last_err = stderr_lines[-1] if stderr_lines else f"exit code {returncode}"
            err_msg = f"Cloudflare Tunnel process exited prematurely ({last_err})"
            with self._lock:
                if not self._is_ready:
                    self._error_message = err_msg
            print(f"[WARNING] {err_msg}")

    def stop(self) -> None:
        with self._lock:
            proc = self._process
            self._process = None
            self._is_ready = False
            self._tunnel_url = None

        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
                print("[INFO] Cloudflare Tunnel process stopped.")
            except Exception as err:
                print(f"[WARNING] Error stopping cloudflared process: {err}")
