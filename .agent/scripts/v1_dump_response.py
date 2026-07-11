"""Dump translate model response structure — ASCII-safe output."""
import asyncio, os, sys, io, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv()

from gtts import gTTS
from pydub import AudioSegment
from google import genai
from google.genai import types

MODEL = "gemini-3.5-live-translate-preview"
RATE = 16000
KOREAN_TEXT = "하나님은 사랑이십니다. 오늘 예배에 오신 것을 환영합니다."

def get_pcm():
    tts = gTTS(text=KOREAN_TEXT, lang="ko")
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    buf.seek(0)
    seg = AudioSegment.from_mp3(buf).set_frame_rate(RATE).set_channels(1).set_sample_width(2)
    return seg.raw_data

async def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    pcm = get_pcm()
    print(f"PCM: {len(pcm)/RATE/2:.1f}s")

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        translation_config=types.TranslationConfig(target_language_code="en"),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(),
        ),
    )

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        chunk = int(RATE * 0.1) * 2
        for i in range(0, len(pcm), chunk):
            await session.send_realtime_input(
                audio=types.Blob(data=pcm[i:i+chunk], mime_type="audio/pcm;rate=16000")
            )
        await session.send_realtime_input(audio_stream_end=True)
        print("Audio sent.\n")

        count = 0
        try:
            async with asyncio.timeout(25):
                async for r in session.receive():
                    count += 1

                    # Check all known text fields
                    if r.text:
                        print(f"[#{count}] r.text = {r.text!r}")

                    sc = getattr(r, "server_content", None)
                    if sc:
                        # input transcription (Korean source)
                        it = getattr(sc, "input_transcription", None) or getattr(sc, "input_audio_transcription", None)
                        if it:
                            t = getattr(it, "text", None)
                            if t: print(f"[#{count}] input_transcription.text = {t!r}")

                        # output transcription (English translation)
                        ot = getattr(sc, "output_transcription", None) or getattr(sc, "output_audio_transcription", None)
                        if ot:
                            t = getattr(ot, "text", None)
                            if t: print(f"[#{count}] output_transcription.text = {t!r}")

                        # model_turn parts
                        mt = getattr(sc, "model_turn", None)
                        if mt:
                            for p in (getattr(mt, "parts", None) or []):
                                if getattr(p, "text", None):
                                    print(f"[#{count}] model_turn.part.text = {p.text!r}")
                                if getattr(p, "inline_data", None):
                                    d = p.inline_data
                                    print(f"[#{count}] model_turn.part.inline_data mime={getattr(d,'mime_type','?')} len={len(getattr(d,'data',b''))}")

                        if getattr(sc, "turn_complete", False):
                            print(f"[#{count}] turn_complete")
                            break

                    # Print the full JSON for first 3 responses
                    if count <= 3:
                        try:
                            raw = r.model_dump(exclude_none=True)
                            print(f"  full dump: {json.dumps(raw, default=str, ensure_ascii=False)[:500]}")
                        except Exception as e:
                            print(f"  dump error: {e}")
                    print()
        except TimeoutError:
            print(f"(timeout, {count} responses)")

asyncio.run(main())
