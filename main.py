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
    from app.config import church_cfg, gemini_api_key, get_app_root, network_cfg
    app_root = get_app_root()

    try:
        gemini_api_key()
    except RuntimeError:
        print()
        print("╔══════════════════════════════════════════════════════════════════════╗")
        print("║  API key not found.                                                  ║")
        print("║                                                                      ║")
        print("║  Live Translation has not been configured.                           ║")
        print("║  Opening SKC_setup.exe...                                            ║")
        print("╚══════════════════════════════════════════════════════════════════════╝")
        print()

        setup_exe = app_root / "SKC_setup.exe"
        setup_py = app_root / "setup_gui.py"
        launched = False
        if setup_exe.exists():
            import subprocess
            subprocess.Popen([str(setup_exe)], cwd=str(app_root))
            launched = True
        elif setup_py.exists():
            import subprocess
            subprocess.Popen([sys.executable, str(setup_py)], cwd=str(app_root))
            launched = True

        if not launched:
            print("[ERROR] SKC_setup.exe could not be found.")
            print("Please reinstall or extract the Live Translation package.")
            print()
            input("Press Enter to exit...")
        raise SystemExit(1)

    cfg = network_cfg()
    port = cfg.get("port", 8000)

    from app.tunnel import CloudflareTunnelManager
    tunnel_mgr = CloudflareTunnelManager(
        port=port,
        enabled=cfg.get("enable_tunnel", True),
        public_url=cfg.get("public_url", ""),
    )
    app.state.tunnel_manager = tunnel_mgr
    tunnel_mgr.start()

    base_url = _base_url(cfg)
    admin_url = _admin_url(cfg)
    live_url = f"{base_url}/live"
    public_url = cfg.get("public_url")
    public_live_url = str(public_url).rstrip('/') if public_url else "https://live.starkvillekoreanchurch.org"



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

    ch_name = church_cfg().get("name", "STARKVILLE KOREAN CHURCH").upper()
    banner_title = f"{ch_name} — LIVE TRANSLATION"
    if len(banner_title) > W - 4:
        banner_title = banner_title[:W - 7] + "..."

    print()
    print("╔" + "═" * W + "╗")
    print(_banner_line(banner_title))
    print("╠" + "═" * W + "╣")
    print(_banner_line("STATUS: Ready"))
    print(_banner_line())
    print(_banner_line("ATTENDEES — Public HTTPS (Scan QR or open):"))
    print(_banner_line(f"  {public_live_url}"))
    print(_banner_line())
    print(_banner_line("LOCAL ACCESS — Same church Wi-Fi:"))
    print(_banner_line(f"  {live_url}"))
    print(_banner_line())
    print(_banner_line("OPERATOR CONSOLE — This laptop only:"))
    print(_banner_line(f"  {admin_url}"))
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
    uvicorn.run(
        app,
        host=cfg.get("host", "0.0.0.0"),
        port=port,
        reload=False,
        access_log=False,
        timeout_graceful_shutdown=1,
    )
