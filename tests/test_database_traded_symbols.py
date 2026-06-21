"""Tests for traded asset symbol lookup."""

from __future__ import annotations

import pytest

from genesis.core.database import Database
from genesis.core.models import Action, Trade, TradeStatus


@pytest.mark.asyncio
async def test_get_traded_asset_symbols_from_buys(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()

    await db.save_trade(
        Trade(
            symbol="USDT/TAG",
            side=Action.BUY,
            amount_usd=8.3,
            amount_token=7944.2,
            price=0.001055,
            status=TradeStatus.CONFIRMED,
        )
    )
    await db.save_trade(
        Trade(
            symbol="USDT/CAKE",
            side=Action.BUY,
            amount_usd=5.0,
            status=TradeStatus.CONFIRMED,
        )
    )
    await db.save_trade(
        Trade(
            symbol="TAG/USDT",
            side=Action.SELL,
            amount_usd=8.0,
            status=TradeStatus.CONFIRMED,
        )
    )

    symbols = await db.get_traded_asset_symbols()
    assert set(symbols) == {"TAG", "CAKE"}