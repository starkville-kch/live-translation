"""
main.py — Application Entry Point
===================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
This file is the single entry point for the live Korean-to-English
translation service. It imports the FastAPI ``app`` object built in
``app/server.py`` so that Uvicorn can discover and serve it.

Usage
-----
  python main.py                   # launch with settings from config.yaml
  uvicorn main:app --reload        # hot-reload for development only

The host/port are read from ``config.yaml`` → ``network`` section.
Do NOT run ``--reload`` in production; the audio capture thread does not
survive hot-reload safely.
"""
from app.server import app  # noqa: F401  — re-exported for `uvicorn main:app`

def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _format_url(host: str, port: int) -> str:
    return f"http://{host}" if port == 80 else f"http://{host}:{port}"


def _base_url(cfg: dict) -> str:
    hostname = (cfg.get("hostname", "") or "").strip()
    port = int(cfg.get("port", 8080))
    if hostname:
        host = hostname if hostname.endswith(".local") else f"{hostname}.local"
        return _format_url(host, port)
    return _format_url("localhost", port)


def _admin_url(cfg: dict) -> str:
    return f"{_base_url(cfg)}/admin"


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # If running as a frozen executable, copy the bundled CHANGELOG.md next to the EXE
    if getattr(sys, "frozen", False):
        import shutil
        from pathlib import Path
        _mei_changelog = Path(sys._MEIPASS) / "CHANGELOG.md"
        _dest_changelog = Path(sys.executable).parent / "CHANGELOG.md"
        if _mei_changelog.exists() and not _dest_changelog.exists():
            try:
                shutil.copy(_mei_changelog, _dest_changelog)
            except Exception:
                pass

    import threading
    import webbrowser
    import uvicorn
    from app.config import network_cfg
    cfg = network_cfg()
    port = cfg.get("port", 8000)

    base_url = _base_url(cfg)
    admin_url = _admin_url(cfg)
    live_url = f"{base_url}/live"
    browser_url = f"{_format_url('localhost', port)}/admin"
    fallback_url = _format_url("192.168.0.169", port)

    if _port_in_use(port):
        print()
        print("╔══════════════════════════════════════════════════╗")
        print(f"║  Port {port} is already in use.                   ║")
        print("║                                                  ║")
        print("║  The service may already be running.             ║")
        print(f"║  → Opening browser: {browser_url:<29}║")
        print("║                                                  ║")
        print("║  To restart: close the other console window      ║")
        print("║  (or press Ctrl+C there), then run this again.   ║")
        print("╚══════════════════════════════════════════════════╝")
        print()
        webbrowser.open(browser_url)
        raise SystemExit(0)

    def _open_browser(url: str):
        import time; time.sleep(2)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, args=(browser_url,), daemon=True).start()

    W = 72

    def _banner_line(text=""):
        return "║  " + text + " " * (W - 2 - len(text)) + "║"

    print()
    print("╔" + "═" * W + "╗")
    print(_banner_line("STARKVILLE KOREAN CHURCH — LIVE TRANSLATION"))
    print("╠" + "═" * W + "╣")
    print(_banner_line("STATUS: Ready"))
    print(_banner_line())
    print(_banner_line("ATTENDEES — Scan the QR code or open:"))
    print(_banner_line(f"  {live_url}"))
    print(_banner_line())
    print(_banner_line("OPERATOR CONSOLE — This laptop only:"))
    print(_banner_line(f"  {admin_url}"))
    print(_banner_line())
    print(_banner_line("FALLBACK — Same church Wi-Fi:"))
    print(_banner_line(f"  {fallback_url}"))
    print(_banner_line())
    print(_banner_line("START SERVICE"))
    print(_banner_line("  1. Open the operator console"))
    print(_banner_line("  2. Select the USB mixer under Input Device"))
    print(_banner_line("  3. Press [Start]"))
    print(_banner_line())
    print(_banner_line("KEEP THIS WINDOW OPEN during the service."))
    print(_banner_line("End of service: press [Stop], then press Ctrl+C here."))
    print("╚" + "═" * W + "╝")
    print()

    # Pass the app object directly (not a string) so PyInstaller frozen builds work.
    uvicorn.run(app, host=cfg.get("host", "0.0.0.0"), port=port, reload=False, access_log=False)
