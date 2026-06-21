"""Tests for gas/stable vs trading position helpers."""

from genesis.core.models import Position, PortfolioSnapshot
from genesis.core.wallet_tokens import (
    count_trading_positions,
    is_gas_or_stable,
    is_trading_position,
)


def test_is_gas_or_stable():
    assert is_gas_or_stable("BNB")
    assert is_gas_or_stable("usdt")
    assert is_gas_or_stable("USDC")
    assert not is_gas_or_stable("TAG")


def test_count_trading_positions_excludes_bnb_and_stables():
    portfolio = PortfolioSnapshot(
        total_value_usd=20.0,
        available_usd=10.0,
        positions=[
            Position(symbol="BNB", amount=0.01, entry_price=0, current_price=600.0),
            Position(symbol="USDT", amount=10.0, entry_price=0, current_price=1.0),
            Position(symbol="TAG", amount=0.001, entry_price=0, current_price=0.001),
        ],
    )
    assert count_trading_positions(portfolio.positions) == 1
    assert is_trading_position("TAG", 0.001)
    assert not is_trading_position("USDT", 10.0)