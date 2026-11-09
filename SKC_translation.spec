# SKC_translation.spec — PyInstaller build spec for SKC Live Translation
# Build: activate .agent/build_env, then python -m PyInstaller SKC_translation.spec
# Output: .agent/scratch/exe/dist/SKC_translation.exe  (~70 MB, self-contained)
#
# Deploy — copy these 3 files to any folder:
#   SKC_translation.exe  — the binary
#   config.yaml          — device_index, port, model
#   .env                 — GEMINI_API_KEY=...
#
# Build environment setup: see docs/BUILD_EXE.md.

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Hidden imports alone do not fail the build when a package is missing.
# Require a working encoder before any expensive dependency analysis.
try:
    import lameenc
except ImportError as exc:
    raise SystemExit(
        f"MP3 encoder unavailable in build environment ({sys.prefix}): {exc}. "
        "Activate the project .agent/build_env and install requirements.txt."
    ) from exc
encoder = lameenc.Encoder()
encoder.set_bit_rate(128)
encoder.set_in_sample_rate(16000)
encoder.set_channels(1)
if not encoder.encode(bytes(3200)) + encoder.flush():
    raise SystemExit("MP3 encoder preflight produced no audio bytes; build stopped.")

block_cipher = None

# ── Collect data files from packages that need them ──────────────────────────
datas = []
datas += collect_data_files("google.genai")
datas += collect_data_files("google.auth")
datas += collect_data_files("certifi")
# Bundle the fixed name corrections, templates, and language catalog.
datas += [("config/name_corrections.yaml", "config"), ("config/config.template.yaml", "config")]
datas += [("app/pca-logo-white-small.webp", "app")]
datas += [("app/templates", "app/templates")]
datas += [("app/static", "app/static")]
datas += [("app/data", "app/data")]
datas += [("CHANGELOG.md", ".")]
datas += [("how_to_use.html", ".")]
datas += [("licenses", "licenses")]


# ── Hidden imports PyInstaller's static analysis misses ──────────────────────
hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("anyio")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("google.genai", filter=lambda name: "tests" not in name)
hiddenimports += collect_submodules("google.auth")
hiddenimports += collect_submodules("zeroconf")
hiddenimports += collect_submodules("jinja2")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "asyncio",
    "anyio._backends._asyncio",
    "anyio._backends._trio",
    "httptools",
    "websockets",
    "qrcode",
    "qrcode.image.pil",
    "PIL",
    "PIL.Image",
    "yaml",
    "dotenv",
    "numpy",
    "pyaudio",
    "scipy",
    "scipy.signal",
    "scipy.signal._upfirdn",
    "scipy.signal._upfirdn_apply",
    "app.tunnel",
    "app.cloudflared_service",
    "app.operator_auth",
    "lameenc",
]

# Dynamically discover pyaudio binary extension in active environment
pyaudio_binaries = []
try:
    site_pyaudio = Path(sys.prefix) / "Lib" / "site-packages" / "pyaudio"
    pyd_matches = list(site_pyaudio.glob("_portaudio*.pyd"))
    if pyd_matches:
        pyaudio_binaries = [(str(pyd_matches[0]), "pyaudio")]
    else:
        fallback_pyd = Path(r"D:\Program_Files\miniconda3\envs\skc_build\Lib\site-packages\pyaudio\_portaudio.cp311-win_amd64.pyd")
        if fallback_pyd.exists():
            pyaudio_binaries = [(str(fallback_pyd), "pyaudio")]
except Exception:
    pass

support_binaries = []
# A venv over Conda must use its base Python's DLLs, not another Conda
# installation's mingw64 DLLs found earlier on PATH.
native_library_dir = Path(sys.base_prefix) / "Library" / "bin"
for pattern in ("libmpdec-*.dll", "libssl-*.dll", "libcrypto-*.dll"):
    support_binaries.extend((str(path), ".") for path in native_library_dir.glob(pattern))

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=pyaudio_binaries + support_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest", "_pytest", "google.genai.tests", "tools",
        "tkinter", "matplotlib", "pandas", "jupyter",
        "IPython", "PyQt5", "PyQt6", "wx",
        "torch", "torchvision", "torchaudio",
        "cv2", "tokenizers", "transformers", "h5py",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

onefile = os.environ.get("SKC_BUILD_LAYOUT") != "onedir"
exe_inputs = [pyz, a.scripts]
if onefile:
    exe_inputs.extend([a.binaries, a.zipfiles, a.datas])
exe_inputs.append([])

exe = EXE(
    *exe_inputs,
    name="SKC_translation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX compression can trigger antivirus — skip it
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # keep console window — shows logs during service
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    exclude_binaries=not onefile,
    onefile=onefile,
    icon=None,
)

if not onefile:
    coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="SKC_translation")
