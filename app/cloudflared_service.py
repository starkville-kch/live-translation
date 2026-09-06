"""Best-effort lifecycle control for the dedicated Windows cloudflared service and embedded on-demand process."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from app.logger import server_log


class CloudflaredService:
    def __init__(self, name: str = "cloudflared") -> None:
        self.name = name
        self._started_by_app = False
        self._warned_unavailable = False
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._atexit_registered = False

    @property
    def is_windows(self) -> bool:
        return sys.platform == "win32"

    @classmethod
    def find_cloudflared_binary(cls) -> Optional[Path]:
        """Locate cloudflared executable in packaged dir, project root, cwd, ProgramData, or PATH."""
        # 1. Frozen sys.executable directory (first priority when running as packaged PyInstaller exe)
        if getattr(sys, "frozen", False):
            exe_dir_cf = Path(sys.executable).parent / "cloudflared.exe"
            if exe_dir_cf.is_file():
                return exe_dir_cf

        # 2. Project root / application directory
        app_dir = Path(__file__).resolve().parent.parent
        app_cf = app_dir / "cloudflared.exe"
        if app_cf.is_file():
            return app_cf

        # 3. Current working directory
        cwd_cf = Path.cwd() / "cloudflared.exe"
        if cwd_cf.is_file():
            return cwd_cf

        # 4. Dedicated Windows directory
        prog_cf = Path("C:/ProgramData/cloudflared/cloudflared.exe")
        if prog_cf.is_file():
            return prog_cf

        # 5. PATH lookup
        which = shutil.which("cloudflared.exe") or shutil.which("cloudflared")
        if which:
            return Path(which)

        return None

    @classmethod
    def get_tunnel_token(cls) -> Optional[str]:
        """Retrieve tunnel token from environment (e.g. .env), or None."""
        token = os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "").strip()
        return token if len(token) > 10 else None

    @classmethod
    def find_tunnel_token_file(cls) -> Optional[Path]:
        """Locate readable tunnel token file in packaged dir, app directory, cwd, or ProgramData."""
        search_dirs: list[Path] = []
        if getattr(sys, "frozen", False):
            search_dirs.append(Path(sys.executable).parent)
        search_dirs.extend([Path(__file__).resolve().parent.parent, Path.cwd()])

        candidates = [d / fname for d in search_dirs for fname in ("token.txt", "token", ".cloudflared_token")]
        candidates.append(Path("C:/ProgramData/cloudflared/token"))

        for cand in candidates:
            if cand.is_file():
                try:
                    with open(cand, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if len(content) > 10:
                        return cand
                except (PermissionError, OSError):
                    server_log.debug("Token candidate %s exists but is not readable", cand)

        return None

    def _sc_exe(self) -> str:
        sc_path = shutil.which("sc.exe") or shutil.which("sc")
        if sc_path:
            return sc_path
        system32_sc = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "sc.exe")
        if os.path.exists(system32_sc):
            return system32_sc
        return "sc.exe"

    def _query(self) -> str:
        # Check active embedded subprocess first
        with self._proc_lock:
            if self._proc is not None:
                if self._proc.poll() is None:
                    return "running"
                exit_code = self._proc.poll()
                server_log.warning("Embedded cloudflared process exited with code %s", exit_code)
                self._proc = None

        if not self.is_windows:
            return "non-windows"

        try:
            sc = self._sc_exe()
            result = subprocess.run(
                [sc, "query", self.name], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5,
            )
            output = (result.stdout + " " + result.stderr).upper()
            if "RUNNING" in output or "STATE              : 4" in output:
                return "running"
            if "STOPPED" in output or "STATE              : 1" in output or "START_PENDING" in output:
                return "stopped"
            if "DOES NOT EXIST" in output or "FAILED 1060" in output or "1060" in output:
                return "missing"
            if result.returncode == 0:
                if "RUNNING" in output:
                    return "running"
                if "STOPPED" in output:
                    return "stopped"
        except Exception:
            server_log.debug("Cloudflared service query via sc.exe failed", exc_info=True)

        # Fallback to PowerShell Get-Service query if sc.exe produced unknown or threw
        try:
            ps_res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", f"(Get-Service -Name '{self.name}' -ErrorAction SilentlyContinue).Status.ToString()"],
                capture_output=True, text=True, timeout=5,
            )
            ps_out = ps_res.stdout.strip().lower()
            if "running" in ps_out:
                return "running"
            if "stopped" in ps_out:
                return "stopped"
            if not ps_out:
                return "missing"
        except Exception:
            pass

        return "unknown"

    def _run(self, action: str) -> bool:
        try:
            sc = self._sc_exe()
            result = subprocess.run(
                [sc, action, self.name], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5,
            )
            server_log.debug("Cloudflared service command completed: %s rc=%s", action, result.returncode)
            return result.returncode == 0
        except Exception:
            server_log.warning("Cloudflared service command failed: %s", action, exc_info=True)
            return False

    def start(self) -> None:
        state = self._query()
        if state == "running":
            self._started_by_app = True
            self._warned_unavailable = False
            server_log.info("Cloudflared service is already running")
            return

        # 1. Attempt to start Windows Service if on Windows
        if self.is_windows:
            if self._run("start"):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if self._query() == "running":
                        self._started_by_app = True
                        self._warned_unavailable = False
                        server_log.info("Cloudflared Windows Service started")
                        print("[READY] Public connection service is running (Windows Service).")
                        return
                    time.sleep(0.25)

        # 2. On-demand embedded subprocess fallback (requires NO administrator privileges)
        cf_bin = self.find_cloudflared_binary()
        env_token = self.get_tunnel_token()
        token_path = self.find_tunnel_token_file() if not env_token else None

        if cf_bin and (env_token or token_path):
            if env_token:
                server_log.info("Starting embedded cloudflared process: %s using token from environment", cf_bin)
                cmd = [str(cf_bin), "tunnel", "run", "--token", env_token]
            else:
                server_log.info("Starting embedded cloudflared process: %s with token file %s", cf_bin, token_path)
                cmd = [str(cf_bin), "tunnel", "run", "--token-file", str(token_path)]
            try:
                flags = subprocess.CREATE_NO_WINDOW if self.is_windows else 0
                log_file = None
                try:
                    log_dir = Path("logs")
                    log_dir.mkdir(parents=True, exist_ok=True)
                    log_file = open(log_dir / "cloudflared.log", "a", encoding="utf-8", errors="replace")
                except Exception:
                    pass

                out_target = log_file if log_file else subprocess.DEVNULL
                with self._proc_lock:
                    self._proc = subprocess.Popen(
                        cmd,
                        stdout=out_target,
                        stderr=out_target,
                        creationflags=flags,
                    )
                time.sleep(1.0)
                with self._proc_lock:
                    if self._proc and self._proc.poll() is None:
                        self._started_by_app = True
                        self._warned_unavailable = False
                        if not self._atexit_registered:
                            import atexit
                            atexit.register(self.stop)
                            self._atexit_registered = True
                        server_log.info("Embedded cloudflared tunnel process is running (PID %s)", self._proc.pid)
                        print(f"[READY] Public connection service is running (Embedded tunnel PID {self._proc.pid}).")
                        return
                    elif self._proc:
                        rc = self._proc.poll()
                        server_log.warning("Embedded cloudflared exited immediately with code %s (Check logs/cloudflared.log)", rc)
                        self._proc = None
            except Exception as e:
                server_log.error("Failed to spawn embedded cloudflared process: %s", e)

        if not self._warned_unavailable:
            server_log.info("Tunnel Down while computer is off is normal.")
            server_log.info("Repair is needed only when the laptop is on, internet is connected, the local app is healthy, and Cloudflared still cannot run/connect.")
            server_log.warning("[WARNING] Public connection service is unavailable.")
            server_log.warning("[ACTION] Local translation is still ready.")
            self._warned_unavailable = True

    def start_background(self) -> None:
        threading.Thread(target=self.start, daemon=True, name="CloudflaredServiceStart").start()

    def stop(self) -> None:
        """Stop embedded cloudflared child process cleanly."""
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                server_log.info("Stopping embedded cloudflared process (PID %s)...", self._proc.pid)
                try:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                except Exception as e:
                    server_log.debug("Error terminating embedded cloudflared: %s", e)
                finally:
                    self._proc = None

