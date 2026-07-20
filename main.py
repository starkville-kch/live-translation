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

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    import threading
    import webbrowser
    import uvicorn
    from app.config import network_cfg
    cfg = network_cfg()
    port = cfg.get("port", 8000)

    if _port_in_use(port):
        url = f"http://localhost:{port}/"
        print()
        print("╔══════════════════════════════════════════════════╗")
        print(f"║  Port {port} is already in use.                   ║")
        print("║                                                  ║")
        print("║  The service may already be running.             ║")
        print(f"║  → Opening browser: {url:<29}║")
        print("║                                                  ║")
        print("║  To restart: close the other console window      ║")
        print("║  (or press Ctrl+C there), then run this again.   ║")
        print("╚══════════════════════════════════════════════════╝")
        print()
        webbrowser.open(url)
        raise SystemExit(0)

    def _open_browser():
        import time; time.sleep(2)
        webbrowser.open(f"http://localhost:{port}/")

    threading.Thread(target=_open_browser, daemon=True).start()

    W = 62  # inner width between the box walls
    def _banner_line(text=""):
        return "║  " + text + " " * (W - 2 - len(text)) + "║"

    print()
    print("╔" + "═" * W + "╗")
    print(_banner_line("Starkville Korean Church  -  Live Translation System"))
    print("╠" + "═" * W + "╣")
    print(_banner_line())
    print(_banner_line(f"Operator console  ->  http://localhost:{port}/"))
    print(_banner_line(f"Attendee page     ->  http://localhost:{port}/live"))
    print(_banner_line())
    print(_banner_line("STEPS TO START SERVICE:"))
    print(_banner_line("  1. Browser opens automatically - wait a moment"))
    print(_banner_line("  2. Select the USB mixer from the Input Device dropdown"))
    print(_banner_line("  3. Press  [Start]  to begin live translation"))
    print(_banner_line())
    print(_banner_line("When the service ends:  press  [Stop]  in the browser,"))
    print(_banner_line("then close this window (or press Ctrl+C here)."))
    print(_banner_line())
    print(_banner_line("Keep this window open for the entire service."))
    print("╚" + "═" * W + "╝")
    print()

    # Pass the app object directly (not a string) so PyInstaller frozen builds work.
    uvicorn.run(app, host=cfg.get("host", "0.0.0.0"), port=port, reload=False, access_log=False)
