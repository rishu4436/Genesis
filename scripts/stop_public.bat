@echo off
taskkill /f /im cloudflared.exe >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do taskkill /f /pid %%p >nul 2>&1
echo Stopped Genesis dashboard and tunnel.