"""Persisted TWAK wallet address — instant dashboard display without WSL round-trips."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genesis.core.config import PROJECT_ROOT

WALLET_ADDRESS_FILE = PROJECT_ROOT / "data" / "wallet_address.txt"


def read_persisted_wallet_address() -> str:
    """Last known TWAK wallet address written to data/wallet_address.txt."""
    try:
        if WALLET_ADDRESS_FILE.exists():
            return WALLET_ADDRESS_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def persist_wallet_address(address: str) -> None:
    """Save wallet address for instant SSR on next page load."""
    address = (address or "").strip()
    if not address:
        return
    WALLET_ADDRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WALLET_ADDRESS_FILE.write_text(address + "\n", encoding="utf-8")


def resolve_wallet_address(env: Any) -> str:
    """Env var first, then persisted file."""
    addr = (getattr(env, "genesis_wallet_address", "") or "").strip()
    if addr:
        return addr
    return read_persisted_wallet_address()


def wallet_display_context(env: Any) -> dict[str, str | None]:
    """Address + BscScan URL without TWAK calls."""
    from dashboard.wallet_info import INTEGRATION_LINKS

    address = resolve_wallet_address(env)
    if not address:
        return {"wallet_address": None, "bscscan_wallet_url": None}
    return {
        "wallet_address": address,
        "bscscan_wallet_url": f"{INTEGRATION_LINKS['bscscan']}/address/{address}",
    }