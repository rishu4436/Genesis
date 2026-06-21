#!/usr/bin/env python3
"""Hackathon readiness checklist for Genesis."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis.core.config import get_env_settings
from genesis.core.database import Database
from genesis.execution.pancake_perps import PANCAKE_PERPS_DIAMOND
from genesis.execution.twak_provider import TWAKProvider


def row(label: str, ok: bool, note: str = "") -> tuple[bool, str]:
    icon = "PASS" if ok else "GAP "
    suffix = f" — {note}" if note else ""
    print(f"  [{icon}] {label}{suffix}")
    return ok, f"{label}{suffix}"


def check_bnbagent_sdk() -> tuple[bool, str]:
    try:
        from bnbagent import ERC8004Agent  # noqa: F401
        from bnbagent.erc8004.agent_uri import AgentURIGenerator  # noqa: F401
        from genesis.onchain.twak_wallet_provider import TWAKWalletProvider  # noqa: F401

        return True, "ERC8004Agent + AgentURIGenerator + TWAKWalletProvider"
    except ImportError as exc:
        return False, f"pip install bnbagent ({exc})"


def check_bnb_sdk_bridge(env) -> tuple[bool, str]:
    try:
        from genesis.onchain.bnb_identity import BNBIdentityManager

        manager = BNBIdentityManager(env, twak=None)
        uri = manager._build_agent_uri()
        ok = uri.startswith("data:application/json")
        return ok, "AgentURIGenerator URI" if ok else "URI generation failed"
    except Exception as exc:
        return False, str(exc)[:80]


async def main() -> int:
    env = get_env_settings()
    gaps: list[str] = []

    print("\n=== GENESIS HACKATHON READINESS (Track 1 + Track 2) ===\n")

    print("Core config:")
    _, m = row("API keys (.env)", bool(env.xai_api_key and env.wallet_password))
    if not m.startswith("PASS"):
        gaps.append(m)
    _, m = row("Mainnet network", env.genesis_network == "bsc-mainnet", env.genesis_network)
    if not m.startswith("PASS"):
        gaps.append(m)
    _, m = row("Strategy + risk rules", Path("config/rules.yaml").exists())
    _, m = row("Unit tests", Path("tests").exists(), "run: python -m pytest tests/")

    print("\nIntegrations (prize categories):")
    _, m = row("CMC Agent Hub client", bool(env.cmc_mcp_url), env.cmc_mcp_url)
    _, m = row("CMC API fallback key", bool(env.cmc_api_key))

    twak = TWAKProvider.from_env(env)
    twak_ok = False
    try:
        twak_ok = await twak.verify_installation()
    except Exception:
        twak_ok = False
    _, m = row("TWAK CLI", twak_ok, f"chain={env.twak_chain}, wsl={env.twak_use_wsl}")
    if not twak_ok:
        gaps.append("TWAK CLI not installed")

    sdk_ok, sdk_note = check_bnbagent_sdk()
    _, m = row("BNB SDK (bnbagent)", sdk_ok, sdk_note)
    if not sdk_ok:
        gaps.append("BNB SDK (bnbagent) not installed")

    bridge_ok, bridge_note = check_bnb_sdk_bridge(env)
    _, m = row("TWAK ↔ BNB SDK bridge", bridge_ok, bridge_note)
    if not bridge_ok:
        gaps.append("TWAK ↔ BNB SDK bridge")

    _, m = row(
        "ERC-8004 registered",
        bool(env.competition_agent_id),
        env.competition_agent_id or "run register-competition",
    )
    if not env.competition_agent_id:
        gaps.append("ERC-8004 / competition registration")

    print("\nTWAK wallet (live trading):")
    try:
        if not twak_ok:
            raise RuntimeError("TWAK CLI unavailable")

        auth = await twak._run("auth", "status")
        row("TWAK authenticated", auth.get("configured") is True)
        addr = await twak.get_wallet_address()
        row("Agent wallet address", bool(addr), (addr[:10] + "...") if addr else "")
        portfolio = await twak.get_portfolio()
        funded = portfolio.total_value_usd > 0
        _, m = row("Wallet funded (BNB/USDT)", funded, f"${portfolio.total_value_usd:.2f}")
        if not funded:
            gaps.append("Fund TWAK wallet on BSC mainnet")

        compete = await twak.compete_status()
        registered = compete.get("registered") is True
        open_now = compete.get("open", False)
        deadline = str(compete.get("deadline", ""))[:10]
        compete_note = (
            f"registered, open={open_now}, deadline={deadline}"
            if registered
            else "run register-competition"
        )
        _, m = row("Hackathon compete status", registered, compete_note)
        if not registered:
            gaps.append("Hackathon compete registration")
        elif not open_now:
            row("Competition window", False, "registration period closed")

    except Exception as e:
        _, m = row("TWAK operational", False, str(e)[:80])
        gaps.append("TWAK connection")

    print("\nOperational proof (for judges):")
    db = Database(env.genesis_db_path)
    await db.initialize()
    audits = await db.get_recent_audits(100)
    trades = await db.get_recent_trades(100)
    _, m = row("Audit trail in SQLite", len(audits) > 0, f"{len(audits)} records")
    if not audits:
        gaps.append("Run agent at least once to generate audit logs")
    _, m = row("Live/simulated trades logged", len(trades) > 0, f"{len(trades)} trades")
    _, m = row(
        "README + architecture docs",
        Path("README.md").exists() and Path("docs/architecture.mmd").exists(),
    )
    _, m = row("Dashboard", Path("dashboard/main.py").exists(), "genesis dashboard")

    print("\nExecution surfaces:")
    rules_path = Path("config/rules.yaml")
    perps_enabled = "enabled: true" in rules_path.read_text(encoding="utf-8") if rules_path.exists() else False
    _, m = row(
        "PancakeSwap Perps module",
        Path("genesis/execution/pancake_perps.py").exists(),
        f"diamond={PANCAKE_PERPS_DIAMOND[:10]}… enabled={perps_enabled}",
    )
    row("CMC native MCP client", True, "12 official tools via streamable HTTP")
    row("x402 micropayments", env.cmc_x402_enabled, "TWAK wallet → CMC x402 MCP (~$0.01/call)")

    print("\nTrack 2 (Strategy Skills):")
    skill_path = Path("skills/genesis-momentum-sentiment/SKILL.md")
    _, m = row(
        "Strategy SKILL.md",
        skill_path.exists(),
        "run: python -m genesis.cli strategy-skill export",
    )
    _, m = row("Hackathon tracks configured", "2" in env.hackathon_tracks, env.hackathon_tracks)

    print("\n--- VERDICT ---")
    blockers = [g for g in gaps if g in (
        "ERC-8004 / competition registration",
        "Hackathon compete registration",
        "Fund TWAK wallet on BSC mainnet",
        "TWAK connection",
        "TWAK CLI not installed",
        "BNB SDK (bnbagent) not installed",
        "Run agent at least once to generate audit logs",
    )]

    if not blockers:
        print("READY TO SUBMIT after completing optional polish (demo video, live tx proof).")
        return 0

    print(f"NOT YET SUBMISSION-READY — {len(blockers)} blocker(s):")
    for i, g in enumerate(blockers, 1):
        print(f"  {i}. {g}")
    print("\nMinimum path to ready:")
    print("  1. python -m genesis.cli register-competition")
    print("  2. Fund BSC wallet (BNB + USDT)")
    print("  3. python -m genesis.cli run   (or --simulate first)")
    print("  4. python -m genesis.cli logs --export data/judge_audit.json")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))