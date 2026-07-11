import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from app.gemini_session import _model_rank

models = [
    "gemini-3.5-live-translate-preview",
    "gemini-3.1-flash-live-preview",
    "gemini-2.0-flash-live",
    "gemini-4.0-live-translate-preview",   # hypothetical future
    "gemini-4.0-live-translate",           # hypothetical stable future
]
print("Ranking (best first):")
for m in sorted(models, key=_model_rank, reverse=True):
    print(f"  {_model_rank(m)}  {m}")
