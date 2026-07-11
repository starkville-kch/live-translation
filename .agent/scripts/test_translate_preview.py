"""
Test gemini-3.5-live-translate-preview with TEXT response modality.
The spec said this model only supports AUDIO — verify that empirically.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

MODEL = "gemini-3.5-live-translate-preview"

async def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # First: what does the model metadata say?
    try:
        m = client.models.get(model=f"models/{MODEL}")
        print(f"Model info: {m.name}")
        print(f"  supported_actions: {getattr(m, 'supported_actions', '?')}")
        print(f"  supported_generation_methods: {getattr(m, 'supported_generation_methods', '?')}")
        print(f"  description: {getattr(m, 'description', '?')[:200]}")
    except Exception as e:
        print(f"get model info failed: {e}")

    # Try TEXT modality
    print("\nTrying TEXT modality...")
    config_text = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        system_instruction="Translate Korean to English. Output only English text.",
    )
    try:
        async with client.aio.live.connect(model=MODEL, config=config_text) as session:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text="안녕하세요")])
            )
            reply = ""
            async for r in session.receive():
                if r.text:
                    reply += r.text
                if hasattr(r, "server_content") and r.server_content and getattr(r.server_content, "turn_complete", False):
                    break
            print(f"✓ TEXT works: '{reply.strip()}'")
    except Exception as e:
        print(f"✗ TEXT failed: {e}")

    # Try AUDIO modality just to confirm it works (and understand the model)
    print("\nTrying AUDIO modality...")
    config_audio = types.LiveConnectConfig(response_modalities=["AUDIO"])
    try:
        async with client.aio.live.connect(model=MODEL, config=config_audio) as session:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text="안녕하세요")])
            )
            got_audio = False
            async for r in session.receive():
                if hasattr(r, "data") and r.data:
                    got_audio = True
                    break
                if hasattr(r, "server_content") and r.server_content and getattr(r.server_content, "turn_complete", False):
                    break
            print(f"✓ AUDIO works: got_audio_data={got_audio}")
    except Exception as e:
        print(f"✗ AUDIO failed: {e}")

asyncio.run(main())
