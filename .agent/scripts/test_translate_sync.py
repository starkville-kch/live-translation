"""Synchronous test of gemini-3.5-live-translate-preview TEXT modality."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

MODEL = "gemini-3.5-live-translate-preview"
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# Model metadata
try:
    m = client.models.get(model=f"models/{MODEL}")
    print(f"name: {m.name}")
    print(f"supported_actions: {getattr(m, 'supported_actions', '?')}")
except Exception as e:
    print(f"get failed: {e}")

# Try TEXT config synchronously (will error immediately if unsupported)
print("\nAttempting TEXT modality connect...")
try:
    config = types.LiveConnectConfig(response_modalities=["TEXT"])
    # Just try to construct the session — the 1007 fires at connect time
    import asyncio

    async def _try():
        async with client.aio.live.connect(model=MODEL, config=config) as s:
            print("Connected OK with TEXT")
            await s.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text="안녕")])
            )
            async for r in s.receive():
                if r.text:
                    print(f"text: {r.text!r}")
                if hasattr(r, "server_content") and r.server_content:
                    if getattr(r.server_content, "turn_complete", False):
                        break

    asyncio.run(_try())
except Exception as e:
    print(f"TEXT failed: {e}")
