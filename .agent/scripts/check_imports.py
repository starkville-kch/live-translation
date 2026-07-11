"""Verify all app modules import cleanly and config is readable."""
import sys
import os

# Run from repo root: python .agent/scripts/check_imports.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.config import gemini_api_key, audio_cfg, network_cfg
from app.audio import list_input_devices, AudioCapture
from app.broadcast import CaptionBroadcaster
from app.gemini_session import GeminiSession, GEMINI_MODEL

print(f"✓ Config loaded")
print(f"✓ API key set: {bool(gemini_api_key())}")
print(f"✓ Model: {GEMINI_MODEL}")
print(f"✓ Input devices found: {len(list_input_devices())}")
for d in list_input_devices():
    print(f"  [{d.index:2d}] {d.name}  ({int(d.default_sample_rate)}Hz, {d.max_input_channels}ch)")
print("All imports OK.")
