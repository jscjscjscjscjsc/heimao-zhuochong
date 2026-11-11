@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Super Guga - One Click Launcher
cd /d "%~dp0"

set "VENV=%CD%\.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "PYTHONW=%VENV%\Scripts\pythonw.exe"

if exist "%PYTHON%" (
    "%PYTHON%" --version >nul 2>&1
    if errorlevel 1 (
        echo [INFO] Existing .venv is broken; rebuilding it...
        rmdir /s /q "%VENV%"
    )
)

if not exist "%PYTHON%" (
    set "BOOTSTRAP="
    py -3.12 --version >nul 2>&1 && set "BOOTSTRAP=py -3.12"
    if not defined BOOTSTRAP (
        py -3 --version >nul 2>&1 && set "BOOTSTRAP=py -3"
    )
    if not defined BOOTSTRAP (
        python --version >nul 2>&1 && set "BOOTSTRAP=python"
    )
    if not defined BOOTSTRAP (
        echo [ERROR] Python 3.10 or newer was not found.
        echo Install 64-bit Python 3.12 and enable "Add Python to PATH":
        echo https://www.python.org/downloads/windows/
        pause
        exit /b 1
    )

    echo [1/4] Creating Python environment...
    !BOOTSTRAP! -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv.
        pause
        exit /b 1
    )
)

"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required.
    pause
    exit /b 1
)

echo [2/4] Checking runtime packages...
"%PYTHON%" -c "from importlib.metadata import version; [version(x) for x in ('PyQt5','av','PyOpenGL','Pillow','pywin32','edge-tts','requests','psutil','sounddevice','pywebrtc-audio','vosk','pythonnet','websocket-client','numpy','onnxruntime','kaldi-native-fbank','playwright')]" >nul 2>&1
if errorlevel 1 (
    echo [3/4] Installing runtime packages. The first run may take several minutes...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed. Check the network and run this file again.
        echo Log: %CD%\startup.log
        pause
        exit /b 1
    )
) else (
    echo [3/4] Runtime packages are ready.
)

if not exist "ai_config.json" (
    copy /Y "ai_config.example.json" "ai_config.json" >nul
    echo [INFO] Created ai_config.json. Fill in your own API keys to enable online AI features.
)

echo [4/4] Starting Super Guga...
start "" "%PYTHONW%" launcher.py
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 3" >nul 2>&1
if exist "startup.log" (
    set "LAUNCH_ERROR="
    findstr /I /C:"Traceback" /C:"Error" /C:"Exception" "startup.log" >nul 2>&1 && set "LAUNCH_ERROR=1"
    if defined LAUNCH_ERROR (
        echo [WARN] The launcher reported an error. See:
        echo %CD%\startup.log
        notepad "startup.log"
    )
)

echo Super Guga has been launched. Look for the penguin on the desktop or in the system tray.
exit /b 0
