@echo off
chcp 65001 >nul 2>&1
title VoiceToFile

set PORT=18990
set PYTHON=C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe
set APPFILE=%~dp0app.py
set LOGFILE=%~dp0openvt.log

if exist "%~dp0.voicetofile.lock" del /f /q "%~dp0.voicetofile.lock" >nul 2>&1
if exist "%~dp0.voicetofile.pid" del /f /q "%~dp0.voicetofile.pid" >nul 2>&1

netstat -ano | findstr ":%PORT% LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] VoiceToFile is running, opening browser...
    start http://127.0.0.1:%PORT%/
) else (
    title VoiceToFile — 服务日志
    echo [INFO] Starting VoiceToFile service...
    start http://127.0.0.1:%PORT%/
    powershell -Command "& { '%PYTHON%' '%APPFILE%' 2>&1 | Tee-Object -FilePath '%LOGFILE%' }"
)
