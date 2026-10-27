@echo off
setlocal
cd /d "%~dp0"
set "PY="
if exist "D:\supermapiobjectenvgpu2026\conda\python.exe" set "PY=D:\supermapiobjectenvgpu2026\conda\python.exe"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [x] Python not found.
  pause
  exit /b 1
)
echo Starting FastAPI on http://127.0.0.1:8000 ...
"%PY%" -m uvicorn app:app --host 127.0.0.1 --port 8000
pause