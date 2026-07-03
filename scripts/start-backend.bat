@echo off
setlocal

cd /d "%~dp0..\backend" || exit /b 1
echo Starting DsAgents backend at http://127.0.0.1:8500
uv run uvicorn api:app --host 0.0.0.0 --port 8500
