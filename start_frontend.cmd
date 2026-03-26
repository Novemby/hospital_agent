@echo off
setlocal
cd /d "%~dp0frontend"
"%~dp0.conda-env\python.exe" -m http.server 8001
