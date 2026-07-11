from google.genai import types
import inspect

print("=== SpeechConfig ===")
print(inspect.signature(types.SpeechConfig))

print("\n=== VoiceConfig ===")
print(inspect.signature(types.VoiceConfig))

print("\n=== PrebuiltVoiceConfig ===")
print(inspect.signature(types.PrebuiltVoiceConfig))
