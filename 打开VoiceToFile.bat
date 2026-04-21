@echo off
chcp 65001 >nul 2>&1
title VoiceToFile

set PORT=18990
set PYTHONW=C:\Users\wule_\AppData\Local\Programs\Python\Python312\pythonw.exe
set APPFILE=%~dp0app.py
set LOGFILE=%~dp0openvt.log

if exist "%~dp0.voicetofile.lock" del /f /q "%~dp0.voicetofile.lock" >nul 2>&1
if exist "%~dp0.voicetofile.pid" del /f /q "%~dp0.voicetofile.pid" >nul 2>&1

netstat -ano | findstr ":%PORT% LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] VoiceToFile is running, opening browser...
    start http://127.0.0.1:%PORT%/
) else (
    echo [INFO] Starting VoiceToFile service...
    start "" "%PYTHONW%" "%APPFILE%" >> "%LOGFILE%" 2>&1
    echo [INFO] Waiting 12 seconds for service to start...
    ping -n 13 127.0.0.1 >nul 2>&1
    echo [OK] Opening browser...
    start http://127.0.0.1:%PORT%/
)
