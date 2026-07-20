# SKC_translation.spec — PyInstaller build spec for SKC Live Translation
# Build:  conda run -n skc_build pyinstaller SKC_translation.spec
# Output: .agent/scratch/exe/dist/SKC_translation.exe  (~70 MB, self-contained)
#
# Deploy — copy these 3 files to any folder:
#   SKC_translation.exe  — the binary
#   config.yaml          — device_index, port, model
#   .env                 — GEMINI_API_KEY=...
#
# Build environment setup (one-time):
#   conda create -n skc_build python=3.11 --yes
#   conda run -n skc_build pip install google-genai fastapi "uvicorn[standard]" pyaudio numpy \
#       python-dotenv pyyaml "qrcode[pil]" Pillow sse-starlette scipy zeroconf pyinstaller

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# ── Collect data files from packages that need them ──────────────────────────
datas = []
datas += collect_data_files("google.genai")
datas += collect_data_files("google.api_core")
datas += collect_data_files("google.auth")
datas += collect_data_files("grpc")
datas += collect_data_files("certifi")
datas += collect_data_files("sse_starlette")
# Bundle glossary config, PCA logo asset, and HTML templates
datas += [("config/glossary.yaml", "config")]
datas += [("app/pca-logo-white-small.webp", "app")]
datas += [("app/templates/attendee.html", "app/templates")]
datas += [("app/templates/operator.html", "app/templates")]

# ── Hidden imports PyInstaller's static analysis misses ──────────────────────
hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("anyio")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("google.genai")
hiddenimports += collect_submodules("google.api_core")
hiddenimports += collect_submodules("google.auth")
hiddenimports += collect_submodules("grpc")
hiddenimports += collect_submodules("sse_starlette")
hiddenimports += collect_submodules("zeroconf")
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
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[
        # conda's PyAudio statically links PortAudio into the .pyd — include it explicitly
        (
            r"D:\Program_Files\miniconda3\envs\skc_build\Lib\site-packages\pyaudio\_portaudio.cp311-win_amd64.pyd",
            "pyaudio",
        ),
    ],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "pandas", "jupyter",
        "IPython", "PyQt5", "PyQt6", "wx",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
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
    onefile=True,
    icon=None,
)
