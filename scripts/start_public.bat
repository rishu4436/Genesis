@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

echo [Genesis] Stopping old processes ...
taskkill /f /im cloudflared.exe >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do taskkill /f /pid %%p >nul 2>&1
timeout /t 2 /nobreak >nul

echo [Genesis] Starting dashboard on 0.0.0.0:8080 ...
start "genesis-dashboard" /b python -m genesis.cli dashboard --host 0.0.0.0 --port 8080
timeout /t 6 /nobreak >nul

if not exist bin\cloudflared.exe (
  echo [Genesis] Downloading cloudflared ...
  python -m genesis.cli deploy-endpoint --tunnel --port 8080 --skip-onchain
  if not exist bin\cloudflared.exe (
    echo ERROR: cloudflared missing
    exit /b 1
  )
)

echo [Genesis] Starting Cloudflare tunnel — keep the genesis-tunnel window open ...
del data\tunnel.log >nul 2>&1
start "genesis-tunnel" cmd /k "cd /d %CD% && bin\cloudflared.exe tunnel --url http://127.0.0.1:8080 >> data\tunnel.log 2>&1"
timeout /t 22 /nobreak >nul

set PUBLIC_URL=
for /f "delims=" %%i in ('python -c "import re, pathlib; p=pathlib.Path('data/tunnel.log'); t=p.read_text(encoding='utf-8',errors='ignore') if p.exists() else ''; m=re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', t, re.I); print(m[-1] if m else '')"') do set PUBLIC_URL=%%i

if "!PUBLIC_URL!"=="" (
  echo ERROR: Tunnel URL not found in data\tunnel.log
  type data\tunnel.log
  exit /b 1
)

echo [Genesis] Public URL: !PUBLIC_URL!
echo !PUBLIC_URL!> data\public_endpoint.txt

echo [Genesis] Updating .env (on-chain update may take ~2 min) ...
python -m genesis.cli deploy-endpoint --url !PUBLIC_URL!
set DEPLOY_ERR=!errorlevel!

echo.
echo Dashboard: !PUBLIC_URL!/app
echo ERC-8183:  !PUBLIC_URL!/erc8183/status
echo.
if !DEPLOY_ERR! neq 0 (
  echo WARNING: deploy-endpoint returned !DEPLOY_ERR! — site may still work if .env was updated.
)
echo Leave the genesis-tunnel window open while judges review your agent.
endlocal
exit /b 0