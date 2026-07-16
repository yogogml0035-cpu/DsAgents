param(
    [switch]$Server,
    [Alias("P")]
    [ValidateRange(1, 65535)]
    [int]$Port = 8500
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $repoRoot "backend"

if (-not $Server) {
    $scriptPath = $PSCommandPath
    Write-Host "Opening backend server window on port $Port ..."
    Start-Process -FilePath "powershell.exe" `
        -WorkingDirectory $repoRoot `
        -ArgumentList @(
            "-NoExit"
            "-ExecutionPolicy", "Bypass"
            "-File", $scriptPath
            "-Server"
            "-Port", "$Port"
        )
    exit 0
}

Set-Location $backendPath
Write-Host "Starting DsAgents backend at http://127.0.0.1:$Port"
uv run uvicorn api:app --host 0.0.0.0 --port $Port
