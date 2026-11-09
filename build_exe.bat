@echo off
:: build_exe.bat — Build SKC_translation.exe and SKC_setup.exe from source
::
:: Prerequisites (one-time setup):
::   Prepare the project-local .agent\build_env environment.
::   See docs\BUILD_EXE.md. The shared agent environment is not a fallback.
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
::   ├── Korean installation and usage PDF
::   ├── how_to_use.html
::   ├── .env.example
::   └── branding/
::       └── church-logo.webp / church-logo.png
::   cloudflared.exe is downloaded during setup, not bundled in the ZIP.
::   The Korean installation/how-to PDF is included in the ZIP.

setlocal
cd /d "%~dp0"
set "OUT_DIR=.agent"
set "BUILD_ENV=%CD%\.agent\build_env"

if not exist "%BUILD_ENV%\Scripts\activate.bat" (
    echo [ERROR] Project build environment missing: %BUILD_ENV%
    echo Prepare it using docs\BUILD_EXE.md before building.
    pause & exit /b 1
)

echo [1/2] Activating project build environment...
call "%BUILD_ENV%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate project build environment.
    pause & exit /b 1
)

:: Terminate any running instances so Windows does not lock files in .agent\dist
taskkill /F /IM SKC_translation.exe >nul 2>&1
taskkill /F /IM SKC_setup.exe >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1

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
echo   ├── Korean installation and usage PDF
echo   ├── how_to_use.html
echo   ├── branding\
echo   └── SKC_translate_v*.zip  (Distribution Archive)
echo.
echo Deployment instructions:
echo   1. Distribute SKC_translate_v*.zip or contents of .agent\dist\ to the target Windows PC.
echo   2. Extract to a clean folder.
echo   3. Run SKC_setup.exe to set church identity, API key, and download cloudflared.exe if needed.
echo   4. Run SKC_translation.exe every Sunday.
echo.
pause
