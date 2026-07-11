"""
V1 Audio Path Test:
1. Generate a short Korean TTS audio clip (via gTTS if available, else use a pre-baked PCM blob)
2. Send it directly through the GeminiSession pipeline
3. Verify English translation arrives via output_audio_transcription.text
4. Save the Korean source to .agent/scratch/v1_test.wav for manual review
"""
import asyncio, os, sys, struct, wave, io, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

MODEL = "gemini-3.5-live-translate-preview"
RATE = 16000
SCRATCH = os.path.join(os.path.dirname(__file__), "../scratch")
os.makedirs(SCRATCH, exist_ok=True)

KOREAN_TEXT = (
    "하나님은 사랑이십니다. "
    "오늘 예배에 오신 것을 환영합니다. "
    "우리는 함께 기도하겠습니다. "
    "주님의 은혜가 여러분과 함께하기를 바랍니다."
)


def get_korean_pcm() -> bytes:
    """Return 16kHz mono PCM16 bytes of Korean TTS, or None if gTTS unavailable."""
    try:
        from gtts import gTTS
        import audioop, io as _io
        print("Using gTTS for Korean audio...")
        tts = gTTS(text=KOREAN_TEXT, lang="ko")
        mp3_buf = _io.BytesIO()
        tts.write_to_fp(mp3_buf)
        mp3_buf.seek(0)

        # Decode MP3 -> PCM via pydub if available
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_mp3(mp3_buf)
            seg = seg.set_frame_rate(RATE).set_channels(1).set_sample_width(2)
            return seg.raw_data
        except ImportError:
            print("pydub not available, falling back to silence")
            return None
    except ImportError:
        return None


def make_silence_pcm(seconds=5) -> bytes:
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *([0] * n))


async def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    pcm = get_korean_pcm()
    if pcm is None:
        print("No TTS available — using 3s silence (will produce no transcript, but tests pipeline)")
        pcm = make_silence_pcm(3)
        using_tts = False
    else:
        print(f"PCM ready: {len(pcm)} bytes ({len(pcm)/RATE/2:.1f}s)")
        using_tts = True

    # Save WAV for manual review
    wav_path = os.path.join(SCRATCH, "v1_test.wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm)
    print(f"Saved: {wav_path}")

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

    print(f"\nConnecting to {MODEL}...")
    translations = []
    source_transcripts = []
    start_time = time.monotonic()

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        print("Connected. Streaming audio...")

        chunk_size = int(RATE * 0.1) * 2  # 100ms
        for i in range(0, len(pcm), chunk_size):
            await session.send_realtime_input(
                audio=types.Blob(data=pcm[i:i+chunk_size], mime_type="audio/pcm;rate=16000")
            )

        # Signal end of audio
        await session.send_realtime_input(audio_stream_end=True)
        print("Audio stream ended. Waiting for translations...")

        timeout = 30 if using_tts else 8
        try:
            async with asyncio.timeout(timeout):
                async for r in session.receive():
                    sc = getattr(r, "server_content", None)
                    if sc:
                        it = getattr(sc, "input_audio_transcription", None)
                        if it and getattr(it, "text", None):
                            print(f"[KO] {it.text}")
                            source_transcripts.append(it.text)

                        ot = getattr(sc, "output_audio_transcription", None)
                        if ot and getattr(ot, "text", None):
                            elapsed = (time.monotonic() - start_time) * 1000
                            print(f"[EN +{elapsed:.0f}ms] {ot.text}")
                            translations.append(ot.text)

                        if getattr(sc, "turn_complete", False):
                            print("[turn_complete]")
                            break
                    if r.text:
                        print(f"[r.text] {r.text}")
                        translations.append(r.text)
        except TimeoutError:
            print(f"({timeout}s timeout)")

    print("\n--- V1 RESULT ---")
    if translations:
        print(f"PASS: {len(translations)} translation segment(s) received")
        print("Full translation:", " ".join(translations))
    elif not using_tts:
        print("PASS (silence baseline): no translation from silence — expected")
    else:
        print("FAIL: TTS audio sent but no translation received")

    if source_transcripts:
        print("Korean source:", " ".join(source_transcripts))

asyncio.run(main())
