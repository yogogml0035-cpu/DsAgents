@echo off
setlocal

cd /d "%~dp0..\backend" || exit /b 1
echo Starting DsAgents backend at http://10.11.148.97:8500
uv run uvicorn api:app --host 0.0.0.0 --port 8500
