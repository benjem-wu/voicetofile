# VoiceToFile Startup Script
$ErrorActionPreference = "Continue"

$PORT = 18990
$PYTHON = "C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe"
$APPFILE = Join-Path $PSScriptRoot "app.py"
$LOGFILE = Join-Path $PSScriptRoot "startup.log"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $LOGFILE -Value $line -Encoding UTF8
}

Write-Log "=== VoiceToFile Startup ==="

# 1. Kill old process on port
Write-Log "[1/4] Cleaning up old process..."
$connections = Get-NetTCPConnection -LocalPort $PORT -ErrorAction SilentlyContinue
foreach ($conn in $connections) {
    if ($conn.State -eq "Listen") {
        Write-Log "Killing PID=$($conn.OwningProcess)"
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2

# 2. Cleanup lock files
Write-Log "[2/4] Cleaning up lock files..."
$lockFile = Join-Path $PSScriptRoot ".voicetofile.lock"
$pidFile = Join-Path $PSScriptRoot ".voicetofile.pid"
if (Test-Path $lockFile) { Remove-Item $lockFile -Force; Write-Log "Removed .voicetofile.lock" }
if (Test-Path $pidFile) { Remove-Item $pidFile -Force; Write-Log "Removed .voicetofile.pid" }

# 3. Check Python
if (-not (Test-Path $PYTHON)) {
    Write-Log "[ERROR] Python not found: $PYTHON"
    Read-Host "Press Enter to exit"
    exit 1
}

# 4. Start Flask
Write-Log "[3/4] Starting Flask..."
Start-Process $PYTHON -ArgumentList $APPFILE -WindowStyle Normal

# 5. Wait for server, then open browser
Write-Log "[4/4] Waiting for server (10 seconds)..."
Start-Sleep -Seconds 10

Write-Log "Opening browser..."
$edgePath = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (Test-Path $edgePath) {
    Start-Process $edgePath -ArgumentList "--new-window","http://127.0.0.1:$PORT/"
} else {
    Start-Process "http://127.0.0.1:$PORT/"
}

Write-Log "Done."
