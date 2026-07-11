import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv()
from google import genai
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
for m in client.models.list():
    name = m.name.lower()
    if "live" in name or ("flash" in name and "2.0" in name):
        print(m.name)
