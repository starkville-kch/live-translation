"""
Broader sweep: test every model that has bidiGenerateContent support for TEXT modality.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

async def test(client, model_name):
    config = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        system_instruction="Reply with exactly: hello",
    )
    try:
        async with client.aio.live.connect(model=model_name, config=config) as session:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text="hello")])
            )
            reply = ""
            async for r in session.receive():
                if r.text:
                    reply += r.text
                if hasattr(r, "server_content") and r.server_content and getattr(r.server_content, "turn_complete", False):
                    break
            print(f"✓ TEXT OK  {model_name}: '{reply.strip()[:60]}'")
            return True
    except Exception as e:
        short = str(e)[:100]
        print(f"✗ {model_name}: {short}")
        return False

async def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # List all models supporting bidiGenerateContent
    candidates = []
    for m in client.models.list():
        actions = getattr(m, "supported_actions", []) or []
        if "bidiGenerateContent" in actions:
            candidates.append(m.name.removeprefix("models/"))

    print(f"Models with bidiGenerateContent: {candidates}\n")

    for name in candidates:
        await test(client, name)

asyncio.run(main())
