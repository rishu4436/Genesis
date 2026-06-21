#!/usr/bin/env python3
"""Validate Genesis .env configuration before running the agent."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis.core.config import get_env_settings, load_rules
from genesis.utils import truncate_address


def check(name: str, ok: bool, detail: str = "") -> bool:
    icon = "✓" if ok else "✗"
    suffix = f" — {detail}" if detail else ""
    print(f"  {icon} {name}{suffix}")
    return ok


async def main() -> int:
    print("Genesis .env Validation\n")
    errors = 0

    try:
        env = get_env_settings()
        rules = load_rules()
    except Exception as e:
        print(f"  ✗ Config load failed: {e}")
        return 1

    print("Required:")
    if env.llm_enabled:
        if not check(
            "XAI_API_KEY",
            bool(env.xai_api_key),
            "needed for LLM decisions (grok provider)",
        ):
            errors += 1
    else:
        check("LLM_ENABLED", True, "false — rule-based trading, no API credits needed")
    if not check("WALLET_PASSWORD", bool(env.wallet_password), "needed for ERC-8004 registration"):
        errors += 1

    print("\nRecommended:")
    cmc_ok = bool(env.cmc_api_key) or env.cmc_x402_enabled
    cmc_detail = []
    if env.cmc_api_key:
        cmc_detail.append("API key")
    if env.cmc_x402_enabled:
        cmc_detail.append(f"x402 ({env.cmc_x402_mode})")
    if not check("CMC data source", cmc_ok, " or ".join(cmc_detail) or "set CMC_API_KEY or CMC_X402_ENABLED"):
        errors += 1
    check("rules.yaml", True, rules.strategy.name)

    print("\nTWAK:")
    from genesis.execution.twak_provider import TWAKProvider

    twak = TWAKProvider.from_env(env)
    twak_ok = await twak.verify_installation()
    twak_detail = f"chain={env.twak_chain}" + (" (via WSL)" if env.twak_use_wsl else "")
    if not check("TWAK CLI", twak_ok, twak_detail):
        errors += 1
        print("    Linux/WSL:  which twak")
        print("    Windows:    set TWAK_USE_WSL=true in .env")
        print("    Install:    npm install -g @trustwallet/cli")

    if twak_ok:
        try:
            auth = await twak._run("auth", "status")
            check("TWAK auth", auth.get("configured") is True, f"accessId={auth.get('accessId', '?')}")
        except Exception as e:
            check("TWAK auth", False, str(e))
            errors += 1

        twak_pw = env.twak_wallet_password or env.wallet_password
        if not check("TWAK_WALLET_PASSWORD", bool(twak_pw), "needed for swaps"):
            errors += 1

        try:
            addr = await twak.get_wallet_address()
            check("TWAK wallet", bool(addr), truncate_address(addr) if addr else "run setup-wallet")
        except Exception as e:
            check("TWAK wallet", False, f"run: python -m genesis.cli setup-wallet ({e})")

    print("\nNetwork alignment:")
    networks_match = env.genesis_network == env.twak_network == env.bnb_agent_network
    if not check(
        "All networks match",
        networks_match,
        f"genesis={env.genesis_network} twak={env.twak_network} bnb={env.bnb_agent_network}",
    ):
        errors += 1

    print("\nOptional:")
    check("COMPETITION_AGENT_ID", bool(env.competition_agent_id), "set after register-competition")
    check("PRIVATE_KEY", not bool(env.private_key), "should be empty after first registration (good if empty)")

    print()
    if errors:
        print(f"Fix {errors} issue(s) above, then re-run: python scripts/validate_env.py")
        return 1

    print("All required checks passed. Next:")
    print("  python -m genesis.cli register-competition")
    print("  python -m genesis.cli run")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))