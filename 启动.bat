@echo off
chcp 65001 >nul 2>&1
title VoiceToFile 启动器
echo 正在启动 VoiceToFile，请稍候...
powershell -ExecutionPolicy Bypass -File "%~dp0启动.ps1"
