@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0.."
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do (
  echo Dashboard already on port 8080 PID %%p
  exit /b 0
)
echo Starting Genesis dashboard on 0.0.0.0:8080 ...
start "genesis-dashboard" /b python -m genesis.cli dashboard --host 0.0.0.0 --port 8080
timeout /t 4 /nobreak >nul
echo Local:  http://127.0.0.1:8080/app
if exist data\public_endpoint.txt (
  set /p PUB=<data\public_endpoint.txt
  echo Public: !PUB!/app
)