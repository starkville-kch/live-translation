"""
Test gemini-3.5-live-translate-preview with translation_config.
Text translation arrives via output_transcription.text — no audio billing.
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

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        translation_config=types.TranslationConfig(
            target_language_code="en",
        ),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    print(f"Connecting to {MODEL} with translation_config...")
    try:
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            print("Connected.")
            await session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text="하나님은 사랑이십니다. 오늘 예배에 오신 것을 환영합니다.")]
                )
            )
            print("Sent test Korean text. Waiting for response...")
            async for r in session.receive():
                sc = getattr(r, "server_content", None)
                if sc:
                    ot = getattr(sc, "output_transcription", None)
                    if ot and getattr(ot, "text", None):
                        print(f"output_transcription.text: {ot.text!r}")
                    if getattr(sc, "turn_complete", False):
                        print("turn_complete")
                        break
                if r.text:
                    print(f"r.text: {r.text!r}")
    except Exception as e:
        print(f"ERROR: {e}")

asyncio.run(main())
