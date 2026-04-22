@echo off
chcp 65001 >nul 2>&1
title VoiceToFile

echo Starting VoiceToFile...

cd /d F:\voicetofile
start python.exe app.py

echo Waiting for server to start...
timeout /t 8 >nul

echo.
echo ===================
echo VoiceToFile 已启动！
echo 正在打开浏览器...
echo ===================
echo.

start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --inprivate http://127.0.0.1:18990/

pause