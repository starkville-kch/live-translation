"""Explicit, two-request developer evaluation. Never runs after a service.

Prepare creates lossless audio chunks and a request manifest without networking.
Run requires a separately confirmed paid credential. It never retries inference;
the exclusive attempt markers prevent accidentally charging the same run twice.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

from dotenv import dotenv_values
from google import genai
from google.genai import types
from google.genai.interactions import CreateModelInteraction
import httpx
import yaml


MODEL = "gemini-3.5-transcribe"
PROPER_NOUNS = ["스타크빌 한인교회", "카미카", "옥한흠", "John Stott", "Charles Spurgeon"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe(path: Path) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,sample_rate,channels,duration_ts,time_base",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout)


def vocabulary(path: Path) -> list[str]:
    """Include canonical Korean/English terms and variants from the whole archive."""
    terms = list(PROPER_NOUNS)
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    for entries in data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("ko", "en"):
                value = entry.get(key)
                if isinstance(value, str):
                    terms.append(value)
            terms.extend(value for value in entry.get("variants", []) if isinstance(value, str))
    terms = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    if not 0 < len(terms) <= 1000:
        raise ValueError("Custom vocabulary must contain 1 to 1,000 terms")
    return terms


def prepare(source: Path, glossary: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError("Manifest already exists; inspect it rather than overwriting evidence")
    info = probe(source)
    duration = float(info["format"]["duration"])
    if not 3600 < duration <= 7200:
        raise ValueError("This approved evaluation requires exactly two chunks, each at most one hour")
    split = 2700.0
    if duration - split > 3600:
        raise ValueError("The remaining second chunk would exceed one hour")
    chunks = []
    for number, offset in enumerate((0.0, split), 1):
        target = output / f"part-{number}.flac"
        command = ["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(source), "-ss", str(offset)]
        if number == 1:
            command += ["-t", str(split)]
        command += ["-map", "0:a:0", "-vn", "-c:a", "flac", "-sample_fmt", "s16", str(target)]
        subprocess.run(command, check=True)
        chunk_info = probe(target)
        chunks.append({"number": number, "path": str(target), "offset_seconds": offset,
                       "sha256": sha256(target), "probe": chunk_info})
    configuration = {"language_codes": ["ko", "en"], "custom_vocabulary": vocabulary(glossary),
                     "mode": {"type": "verbatim"}}
    # Validate the installed official SDK schema locally, before any upload.
    CreateModelInteraction.model_validate({"model": MODEL, "input": [{"type": "audio",
        "uri": "https://generativelanguage.googleapis.com/v1beta/files/offline-schema-check",
        "mime_type": "audio/flac"}], "generation_config": {"transcription_config": configuration},
        "store": False})
    write_json(manifest_path, {"model": MODEL, "source": str(source), "source_sha256": sha256(source),
        "source_probe": info, "glossary": str(glossary), "glossary_sha256": sha256(glossary),
        "transcription_config": configuration, "chunks": chunks, "inference_requests_allowed": 2,
        "estimated_usd_before_run": duration / 60 * 0.005,
        "audio_note": "Decoded to lossless PCM16 FLAC at original sample rate/channels; no resampling or downmix",
        "timestamps_note": "Only chunk offsets are known; word timestamps are disabled with custom vocabulary"})
    print(json.dumps({"prepared": str(manifest_path), "duration_seconds": duration,
                      "vocabulary_terms": len(configuration["custom_vocabulary"]), "chunks": 2}), flush=True)


def extract_text(response: dict) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    return "\n".join(content["text"] for step in response.get("steps", [])
                     if step.get("type") == "model_output" for content in step.get("content", [])
                     if content.get("type") == "text" and isinstance(content.get("text"), str))


def run(output: Path, env_file: Path, key_name: str, paid_confirmed: bool) -> None:
    if not paid_confirmed:
        raise ValueError("Paid-project provenance must be confirmed before uploading church audio")
    key = dotenv_values(env_file).get(key_name)
    if not key:
        raise ValueError("The explicitly selected credential is missing")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest["model"] != MODEL or len(manifest["chunks"]) != 2:
        raise ValueError("Unexpected evaluation manifest")
    for chunk in manifest["chunks"]:
        if sha256(Path(chunk["path"])) != chunk["sha256"]:
            raise ValueError("Audio hash changed after preparation")
        if (output / f"part-{chunk['number']}.attempt.json").exists():
            raise FileExistsError("An inference was already attempted; do not retry without new approval")
    api = genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=900000, retry_options=types.HttpRetryOptions(attempts=1)))
    results = []
    with httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0), follow_redirects=False) as transport:
        for chunk in manifest["chunks"]:
            number = chunk["number"]
            print(f"Uploading part {number}/2 using confirmed paid credential", flush=True)
            uploaded = api.files.upload(file=chunk["path"], config=types.UploadFileConfig(mime_type="audio/flac"))
            write_json(output / f"part-{number}.upload.json", uploaded.model_dump(mode="json"))
            if not uploaded.name or not uploaded.uri:
                raise RuntimeError("Upload did not return a file name and URI")
            try:
                while uploaded.state and uploaded.state.name == "PROCESSING":
                    time.sleep(2)
                    uploaded = api.files.get(name=uploaded.name)
                if uploaded.state and uploaded.state.name == "FAILED":
                    raise RuntimeError("Uploaded audio processing failed before inference")
                request = {"model": MODEL, "input": [{"type": "audio", "uri": uploaded.uri,
                    "mime_type": "audio/flac"}], "generation_config": {
                    "transcription_config": manifest["transcription_config"]}, "store": False}
                write_json(output / f"part-{number}.request.json", request)
                attempt = output / f"part-{number}.attempt.json"
                with attempt.open("x", encoding="utf-8") as marker:
                    json.dump({"started_at": datetime.now(timezone.utc).isoformat(),
                               "key_source": f"{env_file}:{key_name}", "paid_project_confirmed": True,
                               "automatic_inference_retries": 0}, marker, indent=2)
                print(f"Sending approved inference {number}/2; automatic retries disabled", flush=True)
                started = time.monotonic()
                response = transport.post("https://generativelanguage.googleapis.com/v1beta/interactions",
                    headers={"x-goog-api-key": key}, json=request)
                # Preserve exact response bytes, including error bodies and usage, before parsing.
                (output / f"part-{number}.raw.json").write_bytes(response.content)
                write_json(output / f"part-{number}.http.json", {"status_code": response.status_code,
                    "elapsed_seconds": time.monotonic() - started,
                    "response_headers": {name: value for name, value in response.headers.items()
                        if name.lower() in {"content-type", "x-request-id", "date", "server-timing"}}})
                response.raise_for_status()
                raw = response.json()
                text = extract_text(raw)
                if raw.get("status") != "completed" or not text.strip():
                    raise RuntimeError("Inference did not return a completed nonempty transcript; no retry performed")
                (output / f"part-{number}.txt").write_text(text, encoding="utf-8")
                usage = raw.get("usage", {})
                input_tokens = usage.get("total_input_tokens")
                output_tokens = usage.get("total_output_tokens")
                estimated = ((input_tokens * 2 + output_tokens * 12) / 1_000_000
                             if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None)
                results.append({"part": number, "usage": usage, "published_rate_estimate_usd": estimated,
                                "invoice_verified": False})
                write_json(output / "usage_cost.json", {"parts": results,
                    "published_input_usd_per_million": 2, "published_output_usd_per_million": 12,
                    "total_published_rate_estimate_usd": sum(row["published_rate_estimate_usd"] or 0 for row in results),
                    "invoice_verified": False})
                print(f"Completed part {number}/2; transcript and usage saved", flush=True)
            finally:
                api.files.delete(name=uploaded.name)
                print(f"Removed remote uploaded file for part {number}", flush=True)
    sections = ["# 2026-11-08 오프라인 전사 평가", "",
        "아래 시각은 구간 시작 위치이며 단어별 시각이 아닙니다. 영어 번역은 생성하지 않았습니다.", ""]
    for chunk in manifest["chunks"]:
        number = chunk["number"]
        seconds = int(chunk["offset_seconds"])
        stamp = f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"
        text = (output / f"part-{number}.txt").read_text(encoding="utf-8")
        sections += [f"## {stamp}", "", "**Spoken (ko/en)**", text, "", "**English**", "", "", "---", ""]
    (output / "transcript.md").write_text("\n".join(sections), encoding="utf-8")
    print("Completed one approved evaluation; no more inference calls will run", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--glossary", type=Path, default=Path("docs/archive/glossary_terms.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--key-name", default="GEMINI_API_KEY")
    parser.add_argument("--paid-project-confirmed", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        if not args.source:
            parser.error("prepare requires --source")
        prepare(args.source, args.glossary, args.output)
    else:
        run(args.output, args.env_file, args.key_name, args.paid_project_confirmed)


if __name__ == "__main__":
    main()
