"""Try to get model info for a specific model ID to see if it's accessible."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv()
from google import genai

from google.genai import types as gtypes

for api_ver in ["v1beta", "v1alpha", "v1"]:
    print(f"\n--- {api_ver} ---")
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=gtypes.HttpOptions(api_version=api_ver),
    )
    for name in ["gemini-3.1-flash-live", "gemini-3.1-flash-live-preview"]:
        try:
            m = client.models.get(model=f"models/{name}")
            print(f"OK  {m.name}  supported_actions={getattr(m, 'supported_actions', '?')}")
        except Exception as e:
            short = str(e).split(".")[0]
            print(f"ERR {name}: {short}")
