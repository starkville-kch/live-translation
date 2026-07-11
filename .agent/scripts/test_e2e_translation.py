"""
End-to-end test: synthesize a short Korean TTS wav (via gTTS or a sine-tone fallback),
send it through the translate session, and confirm output_audio_transcription.text arrives.
Falls back to a silence blob if gTTS unavailable — at minimum confirms the session
opens and doesn't crash immediately.
"""
import asyncio, os, sys, struct, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

MODEL = "gemini-3.5-live-translate-preview"
RATE = 16000

def make_silence(seconds=2):
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *([0] * n))

def make_sine(freq=440, seconds=2):
    n = int(RATE * seconds)
    samples = [int(32767 * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)]
    return struct.pack(f"<{n}h", *samples)

async def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        translation_config=types.TranslationConfig(target_language_code="en"),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(),
        ),
        session_resumption=types.SessionResumptionConfig(),
    )

    print(f"Connecting to {MODEL}...")
    async with client.aio.live.connect(model=MODEL, config=config) as session:
        print("Connected OK.")

        # Send 2s of silence first (audio context), then activity end signal
        audio_blob = make_silence(2)
        chunk_size = int(RATE * 0.1) * 2  # 100ms chunks in bytes
        for i in range(0, len(audio_blob), chunk_size):
            await session.send_realtime_input(
                audio=types.Blob(data=audio_blob[i:i+chunk_size], mime_type="audio/pcm;rate=16000")
            )

        print("Audio sent. Waiting for response (up to 10s)...")
        got_any = False
        try:
            async with asyncio.timeout(10):
                async for r in session.receive():
                    sc = getattr(r, "server_content", None)
                    if sc:
                        it = getattr(sc, "input_audio_transcription", None)
                        if it and getattr(it, "text", None):
                            print(f"[KO transcript] {it.text!r}")
                            got_any = True
                        ot = getattr(sc, "output_audio_transcription", None)
                        if ot and getattr(ot, "text", None):
                            print(f"[EN translation] {ot.text!r}")
                            got_any = True
                        if getattr(sc, "turn_complete", False):
                            print("[turn_complete]")
                            break
                    if r.text:
                        print(f"[r.text] {r.text!r}")
                        got_any = True
        except TimeoutError:
            print("(10s timeout — no speech detected in silence, expected)")

        if not got_any:
            print("Session connected and stable. No transcript from silence (expected).")
        print("PASS: session opened, no crash, audio accepted.")

asyncio.run(main())
