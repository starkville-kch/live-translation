@echo off
:: build_exe.bat — Build SKC_translation.exe and SKC_setup.exe from source
::
:: Prerequisites (one-time setup):
::   conda create -n skc_build python=3.11 --yes
::   conda run -n skc_build pip install google-genai fastapi "uvicorn[standard]" pyaudio numpy ^
::       python-dotenv pyyaml "qrcode[pil]" Pillow sse-starlette scipy zeroconf pyinstaller
::
:: Output:
::   .agent\dist\SKC_translation.exe  (Main Sunday live translation)
::   .agent\dist\SKC_setup.exe        (Setup wizard & key configuration)
::
:: Deploy package:
::   Live Translation/
::   ├── SKC_translation.exe
::   ├── SKC_setup.exe
::   ├── config.yaml
::   └── branding/
::       └── church-logo.png

setlocal
set "CONDA_ROOT=D:\Program_Files\miniconda3"
set "CONDA_ENV=agent"
if exist "%CONDA_ROOT%\envs\skc_build\python.exe" set "CONDA_ENV=skc_build"

if not exist "%CONDA_ROOT%\Scripts\activate.bat" (
    echo [ERROR] Conda not found at %CONDA_ROOT%
    echo Edit CONDA_ROOT in this file to match your Miniconda installation.
    pause & exit /b 1
)

echo [1/2] Activating conda build environment '%CONDA_ENV%'...
call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%\envs\%CONDA_ENV%"
if errorlevel 1 (
    echo [ERROR] Failed to activate '%CONDA_ENV%'.
    pause & exit /b 1
)

cd /d "%~dp0"

echo [2/2] Running Multi-Threaded Parallel Build (SKC_translation.exe + SKC_setup.exe)...
python build_parallel.py -j 4
if errorlevel 1 (
    echo [ERROR] Build failed. See output above.
    pause & exit /b 1
)

echo.
echo ================================================================
echo  Build Succeeded!
echo ================================================================
echo Output Directory: %OUT_DIR%\dist\
echo   ├── SKC_translation.exe
echo   ├── SKC_setup.exe
echo   ├── config.yaml
echo   └── branding\
echo.
echo Deployment instructions:
echo   1. Copy the contents of .agent\dist\ to the target Windows PC.
echo   2. Run SKC_setup.exe once to set church identity and API key.
echo   3. Run SKC_translation.exe every Sunday.
echo.
pause
