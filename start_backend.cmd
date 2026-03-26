@echo off
setlocal
cd /d "%~dp0"
"%~dp0.conda-env\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
