"""Entry point: uvicorn main:app"""
from app.server import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn
    from app.config import network_cfg
    cfg = network_cfg()
    uvicorn.run("main:app", host=cfg.get("host", "0.0.0.0"), port=cfg.get("port", 8000), reload=False)
