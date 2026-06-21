#!/usr/bin/env python3
"""Backfill missing entry price / token amount on past trades via TWAK re-quote."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis.core.config import get_env_settings, get_rules
from genesis.core.database import Database
from genesis.core.models import Action
from genesis.execution.twak_provider import TWAKProvider, BSC_USDT_ADDRESS


async def main() -> int:
    env = get_env_settings()
    rules = get_rules()
    db = Database(env.genesis_db_path)
    await db.initialize()
    twak = TWAKProvider.from_env(env)

    token_by_symbol = {t.symbol.upper(): t for t in rules.allowed_tokens}
    trades = await db.get_recent_trades(50)
    updated = 0

    for trade in trades:
        if trade.get("price") and trade.get("amount_token"):
            continue
        if str(trade.get("side", "")).upper() != "BUY":
            continue

        symbol = trade.get("symbol", "")
        asset = symbol.split("/")[-1] if "/" in symbol else symbol
        token = token_by_symbol.get(asset.upper())
        trade_id = trade.get("id")
        if not token or not trade_id:
            print(f"  skip {asset}: missing allowlist entry or trade id")
            continue

        amount_usd = float(trade.get("amount_usd") or 0)
        if amount_usd <= 0:
            continue

        try:
            quote = await twak._run(
                "swap",
                BSC_USDT_ADDRESS,
                token.address,
                "--chain",
                twak.chain,
                "--usd",
                str(amount_usd),
                "--slippage",
                "1",
                "--quote-only",
            )
            built = twak._build_trade_from_swap_result(
                quote,
                from_token="USDT",
                to_token=asset,
                amount=amount_usd,
                slippage_bps=100,
                amount_is_usd=True,
                trade_side=Action.BUY,
            )
            trade["amount_token"] = built.amount_token
            trade["price"] = built.price
            await db.update_trade_data(trade_id, trade)
            print(
                f"  updated {asset}: {built.amount_token:.4f} tokens "
                f"@ ${built.price:.8f} (est. from quote)"
            )
            updated += 1
        except Exception as exc:
            print(f"  failed {asset}: {exc}")

    print(f"Backfilled {updated} trade(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))