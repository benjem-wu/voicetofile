@echo off
chcp 65001 >nul 2>&1
title VoiceToFile

echo ========================================
echo  VoiceToFile
echo ========================================
echo.

set PYTHON=C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe
set APPFILE=%~dp0app.py

pushd "%~dp0"

if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    pause
    exit /b 1
)

echo [1/3] Checking dependencies...
"%PYTHON%" -m pip show flask >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    "%PYTHON%" -m pip install flask requests faster-whisper yt-dlp playwright
    "%PYTHON%" -m playwright install chromium
)

if not exist "ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe" (
    echo [WARN] ffmpeg not found
)

echo [2/3] Cleaning up old process and lock files...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :18990 ^| findstr LISTENING') do (
    echo Killing PID=%%a
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 >nul 2>&1

set retry=0
:retry_delete
set /a retry+=1
if exist ".voicetofile.lock" (
    del /f /q ".voicetofile.lock" 2>nul
    if exist ".voicetofile.lock" (
        if %retry% lss 3 (
            echo Retrying lock file delete...
            timeout /t 1 >nul 2>&1
            goto retry_delete
        )
    )
)
if exist ".voicetofile.pid" del /f /q ".voicetofile.pid"

echo [3/3] Starting VoiceToFile...
start "" /B "%PYTHON%" "%APPFILE%"

echo Waiting for server to start...
set waited=0
:wait_loop
timeout /t 1 >nul 2>&1
set /a waited+=1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :18990 ^| findstr LISTENING') do (
    echo.
    echo Server is ready! Opening browser...
    echo.
    start "" "http://127.0.0.1:18990"
    echo Press any key to close this window...
    pause >nul
    exit
)
if %waited% lss 30 goto wait_loop

echo.
echo Server failed to start within 30 seconds.
echo.
pause
exit /b 1
