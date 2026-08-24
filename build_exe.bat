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
set "CONDA_ENV=skc_build"
set "OUT_DIR=%~dp0.agent"

if not exist "%CONDA_ROOT%\Scripts\activate.bat" (
    echo [ERROR] Conda not found at %CONDA_ROOT%
    echo Edit CONDA_ROOT in this file to match your Miniconda installation.
    pause & exit /b 1
)

echo [1/4] Activating conda build environment '%CONDA_ENV%'...
call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%\envs\%CONDA_ENV%"
if errorlevel 1 (
    echo [ERROR] Failed to activate '%CONDA_ENV%'. Run the one-time setup above.
    pause & exit /b 1
)

cd /d "%~dp0"

echo [2/4] Building main service binary (SKC_translation.exe)...
pyinstaller "SKC_translation.spec" --noconfirm ^
    --workpath "%OUT_DIR%\build\translation" ^
    --distpath "%OUT_DIR%\dist"
if errorlevel 1 (
    echo [ERROR] SKC_translation build failed. See output above.
    pause & exit /b 1
)

echo [3/4] Building setup wizard binary (SKC_setup.exe)...
pyinstaller "SKC_setup.spec" --noconfirm ^
    --workpath "%OUT_DIR%\build\setup" ^
    --distpath "%OUT_DIR%\dist"
if errorlevel 1 (
    echo [ERROR] SKC_setup build failed. See output above.
    pause & exit /b 1
)

echo [4/4] Preparing package structure...
if not exist "%OUT_DIR%\dist\branding" mkdir "%OUT_DIR%\dist\branding"
if not exist "%OUT_DIR%\dist\config.yaml" copy "%~dp0config.yaml" "%OUT_DIR%\dist\config.yaml" >nul

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
