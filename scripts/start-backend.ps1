param(
    [switch]$Server
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $repoRoot "backend"

if (-not $Server) {
    $scriptPath = $PSCommandPath
    Start-Process -FilePath "powershell.exe" `
        -WorkingDirectory $repoRoot `
        -ArgumentList @(
            "-NoExit"
            "-ExecutionPolicy", "Bypass"
            "-File", "`"$scriptPath`""
            "-Server"
        )
    exit 0
}

Set-Location $backendPath
Write-Host "Starting DsAgents backend at http://127.0.0.1:8500"
uv run uvicorn api:app --host 0.0.0.0 --port 8500
