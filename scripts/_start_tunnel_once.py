"""One-shot tunnel launcher for Windows (persistent cmd /k window)."""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "bin" / "cloudflared.exe"
LOG = ROOT / "data" / "tunnel.log"
PORT = 8080


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("", encoding="utf-8")
    inner = (
        f'cd /d "{ROOT}" && "{EXE}" tunnel --url http://127.0.0.1:{PORT} '
        f'>> "{LOG}" 2>&1'
    )
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "genesis-tunnel", "cmd", "/k", inner],
        cwd=str(ROOT),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.time() + 60
    url = None
    while time.time() < deadline:
        text = LOG.read_text(encoding="utf-8", errors="ignore") if LOG.exists() else ""
        matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", text, re.I)
        if matches:
            url = matches[-1]
            break
        time.sleep(0.5)
    if not url:
        print("ERROR: tunnel URL not found in log", file=sys.stderr)
        return 1
    (ROOT / "data" / "public_endpoint.txt").write_text(url + "\n", encoding="utf-8")
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())