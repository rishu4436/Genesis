"""Tests for percentage-based trade sizing."""

from __future__ import annotations

from genesis.core.models import PortfolioSnapshot, Position, RulesConfig
from genesis.decision.trade_sizing import (
    max_spot_trade_size_usd,
    pct_spot_trade_size_usd,
    perps_margin_usd,
    perps_notional_usd,
    resolve_spot_trade_size,
    spot_trade_size_usd,
    stable_quote_balance_usd,
)


def _portfolio(usdt: float = 0.0, usdc: float = 0.0) -> PortfolioSnapshot:
    positions = []
    if usdt > 0:
        positions.append(
            Position(symbol="USDT", amount=usdt, entry_price=0, current_price=1.0)
        )
    if usdc > 0:
        positions.append(
            Position(symbol="USDC", amount=usdc, entry_price=0, current_price=1.0)
        )
    return PortfolioSnapshot(
        total_value_usd=usdt + usdc + 1.0,
        available_usd=usdt + usdc,
        positions=positions,
    )


def test_stable_quote_balance_sums_usdt_usdc():
    portfolio = _portfolio(usdt=80.0, usdc=20.0)
    assert stable_quote_balance_usd(portfolio) == 100.0


def test_spot_trade_size_five_percent():
    rules = RulesConfig()
    rules.risk.spot_stable_pct = 5.0
    rules.risk.min_swap_usd = 1.0
    portfolio = _portfolio(usdt=100.0)
    assert spot_trade_size_usd(portfolio, rules) == 5.0
    assert max_spot_trade_size_usd(portfolio, rules) == 5.0


def test_spot_trade_size_small_wallet_uses_one_dollar_minimum():
    rules = RulesConfig()
    rules.risk.spot_stable_pct = 5.0
    rules.risk.min_swap_usd = 1.0
    portfolio = _portfolio(usdt=9.15)
    assert pct_spot_trade_size_usd(portfolio, rules) == 0.46
    assert max_spot_trade_size_usd(portfolio, rules) == 1.0
    assert spot_trade_size_usd(portfolio, rules) == 1.0


def test_spot_trade_size_below_one_dollar_stables_returns_zero():
    rules = RulesConfig()
    rules.risk.min_swap_usd = 1.0
    portfolio = _portfolio(usdt=0.5)
    assert spot_trade_size_usd(portfolio, rules) == 0.0


def test_spot_trade_size_at_twenty_uses_five_percent():
    rules = RulesConfig()
    rules.risk.spot_stable_pct = 5.0
    rules.risk.min_swap_usd = 1.0
    portfolio = _portfolio(usdt=20.0)
    assert spot_trade_size_usd(portfolio, rules) == 1.0
    portfolio_large = _portfolio(usdt=100.0)
    assert spot_trade_size_usd(portfolio_large, rules) == 5.0


def test_resolve_spot_trade_size_caps_llm_request():
    rules = RulesConfig()
    rules.risk.spot_stable_pct = 5.0
    rules.risk.min_swap_usd = 1.0
    portfolio = _portfolio(usdt=100.0)
    assert resolve_spot_trade_size(portfolio, rules, requested_usd=50.0) == 5.0
    assert resolve_spot_trade_size(portfolio, rules, requested_usd=0.5) == 1.0


def test_perps_margin_four_percent():
    rules = RulesConfig()
    rules.perps.margin_stable_pct = 4.0
    portfolio = _portfolio(usdt=100.0)
    assert perps_margin_usd(portfolio, rules) == 4.0


def test_perps_notional_margin_times_leverage():
    rules = RulesConfig()
    rules.perps.margin_stable_pct = 4.0
    rules.perps.max_leverage = 5
    portfolio = _portfolio(usdt=100.0)
    assert perps_notional_usd(portfolio, rules) == 20.0