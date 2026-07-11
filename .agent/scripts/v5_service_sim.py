"""
V5: Full service simulation.
- Streams looping Korean audio for 15 minutes (enough to cross the 10-min WebSocket boundary)
- Monitors session status for unexpected FAILED states
- Counts reconnects (routine ones are expected; FAILED is not)
- Checks logs were written
- Reports summary
"""
import asyncio, os, sys, io, time, json, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv()

from app.gemini_session import GeminiSession, SessionStatus
from gtts import gTTS
from pydub import AudioSegment

RATE = 16000
SIM_MINUTES = 15
LOG_PATH = os.path.join(os.path.dirname(__file__), "../../logs/translation.log")

SERMON_LOOP = (
    "하나님은 사랑이십니다. "
    "예수님은 우리의 구원자이십니다. "
    "성령님은 우리와 함께 하십니다. "
    "오늘 우리는 하나님의 말씀을 함께 나눕니다. "
)

def make_pcm(text):
    tts = gTTS(text=text, lang="ko")
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    buf.seek(0)
    seg = AudioSegment.from_mp3(buf).set_frame_rate(RATE).set_channels(1).set_sample_width(2)
    return seg.raw_data

async def main():
    print(f"V5: {SIM_MINUTES}-minute service simulation")
    print("Generating loop audio...")
    pcm_loop = make_pcm(SERMON_LOOP)
    loop_duration = len(pcm_loop) / RATE / 2
    print(f"Loop: {loop_duration:.1f}s, will repeat ~{SIM_MINUTES*60/loop_duration:.0f}x\n")

    captions_received = []
    state_history = []
    start_time = time.monotonic()

    def on_caption(text):
        captions_received.append((time.monotonic() - start_time, text))

    def on_state(s):
        state_history.append((time.monotonic() - start_time, s.status, s.reconnect_count, s.last_event))
        elapsed = time.monotonic() - start_time
        print(f"  [{elapsed/60:.1f}m] {s.status} rc={s.reconnect_count} — {s.last_event}")

    session = GeminiSession(on_caption=on_caption, on_state_change=on_state)
    await session.start()
    await asyncio.sleep(3)

    end_time = start_time + SIM_MINUTES * 60
    chunk = int(RATE * 0.1) * 2
    loop_count = 0

    print(f"Streaming audio for {SIM_MINUTES} minutes...")
    while time.monotonic() < end_time:
        loop_count += 1
        elapsed = time.monotonic() - start_time
        if loop_count % 5 == 0:
            s = session.state
            print(f"  [{elapsed/60:.1f}m] loop#{loop_count} status={s.status} captions={len(captions_received)} reconnects={s.reconnect_count}")

        for i in range(0, len(pcm_loop), chunk):
            if time.monotonic() >= end_time:
                break
            await session.send_audio(pcm_loop[i:i+chunk])
            await asyncio.sleep(0.095)  # ~real-time pacing

    await asyncio.sleep(3)
    final = session.state
    await session.stop()

    elapsed_total = time.monotonic() - start_time

    # Check log file
    log_exists = os.path.exists(LOG_PATH)
    log_size = os.path.getsize(LOG_PATH) if log_exists else 0

    print(f"\n--- V5 RESULTS ---")
    print(f"Run time: {elapsed_total/60:.1f} minutes")
    print(f"Total captions: {len(captions_received)}")
    print(f"Session reconnects: {final.reconnect_count}")
    print(f"Final status: {final.status}")
    print(f"Log file: {'exists' if log_exists else 'MISSING'} ({log_size} bytes)")

    failed_states = [s for _, s, _, _ in state_history if s == SessionStatus.FAILED]
    reconnects = max((rc for _, _, rc, _ in state_history), default=0)

    # Check GoAway reconnections (routine)
    goaway_events = [e for _, _, _, e in state_history if "GoAway" in e or "Reconnecting" in e]
    print(f"GoAway/reconnect events: {len(goaway_events)}")

    if failed_states:
        print("FAIL: session entered FAILED state")
    elif len(captions_received) == 0:
        print("FAIL: no captions received")
    elif not log_exists:
        print("FAIL: no log file written")
    else:
        print(f"\nPASS: {SIM_MINUTES}-minute run completed")
        print(f"  Captions: {len(captions_received)}")
        print(f"  Reconnects (routine): {reconnects}")
        if goaway_events:
            print(f"  Crossed 10-min boundary: YES ({len(goaway_events)} GoAway events)")
        else:
            print(f"  Note: no GoAway in {SIM_MINUTES}min (GoAway expected at ~10min in real service)")

asyncio.run(main())
