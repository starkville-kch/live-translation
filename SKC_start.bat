@echo off
setlocal enabledelayedexpansion


::  SKC_start.bat — SKC Live Translation System Launcher
::  Starkville Korean Church (PCA)

::  One double-click to:
::    1. Activate the 'agent' conda environment
::    2. Start the FastAPI translation server (python main.py)
::    3. Automatically open the operator console in the default browser
::
::  Keep this window open for the entire service.
::  Close it (or press Ctrl+C) to stop the server after the service ends.


:: Configuration
set "CONDA_ROOT=D:\Program_Files\miniconda3"
set "CONDA_ENV=agent"
set "PROJECT_DIR=%~dp0"
set "SERVER_PORT=8080"
set "OPERATOR_URL=http://localhost:%SERVER_PORT%"

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

::  Change to project directory
cd /d "%PROJECT_DIR%"

::  Start the server (blocking — keeps this window alive)
::  The server opens the browser automatically after startup.
python main.py

::  Post-exit message (only reached after server stops)
exit /b 0
