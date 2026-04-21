# VoiceToFile Startup Script
$ErrorActionPreference = "Continue"

$PORT = 18990
$PYTHON = "C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe"
$APPFILE = Join-Path $PSScriptRoot "app.py"
$LOGFILE = Join-Path $PSScriptRoot "startup.log"
$STARTUP_WAIT = 15  # 等待秒数

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $LOGFILE -Value $line -Encoding UTF8
}

function Test-ServerReady($maxRetries = 20, $delay = 1) {
    for ($i = 1; $i -le $maxRetries; $i++) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$PORT/" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($response.StatusCode -eq 200) {
                return $true
            }
        } catch {}
        Write-Log "Waiting for server... ($i/$maxRetries)"
        Start-Sleep -Seconds $delay
    }
    return $false
}

Write-Log "=== VoiceToFile Startup ==="

# 1. Kill old process on port
Write-Log "[1/5] Cleaning up old process..."
$connections = Get-NetTCPConnection -LocalPort $PORT -ErrorAction SilentlyContinue
foreach ($conn in $connections) {
    if ($conn.State -eq "Listen") {
        Write-Log "Killing PID=$($conn.OwningProcess)"
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2

# 2. Cleanup lock files
Write-Log "[2/5] Cleaning up lock files..."
$lockFile = Join-Path $PSScriptRoot ".voicetofile.lock"
$pidFile = Join-Path $PSScriptRoot ".voicetofile.pid"
if (Test-Path $lockFile) { Remove-Item $lockFile -Force; Write-Log "Removed .voicetofile.lock" }
if (Test-Path $pidFile) { Remove-Item $pidFile -Force; Write-Log "Removed .voicetofile.pid" }

# 3. Check Python
Write-Log "[3/5] Checking Python..."
if (-not (Test-Path $PYTHON)) {
    Write-Log "[ERROR] Python not found: $PYTHON"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Log "Python OK: $PYTHON"

# 4. Start Flask
Write-Log "[4/5] Starting Flask..."
$proc = Start-Process $PYTHON -ArgumentList $APPFILE -PassThru -WindowStyle Normal
Write-Log "Started with PID=$($proc.Id)"

# 5. Wait for server ready with health check
Write-Log "[5/5] Waiting for server ready..."
if (Test-ServerReady -maxRetries 30 -delay 1) {
    Write-Log "Server ready! Opening browser..."
    $edgePath = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if (Test-Path $edgePath) {
        Start-Process $edgePath -ArgumentList "--new-window","http://127.0.0.1:$PORT/"
    } else {
        Start-Process "http://127.0.0.1:$PORT/"
    }
    Write-Log "Done."
} else {
    Write-Log "[ERROR] Server failed to start within timeout."
    Write-Log "Please check startup.log for errors."
    Read-Host "Press Enter to exit"
    exit 1
}
