import inspect
from google.genai import types

print('=== TranslationConfig ===')
print(inspect.signature(types.TranslationConfig))

print()
print('=== LiveConnectConfig fields ===')
sig = inspect.signature(types.LiveConnectConfig)
for name, param in sig.parameters.items():
    print(f'  {name}: {param.default}')

print()
print('=== AudioTranscriptionConfig ===')
print(inspect.signature(types.AudioTranscriptionConfig))
