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

if __name__ == "__main__":
    import uvicorn
    from app.config import network_cfg
    cfg = network_cfg()
    # Bind to all interfaces (0.0.0.0) so phones on the same WiFi can reach
    # the caption and audio endpoints without extra firewall rules.
    uvicorn.run("main:app", host=cfg.get("host", "0.0.0.0"), port=cfg.get("port", 8000), reload=False, access_log=False)
