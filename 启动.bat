@echo off
chcp 65001 >nul 2>&1
title VoiceToFile

echo ========================================
echo  VoiceToFile
echo ========================================
echo.

if not exist "C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe" (
    echo [ERROR] Python not found at: C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe
    pause
    exit /b 1
)

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo [1/4] Checking dependencies...
"C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe" -m pip show flask >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    "C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe" -m pip install flask requests faster-whisper yt-dlp playwright
    "C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe" -m playwright install chromium
)

if not exist "ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe" (
    echo [WARN] ffmpeg not found, will use system ffmpeg
)

echo [2/4] Checking if VoiceToFile is already running...
:: 检查端口 18990 是否已被占用
netstat -ano | findstr :18990 | findstr LISTENING >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] VoiceToFile is already running, opening browser...
    start "" "http://127.0.0.1:18990"
    echo [OK] Browser opened.
    exit /b 0
)

echo [3/4] Starting VoiceToFile...
echo [4/4] Opening browser...
start "" "http://127.0.0.1:18990"
"C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe" "%SCRIPT_DIR%\app.py"