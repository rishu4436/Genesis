"""Percentage-based trade sizing from USDT/USDC stable balances."""

from __future__ import annotations

from genesis.core.models import PortfolioSnapshot, RulesConfig
from genesis.utils import pct_to_fraction

QUOTE_STABLE_SYMBOLS = frozenset({"USDT", "USDC"})


def stable_quote_balance_usd(portfolio: PortfolioSnapshot) -> float:
    """Total USD value of USDT + USDC in the portfolio."""
    balance = 0.0
    for position in portfolio.positions:
        sym = position.symbol.upper()
        if sym not in QUOTE_STABLE_SYMBOLS:
            continue
        if position.current_price and position.current_price > 0:
            balance += position.amount * position.current_price
        else:
            balance += position.amount

    if balance > 0:
        return balance

    return portfolio.available_usd or 0.0


def pct_spot_trade_size_usd(portfolio: PortfolioSnapshot, rules: RulesConfig) -> float:
    """Raw spot_stable_pct% of USDT/USDC (default 5%)."""
    pct = rules.risk.spot_stable_pct
    size = stable_quote_balance_usd(portfolio) * pct_to_fraction(pct)
    return round(max(size, 0.0), 2)


def max_spot_trade_size_usd(portfolio: PortfolioSnapshot, rules: RulesConfig) -> float:
    """
    Maximum spot swap notional.

    - Stables >= $20 (5% >= $1): use 5% of stables
    - Stables $1–$19 (5% < $1): use $1 minimum
    - Stables < $1: cannot trade
    """
    stables = stable_quote_balance_usd(portfolio)
    min_size = rules.risk.min_swap_usd
    pct_size = pct_spot_trade_size_usd(portfolio, rules)

    if stables < min_size:
        return 0.0
    if pct_size >= min_size:
        return pct_size
    return min_size


def spot_trade_size_usd(portfolio: PortfolioSnapshot, rules: RulesConfig) -> float:
    """Default spot swap size (min $1 when 5% < $1, else 5% of stables)."""
    return resolve_spot_trade_size(portfolio, rules)


def resolve_spot_trade_size(
    portfolio: PortfolioSnapshot,
    rules: RulesConfig,
    requested_usd: float | None = None,
) -> float:
    """
    Clamp spot trade size: min $1 (if affordable), max 5% when 5% >= $1.

    LLM/rule requests above the max are capped; requests below $1 bump to $1
    when the wallet has enough stables.
    """
    max_size = max_spot_trade_size_usd(portfolio, rules)
    min_size = rules.risk.min_swap_usd

    if max_size <= 0:
        return 0.0

    if requested_usd is None:
        return max_size

    capped = min(max(requested_usd, 0.0), max_size)
    if capped < min_size:
        return min_size if max_size >= min_size else 0.0
    return round(capped, 2)


def perps_margin_usd(portfolio: PortfolioSnapshot, rules: RulesConfig) -> float:
    """Perps collateral margin: margin_stable_pct% of USDT/USDC (default 4%)."""
    pct = rules.perps.margin_stable_pct
    margin = stable_quote_balance_usd(portfolio) * pct_to_fraction(pct)
    return round(max(margin, 0.0), 2)


def perps_notional_usd(
    portfolio: PortfolioSnapshot,
    rules: RulesConfig,
    leverage: int | None = None,
) -> float:
    """Perps position notional = margin × leverage (max leverage from rules, default 5x)."""
    margin = perps_margin_usd(portfolio, rules)
    lev = leverage if leverage is not None else rules.perps.max_leverage
    lev = max(1, min(lev, rules.perps.max_leverage))
    return round(margin * lev, 2)