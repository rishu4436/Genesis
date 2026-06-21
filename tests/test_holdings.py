"""Tests for dashboard holdings merge."""

import pytest

from genesis.core.models import PortfolioSnapshot, Position
from dashboard.holdings import (
    enrich_trade_row,
    entry_prices_for_holdings,
    is_negligible_position,
    merge_holdings,
    summarize_holdings,
)
from genesis.data.price_resolver import STABLE_USD


def test_merge_holdings_with_entry_price():
    portfolio = PortfolioSnapshot(
        total_value_usd=10.0,
        available_usd=1.5,
        positions=[
            Position(symbol="TAG", amount=7865.0, entry_price=0.0, current_price=0.0011),
            Position(symbol="USDT", amount=1.5, entry_price=0.0, current_price=1.0),
        ],
    )
    entries = {"TAG": {"entry_price": 0.001055, "amount_token": 7865.0, "amount_usd": 8.3}}
    holdings = merge_holdings(portfolio, entries)

    tag = next(h for h in holdings if h["symbol"] == "TAG")
    assert tag["amount"] == 7865.0
    assert tag["entry_price"] == 0.001055
    assert tag["pnl_pct"] is not None
    assert tag["pnl_usd"] is not None
    summary = summarize_holdings(holdings)
    assert summary["unrealized_pnl_usd"] != 0
    assert summary["total_value_usd"] > 0


def test_usdt_stable_price_is_one():
    portfolio = PortfolioSnapshot(
        total_value_usd=10.0,
        available_usd=10.0,
        positions=[
            Position(symbol="USDT", amount=9.72, entry_price=0, current_price=0.99),
        ],
    )
    holdings = merge_holdings(portfolio, {})
    usdt = holdings[0]
    assert usdt["current_price"] == STABLE_USD["USDT"]
    assert usdt["value_usd"] == pytest.approx(9.72)


def test_entry_prices_for_holdings_drops_sold_assets():
    entries = {
        "TAG": {"entry_price": 0.001, "amount_token": 8000},
        "CAKE": {"entry_price": 2.5, "amount_token": 1.0},
    }
    held = [{"symbol": "USDT"}, {"symbol": "BNB"}]
    filtered = entry_prices_for_holdings(entries, held)
    assert "TAG" not in filtered
    assert "CAKE" not in filtered


def test_merge_holdings_skips_sold_positions():
    portfolio = PortfolioSnapshot(
        total_value_usd=10.0,
        available_usd=9.0,
        positions=[
            Position(symbol="BNB", amount=0.002, entry_price=0.0, current_price=600.0),
            Position(symbol="USDT", amount=9.0, entry_price=0.0, current_price=1.0),
        ],
    )
    entries = {"TAG": {"entry_price": 0.001, "amount_token": 8394.0}}
    holdings = merge_holdings(portfolio, entries)
    symbols = {h["symbol"] for h in holdings}
    assert "TAG" not in symbols
    assert symbols == {"BNB", "USDT"}


def test_is_negligible_position_dust():
    assert is_negligible_position(0.0) is True
    assert is_negligible_position(100.0, value_usd=0.005) is True
    assert is_negligible_position(100.0, value_usd=1.0) is False


def test_enrich_trade_row():
    row = enrich_trade_row(
        {"symbol": "USDT/TAG", "side": "BUY", "amount_usd": 8.3, "amount_token": 7865.0}
    )
    assert row["asset"] == "TAG"
    assert abs(row["price"] - 8.3 / 7865.0) < 1e-9