@echo off
chcp 65001 >nul 2>&1
title VoiceToFile Launcher
echo Starting VoiceToFile...
powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1"
