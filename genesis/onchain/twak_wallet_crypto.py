"""Decrypt TWAK ~/.twak/wallet.json for local EIP-712 signing (x402 MCP)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PBKDF2_ITERATIONS = 600_000
KEY_LENGTH = 32


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS, KEY_LENGTH)


def decrypt_twak_mnemonic(wallet_data: dict[str, Any], password: str) -> str:
    """Decrypt TWAK AES-256-GCM encrypted mnemonic."""
    salt = bytes.fromhex(wallet_data["salt"])
    key = _derive_key(password, salt)
    iv = bytes.fromhex(wallet_data["iv"])
    ciphertext = bytes.fromhex(wallet_data["encryptedMnemonic"])
    tag = bytes.fromhex(wallet_data["authTag"])
    aes = AESGCM(key)
    plaintext = aes.decrypt(iv, ciphertext + tag, None)
    return plaintext.decode()


def load_twak_wallet_json(wallet_path: Path | None = None, *, use_wsl: bool = False) -> dict[str, Any]:
    """Load TWAK wallet.json from local path or WSL home."""
    if wallet_path and wallet_path.exists():
        return json.loads(wallet_path.read_text(encoding="utf-8"))

    if use_wsl:
        raw = subprocess.check_output(["wsl", "bash", "-lc", "cat ~/.twak/wallet.json"])
        return json.loads(raw)

    local = Path.home() / ".twak" / "wallet.json"
    if not local.exists():
        raise FileNotFoundError(
            "TWAK wallet.json not found. Run genesis setup-wallet or set TWAK wallet path."
        )
    return json.loads(local.read_text(encoding="utf-8"))