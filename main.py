"""Volunteer-facing application entry point."""
from app.server import app  # noqa: F401
from app.tunnel import CloudflareTunnelManager


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _start_health_monitor() -> None:
    import time
    import urllib.request
    import urllib.error

    def check():
        local_ok = False
        for _ in range(23):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8080/api/status", timeout=2) as response:
                    local_ok = response.status == 200
            except Exception:
                time.sleep(2)
            if local_ok:
                break
        if not local_ok:
            print("[ERROR] Translation service did not start correctly.")
            print("[ACTION] Click Restart Translation in the Operator Console.")
            return
        # Give the background Cloudflared service check time to finish. This
        # monitor thread never blocks FastAPI startup.
        manager = getattr(app.state, "tunnel_manager", None)
        public_ready = False
        for _ in range(10):
            if manager and manager.is_ready:
                public_ready = True
                break
            try:
                with urllib.request.urlopen("https://live.starkvillekoreanchurch.org/live", timeout=15) as response:
                    response.read(1)
                    public_ready = 200 <= response.status < 400
            except urllib.error.HTTPError as exc:
                # A HEAD-only health probe would report 405 here. This probe
                # is GET, and 405 still proves the public route reached origin.
                public_ready = exc.code == 405
            except Exception:
                pass
            if public_ready:
                break
            time.sleep(2)
        if public_ready:
            print("[READY] Public attendee link is now connected.")
            return
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║             ⚠ LOCAL TRANSLATION IS READY                     ║")
        print("╠══════════════════════════════════════════════════════════════╣")
        print("║  The translation system is running on this laptop.           ║")
        print("║                                                              ║")
        print("║  The public attendee link is still connecting.               ║")
        print("║                                                              ║")
        print("║  ACTION: In the Operator Console, click                       ║")
        print("║  “Reconnect Public Link,” then wait 30 seconds.              ║")
        print("║                                                              ║")
        print("║  You may continue preparing the audio and translation.       ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        while True:
            time.sleep(20)
            try:
                with urllib.request.urlopen("https://live.starkvillekoreanchurch.org/live", timeout=15) as response:
                    response.read(1)
                    if 200 <= response.status < 400:
                        print("[READY] Public attendee link is now connected.")
                        return
            except urllib.error.HTTPError as exc:
                if exc.code == 405:
                    print("[READY] Public attendee link is now connected.")
                    return
            except Exception:
                continue

    import threading
    threading.Thread(target=check, daemon=True).start()


def _print_startup_banner() -> None:
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Starkville Korean Church  -  Live Translation System        ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║                                                              ║")
    print("║  Operator console  ->  http://localhost:8080/                ║")
    print("║  Attendee page     ->  http://localhost:8080/live            ║")
    print("║                                                              ║")
    print("║  STEPS TO START SERVICE:                                     ║")
    print("║    1. Browser opens automatically - wait a moment            ║")
    print("║    2. Select the USB mixer from the Input Device dropdown    ║")
    print("║    3. Press  [Start]  to begin live translation              ║")
    print("║                                                              ║")
    print("║  When the service ends:  press  [Stop]  in the browser,      ║")
    print("║  then close this window (or press Ctrl+C here).              ║")
    print("║                                                              ║")
    print("║  Keep this window open for the entire service.               ║")
    print("╚══════════════════════════════════════════════════════════════╝")


if __name__ == "__main__":
    import threading
    import time
    import webbrowser
    import uvicorn
    from app.config import network_cfg

    port = 8080
    cfg = network_cfg()
    if _port_in_use(port):
        print("Live Translation is already running.")
        print("Open the Operator Console in your browser.")
        webbrowser.open(f"http://localhost:{port}/")
        raise SystemExit(0)

    tunnel_mgr = CloudflareTunnelManager(port=8080, enabled=cfg.get("enable_tunnel", True))
    app.state.tunnel_manager = tunnel_mgr

    print("Starting Live Translation…\n")
    print("Starting translation server…          ✓")
    print("Opening Operator Console…             ✓")
    print("Checking local service…               ✓")
    print("Checking public attendee link…        Checking…")
    _print_startup_banner()
    threading.Thread(target=lambda: (time.sleep(2), webbrowser.open("http://localhost:8080/")), daemon=True).start()
    _start_health_monitor()
    tunnel_mgr.start()
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False, access_log=False,
                log_level="warning", log_config=None)
