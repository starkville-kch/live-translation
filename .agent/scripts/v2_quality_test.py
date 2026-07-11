"""
V2: Translation quality test.
Sends a realistic Korean sermon excerpt, prints Korean source + English translation
side-by-side. Saves results to .agent/scratch/v2_quality_result.txt
"""
import asyncio, os, sys, io, time
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
SCRATCH = os.path.join(os.path.dirname(__file__), "../scratch")
os.makedirs(SCRATCH, exist_ok=True)

# Realistic 3-5 min sermon excerpt covering: scripture, prayer, common phrases
SERMON = (
    "사랑하는 성도 여러분, 오늘 우리는 요한복음 3장 16절 말씀을 함께 나누겠습니다. "
    "하나님이 세상을 이처럼 사랑하사 독생자를 주셨으니, "
    "이는 저를 믿는 자마다 멸망치 않고 영생을 얻게 하려 하심이라. "
    "아멘. "
    "우리 모두 기도하겠습니다. "
    "주님, 오늘 이 시간 우리와 함께 해 주셔서 감사합니다. "
    "우리의 마음을 열어 주시고, 말씀을 받아들일 수 있도록 도와주세요. "
    "예수님의 이름으로 기도합니다. 아멘. "
    "오늘 설교의 제목은 '하나님의 사랑' 입니다. "
    "우리는 종종 세상의 어려움 속에서 하나님의 사랑을 느끼지 못할 때가 있습니다. "
    "하지만 성경은 우리에게 분명히 말씀합니다. "
    "하나님은 우리를 사랑하시며, 결코 우리를 떠나지 않으신다고. "
    "오늘 이 말씀이 여러분의 마음에 위로와 힘이 되기를 바랍니다."
)


def make_pcm(text: str) -> bytes:
    tts = gTTS(text=text, lang="ko", slow=False)
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    buf.seek(0)
    seg = AudioSegment.from_mp3(buf).set_frame_rate(RATE).set_channels(1).set_sample_width(2)
    return seg.raw_data


async def main():
    print("Generating Korean TTS audio...")
    pcm = make_pcm(SERMON)
    duration = len(pcm) / RATE / 2
    print(f"Audio: {duration:.1f}s\n")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        translation_config=types.TranslationConfig(target_language_code="en"),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(),
        ),
    )

    ko_segments = []
    en_segments = []
    latencies = []
    start = time.monotonic()

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        chunk = int(RATE * 0.1) * 2
        for i in range(0, len(pcm), chunk):
            await session.send_realtime_input(
                audio=types.Blob(data=pcm[i:i+chunk], mime_type="audio/pcm;rate=16000")
            )
        await session.send_realtime_input(audio_stream_end=True)

        first_token_at = None
        try:
            async with asyncio.timeout(duration + 30):
                async for r in session.receive():
                    sc = getattr(r, "server_content", None)
                    if sc:
                        it = getattr(sc, "input_transcription", None)
                        if it and getattr(it, "text", None):
                            ko_segments.append(it.text)

                        ot = getattr(sc, "output_transcription", None)
                        if ot and getattr(ot, "text", None):
                            if first_token_at is None:
                                first_token_at = time.monotonic()
                                latencies.append((time.monotonic() - start) * 1000)
                            en_segments.append(ot.text)

                        if getattr(sc, "turn_complete", False):
                            break
        except TimeoutError:
            print("(timeout)")

    ko_full = "".join(ko_segments)
    en_full = "".join(en_segments)

    print("=" * 60)
    print("KOREAN SOURCE:")
    print(ko_full)
    print()
    print("ENGLISH TRANSLATION:")
    print(en_full)
    print("=" * 60)
    print(f"First translation token: {latencies[0]:.0f}ms" if latencies else "No tokens received")
    print(f"Translation segments: {len(en_segments)}")

    result = f"V2 Quality Test\n{'='*60}\nKOREAN:\n{ko_full}\n\nENGLISH:\n{en_full}\n"
    with open(os.path.join(SCRATCH, "v2_quality_result.txt"), "w", encoding="utf-8") as f:
        f.write(result)
    print(f"\nSaved: .agent/scratch/v2_quality_result.txt")

    if en_segments:
        print("\nPASS: translation produced")
    else:
        print("\nFAIL: no translation received")

asyncio.run(main())
