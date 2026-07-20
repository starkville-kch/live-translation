# Single Executable Build Log
### Live Translation System

> **Korean version**: [BUILD_EXE.ko.md](BUILD_EXE.ko.md)

Documents what was attempted, failed, and succeeded when building a single `.exe`. Reference this before rebuilding.

---

## 📌 Table of Contents
1. [Goal](#goal)
2. [Attempt 1 — 3 GB exe (failed)](#attempt-1--3-gb-exe-failed)
3. [Attempt 2 — scipy missing (failed)](#attempt-2--scipy-missing-failed)
4. [Attempt 3 — unittest missing (failed)](#attempt-3--unittest-missing-failed)
5. [Attempt 4 — uvicorn module import failed (failed)](#attempt-4--uvicorn-module-import-failed-failed)
6. [Attempt 5 — config.yaml not found (test mistake)](#attempt-5--configyaml-not-found-test-mistake)
7. [Attempt 6 — Full success ✅](#attempt-6--full-success-)
8. [Code changes for frozen exe compatibility](#code-changes-for-frozen-exe-compatibility)
9. [PyInstaller spec key decisions](#pyinstaller-spec-key-decisions)
10. [How to rebuild](#how-to-rebuild)
11. [skc_build environment — required packages](#skc_build-environment--required-packages)

---

## Goal

Package the entire Python server (`main.py` + `app/`) into a single `.exe` that a volunteer can run by double-clicking — no Python, no conda, no terminal setup required.

**Deployment target: 3 files in one folder**
- `SKC_translation.exe` — the binary (~70 MB)
- `config.yaml` — editable: `device_index`, `port`, `model`
- `.env` — editable: `GEMINI_API_KEY=...`

Tool: **PyInstaller** (`pyinstaller SKC_translation.spec`)

---

## Attempt 1 — 3 GB exe (failed)

**Command:**
```bat
conda run -n agent pyinstaller SKC_translation.spec --onefile
```

**Result:** exe was ~3 GB. Technically worked but unusable for distribution.

**Root cause:** The `agent` environment contains PyTorch with CUDA support (~2.5 GB). PyInstaller bundles every package it can find in the active environment, including ones the app never imports.

**Fix:** Create a minimal build environment containing only what the app actually needs.

```bat
conda create -n skc_build python=3.11 --yes
conda run -n skc_build pip install google-genai fastapi "uvicorn[standard]" pyaudio numpy ^
    python-dotenv pyyaml "qrcode[pil]" Pillow sse-starlette pyinstaller
```

---

## Attempt 2 — scipy missing (failed)

**Result:** Built successfully at ~40 MB. On launch:
```
ModuleNotFoundError: No module named 'scipy'
```

**Root cause:** `app/audio.py` imports `from scipy import signal` for the Butterworth anti-aliasing filter in the resampler. `scipy` was not in the initial `skc_build` install list and was also explicitly listed in the spec's `excludes` — doubly excluded.

**Fix:**
```bat
conda run -n skc_build pip install scipy
```
In `SKC_translation.spec`:
- Remove `"scipy"` from `excludes`
- Add to `hiddenimports`: `"scipy"`, `"scipy.signal"`, `"scipy.signal._upfirdn"`, `"scipy.signal._upfirdn_apply"`

---

## Attempt 3 — unittest missing (failed)

**Result:** Built ~70 MB. On launch:
```
ModuleNotFoundError: No module named 'unittest'
```

**Root cause:** The spec's `excludes` contained `"test"` and `"unittest"`. These seemed safe to exclude (standard library test modules), but scipy pulls them in transitively:

```
scipy.signal → scipy._lib._array_api → scipy._lib.array_api_compat.numpy
    → numpy.testing → unittest
```

**Fix:** Remove `"test"` and `"unittest"` from `excludes` in the spec.

---

## Attempt 4 — uvicorn module import failed (failed)

**Result:** Server started, API key resolved, glossary loaded — then:
```
ERROR: Error loading ASGI app. Could not import module "main"
```

**Root cause:** `main.py` originally used a string-based import:
```python
uvicorn.run("main:app", ...)
```
In a normal Python environment, uvicorn imports the `main` module by name from `sys.path`. In a PyInstaller frozen exe, there is no `main.py` on disk — everything is bundled inside the binary. The string `"main:app"` fails because uvicorn can't find a file to import.

**Fix:** Pass the app object directly instead of a string:
```python
uvicorn.run(app, ...)  # works frozen and unfrozen
```

---

## Attempt 5 — config.yaml not found (test mistake)

**Result:** uvicorn bound to port, but:
```
FileNotFoundError: No such file or directory: '.../dist/config.yaml'
```

**Root cause:** `app/config.py` already had the `sys.frozen` check to look for `config.yaml` next to the exe. But the test was run from `dist/` without copying `config.yaml` there first. This was a test setup mistake, not a code bug.

**Fix:** Copy `config.yaml` and `.env` into the same folder as the exe before running.

---

## Attempt 6 — Server working, logo missing

**Result:** Server starts, browser opens, operator UI responds HTTP 200. But the PCA logo in the header was missing (`/logo.webp` returned HTTP 404).

**Root cause:** `app/server.py` resolves the logo via `Path(__file__).parent / "pca-logo-white-small.webp"`. Inside a frozen exe `__file__` correctly points to `_MEIPASS/app/server.py`, so the path logic is right. The problem was that `pca-logo-white-small.webp` was never listed in the spec's `datas` — it simply wasn't bundled.

**Fix:** One line added to `SKC_translation.spec`:
```python
datas += [("app/pca-logo-white-small.webp", "app")]
```

---

## Attempt 7 — Full success ✅

**Result:** Server starts, browser opens automatically, operator UI responds HTTP 200, logo displays correctly.

Console output on successful launch:
```
INFO  httpx     HTTP Request: GET .../v1beta/models "HTTP/1.1 200 OK"
INFO  session   Auto-selected Gemini model: gemini-3.5-live-translate-preview
INFO  ops       Glossary loaded: 14 direct entries, 5 review-only
INFO            Started server process [...]
INFO            Uvicorn running on http://0.0.0.0:8001
```
Browser opens to `http://localhost:8001/` after 2 seconds.

---

## Code changes for frozen exe compatibility

### `app/config.py` — path resolution

```python
# __file__ points inside PyInstaller's temp extraction folder when frozen.
# User-editable files (config.yaml, .env) must live next to the exe.
_ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"
load_dotenv(_ROOT / ".env")
```

### `app/logger.py` — log directory

```python
import sys as _sys
_log_base = Path(_sys.executable).parent if getattr(_sys, "frozen", False) else Path(".")
_log_dir  = _log_base / _cfg.get("log_dir", "logs")
```

Without this, logs would be written inside the temp extraction folder (`%TEMP%\_MEIxxxxxx\logs\`) and lost when the exe exits.

### `main.py` — uvicorn app reference

```python
uvicorn.run(app, ...)        # correct — works frozen and unfrozen
uvicorn.run("main:app", ...) # broken when frozen
```

### `main.py` — port conflict detection

Added before uvicorn starts:
```python
def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0
```
If the port is taken, the exe prints a message, opens the browser to the running service, and exits cleanly instead of crashing with a raw OS error.

### `main.py` — automatic browser open

```python
def _open_browser():
    import time; time.sleep(2)
    webbrowser.open(f"http://localhost:{port}/")

threading.Thread(target=_open_browser, daemon=True).start()
```
The 2-second delay prevents the browser from requesting the page before uvicorn finishes binding.  
The duplicate browser-open logic in `SKC_start.bat` was removed after this was added.

---

## PyInstaller spec key decisions

### Why `hiddenimports` is needed

PyInstaller uses static analysis to find imports. It misses:
- Packages loaded dynamically at runtime (uvicorn's protocol/loop backends)
- Packages that `collect_submodules()` doesn't fully enumerate
- `scipy`'s internal extension modules

### Why `collect_data_files()` is needed

Some packages ship non-Python files (CA certificates, proto definitions, templates) that PyInstaller won't copy unless told to. Packages requiring this: `google.genai`, `google.api_core`, `google.auth`, `grpc`, `certifi`, `sse_starlette`.

### Why `console=True`

The exe keeps a console window open. This is intentional — operators can see live translation logs, session state, errors, and reconnect events during the service.

### Why `upx=False`

UPX compresses exe files but can trigger Windows Defender false positives on machines without established trust. Skipped to avoid friction on first run at the church.

### PyAudio binary

conda's PyAudio build statically links PortAudio into `_portaudio.cp311-win_amd64.pyd`. There is no separate `portaudio_x64.dll` needed. The `.pyd` is included explicitly in `binaries` in the spec.

---

## How to rebuild

```bat
build_exe.bat
```

Or manually:
```bat
conda run -n skc_build pyinstaller SKC_translation.spec --noconfirm ^
    --workpath .agent\build ^
    --distpath .agent\dist
```

If the build fails with a missing module error, the pattern is always:
1. Identify the missing module from the traceback
2. Install in `skc_build`: `conda run -n skc_build pip install <module>`
3. Add to `hiddenimports` in the spec if PyInstaller's static analysis misses it
4. Rebuild

---

## `skc_build` environment — required packages

```
google-genai
fastapi
uvicorn[standard]
pyaudio
numpy
scipy
python-dotenv
pyyaml
qrcode[pil]
Pillow
sse-starlette
pyinstaller
```

Python version: **3.11** (must match the `.pyd` suffix `cp311` in the PyAudio binary).
