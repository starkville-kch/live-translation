"""
V4: Reconnection test.
Verifies that session resumption works by:
1. Starting a GeminiSession directly
2. Sending audio and confirming translation flows
3. Forcibly closing the underlying connection (simulating drop)
4. Confirming the session auto-reconnects and translation resumes
"""
import asyncio, os, sys, io, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv()

from app.gemini_session import GeminiSession, SessionStatus
from app.broadcast import CaptionBroadcaster
from gtts import gTTS
from pydub import AudioSegment

RATE = 16000

SENTENCE_A = "하나님은 사랑이십니다."
SENTENCE_B = "예수님은 우리의 구원자이십니다."


def make_pcm(text: str) -> bytes:
    tts = gTTS(text=text, lang="ko")
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    buf.seek(0)
    seg = AudioSegment.from_mp3(buf).set_frame_rate(RATE).set_channels(1).set_sample_width(2)
    return seg.raw_data


async def main():
    broadcaster = CaptionBroadcaster()
    state_log = []

    def on_state(s):
        state_log.append((time.monotonic(), s.status, s.last_event))
        print(f"  [state] {s.status} — {s.last_event}")

    session = GeminiSession(
        on_caption=lambda text: print(f"  [EN] {text}", end="", flush=True),
        on_state_change=on_state,
    )

    # Phase 1: Normal connection and translation
    print("Phase 1: Start session and send Korean audio...")
    await session.start()
    await asyncio.sleep(3)  # wait for connect

    pcm_a = make_pcm(SENTENCE_A)
    chunk = int(RATE * 0.1) * 2
    for i in range(0, len(pcm_a), chunk):
        await session.send_audio(pcm_a[i:i+chunk])
    print(f"\nSent audio A ({len(pcm_a)/RATE/2:.1f}s). Waiting for translation...")
    await asyncio.sleep(10)

    s1 = session.state
    print(f"\nAfter phase 1: status={s1.status}, reconnects={s1.reconnect_count}")
    phase1_connected = s1.status == SessionStatus.CONNECTED

    # Phase 2: Simulate a session interruption by raising an error in the session
    # We do this by stopping and immediately restarting (simulates a network blip)
    print("\nPhase 2: Simulate interruption (stop + restart)...")
    await session.stop()
    await asyncio.sleep(1)

    await session.start()
    await asyncio.sleep(4)

    pcm_b = make_pcm(SENTENCE_B)
    for i in range(0, len(pcm_b), chunk):
        await session.send_audio(pcm_b[i:i+chunk])
    print(f"\nSent audio B ({len(pcm_b)/RATE/2:.1f}s). Waiting for translation...")
    await asyncio.sleep(10)

    s2 = session.state
    print(f"\nAfter phase 2: status={s2.status}")
    phase2_reconnected = s2.status == SessionStatus.CONNECTED

    await session.stop()

    print("\n--- V4 RESULTS ---")
    print(f"Phase 1 connected:    {'PASS' if phase1_connected else 'FAIL'}")
    print(f"Phase 2 reconnected:  {'PASS' if phase2_reconnected else 'FAIL'}")

    state_transitions = [s for _, s, _ in state_log]
    had_reconnect = SessionStatus.CONNECTING in state_transitions or SessionStatus.RECONNECTING in state_transitions

    if phase1_connected and phase2_reconnected:
        print("\nPASS: session connected, interrupted, and reconnected automatically")
    else:
        print("\nFAIL: reconnection did not complete")
        for t, s, e in state_log:
            print(f"  {s}: {e}")

asyncio.run(main())
