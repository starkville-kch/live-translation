@echo off
setlocal


::  SKC_start.bat — SKC Live Translation System Launcher
::  Starkville Korean Church (PCA)

::  Launch the packaged EXE when present; otherwise use the developer environment.
::  startup.log records the gap before Python starts as well as application phases.
::
::  Keep this window open for the entire service.
::  Close it (or press Ctrl+C) to stop the server after the service ends.


:: Configuration
set "CONDA_ROOT=D:\Program_Files\miniconda3"
set "CONDA_ENV=agent"
set "PROJECT_DIR=%~dp0"
set "SKC_STARTUP_LOG=%PROJECT_DIR%logs\startup.log"
if not exist "%PROJECT_DIR%logs" mkdir "%PROJECT_DIR%logs"
for /f "tokens=1,2" %%T in ('powershell -NoProfile -Command "$n=[DateTimeOffset]::Now; '{0} {1}' -f $n.ToUnixTimeMilliseconds(),$n.ToString('o')"') do (
    set "SKC_LAUNCH_EPOCH_MS=%%T"
    set "SKC_LAUNCH_TIME=%%U"
)
if exist "%SKC_STARTUP_LOG%" for %%L in ("%SKC_STARTUP_LOG%") do if %%~zL GEQ 1048400 move /y "%SKC_STARTUP_LOG%" "%SKC_STARTUP_LOG%.1" >nul 2>&1
>>"%SKC_STARTUP_LOG%" echo {"time":"%SKC_LAUNCH_TIME%","phase":"batch_start","epoch_ms":%SKC_LAUNCH_EPOCH_MS%,"elapsed_seconds":0}

cd /d "%PROJECT_DIR%"
if exist "%PROJECT_DIR%SKC_translation.exe" (
    "%PROJECT_DIR%SKC_translation.exe"
    exit /b
)

::  Validate conda root
if not exist "%CONDA_ROOT%\Scripts\activate.bat" (
    echo  [ERROR] Conda not found at: %CONDA_ROOT%
    echo  Edit CONDA_ROOT in this file to match your Miniconda installation.
    pause
    exit /b 1
)

::  Validate project directory
if not exist "%PROJECT_DIR%\main.py" (
    echo  [ERROR] Project not found at: %PROJECT_DIR%
    echo  Ensure this .bat file is in the SKC_live_translation_B folder.
    pause
    exit /b 1
)

::  Activate conda environment
call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%\envs\%CONDA_ENV%"
if errorlevel 1 (
    echo  [ERROR] Failed to activate conda env '%CONDA_ENV%'.
    echo  Run: conda create -n agent python=3.11 --yes
    pause
    exit /b 1
)

::  Start the server (blocking — keeps this window alive)
::  The server opens the browser automatically after startup.
python main.py

::  Post-exit message (only reached after server stops)
exit /b 0
