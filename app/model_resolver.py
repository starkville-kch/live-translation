"""Manual check of the configured model; no discovery or fallback."""
import asyncio
from typing import Any
from google import genai
from google.genai import types
from app.config import gemini_api_key


async def verify_model_compatibility(
    model_name: str,
    client: genai.Client | None = None,
    api_key: str | None = None,
    voice_name: str = "orus",
) -> tuple[bool, dict[str, Any], str]:
    """Perform a lightweight Live Translate handshake to verify model compatibility."""
    key = api_key or gemini_api_key()
    c = client or genai.Client(api_key=key)

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        translation_config=types.TranslationConfig(
            target_language_code="en",
            echo_target_language=True,
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name),
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    try:
        # Fast handshake timeout (5.0s)
        async with asyncio.timeout(5.0):
            async with c.aio.live.connect(model=model_name, config=config) as _:
                return (
                    True,
                    {
                        "live_connection": True,
                        "translation_config": True,
                        "audio_output": True,
                        "transcription": True,
                    },
                    "Handshake successful — model is Compatible",
                )
    except asyncio.TimeoutError:
        return False, {}, "Connection timed out during handshake"
    except Exception as e:
        err_msg = str(e)
        if key in err_msg:
            err_msg = err_msg.replace(key, "••••••••")
        return False, {}, f"Compatibility handshake failed: {err_msg}"
