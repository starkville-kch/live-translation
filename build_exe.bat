@echo off
:: build_exe.bat — Build SKC_translation.exe from source
::
:: Prerequisites (one-time setup):
::   conda create -n skc_build python=3.11 --yes
::   conda run -n skc_build pip install google-genai fastapi "uvicorn[standard]" pyaudio numpy ^
::       python-dotenv pyyaml "qrcode[pil]" Pillow sse-starlette scipy pyinstaller
::
:: Output: .agent\dist\SKC_translation.exe
:: Deploy: copy SKC_translation.exe + config.yaml + .env to any folder

setlocal
set "CONDA_ROOT=D:\Program_Files\miniconda3"
set "CONDA_ENV=skc_build"
set "SPEC=SKC_translation.spec"
set "OUT_DIR=%~dp0.agent"

if not exist "%CONDA_ROOT%\Scripts\activate.bat" (
    echo [ERROR] Conda not found at %CONDA_ROOT%
    echo Edit CONDA_ROOT in this file to match your Miniconda installation.
    pause & exit /b 1
)

echo [1/2] Building exe with conda env '%CONDA_ENV%'...
call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%\envs\%CONDA_ENV%"
if errorlevel 1 (
    echo [ERROR] Failed to activate '%CONDA_ENV%'. Run the one-time setup above.
    pause & exit /b 1
)

cd /d "%~dp0"
pyinstaller "%SPEC%" --noconfirm ^
    --workpath "%OUT_DIR%\build" ^
    --distpath "%OUT_DIR%\dist"

if errorlevel 1 (
    echo [ERROR] Build failed. See output above.
    pause & exit /b 1
)

echo.
echo [2/2] Done.
echo Output: %OUT_DIR%\dist\SKC_translation.exe
echo.
echo To deploy, copy these 3 files to any folder:
echo   SKC_translation.exe
echo   config.yaml
echo   .env
echo.
pause
