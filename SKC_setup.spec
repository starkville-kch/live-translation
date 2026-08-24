# SKC_setup.spec — PyInstaller build spec for Live Translation Setup Wizard
# Build:  conda run -n skc_build pyinstaller SKC_setup.spec
# Output: .agent/dist/SKC_setup.exe (Windowed Tkinter application)

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
datas += [("app/pca-logo-white-small.webp", "app")]
datas += [("CHANGELOG.md", ".")]

# ── Hidden imports ────────────────────────────────────────────────────────────
hiddenimports = []
hiddenimports += collect_submodules("google.genai")
hiddenimports += collect_submodules("google.api_core")
hiddenimports += collect_submodules("google.auth")
hiddenimports += collect_submodules("grpc")
hiddenimports += [
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "yaml",
    "dotenv",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "certifi",
]

a = Analysis(
    ["setup_gui.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "pandas", "jupyter", "IPython",
        "PyQt5", "PyQt6", "wx", "scipy", "pyaudio",
        "uvicorn", "fastapi", "sse_starlette",
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
    name="SKC_setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # Windowed mode: clean GUI without command prompt
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
    icon=None,
)
