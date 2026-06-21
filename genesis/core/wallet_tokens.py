"""Gas, stable, and quote tokens — wallet balance only, not open trades."""

from __future__ import annotations

from typing import Protocol

# Native gas + stable/quote assets — never sold by sell-all, not open positions
KEEP_SYMBOLS = frozenset(
    {
        "BNB",
        "WBNB",
        "USDT",
        "USDC",
        "DAI",
        "TUSD",
        "USDD",
        "USD1",
        "USDE",
        "U",
        "FDUSD",
        "FRAX",
        "BUSD",
        "USDP",
        "LUSD",
        "SUSD",
        "GUSD",
        "EURI",
        "XUSD",
        "DUSD",
        "FRXUSD",
        "USDF",
        "LISUSD",
        "XAUt",
    }
)


class _PositionLike(Protocol):
    symbol: str
    amount: float


STABLE_SYMBOLS = KEEP_SYMBOLS - {"BNB", "WBNB"}


def is_stablecoin(symbol: str) -> bool:
    """True for fiat-pegged / quote stables — skip CMC signal scans for these."""
    return symbol.upper() in STABLE_SYMBOLS


def is_gas_or_stable(symbol: str) -> bool:
    """True for BNB (gas) and stablecoins used as quote/cash."""
    return symbol.upper() in KEEP_SYMBOLS


def is_trading_position(symbol: str, amount: float) -> bool:
    """True for non-zero balances that represent an actual trade (not gas/stables)."""
    return amount > 0 and not is_gas_or_stable(symbol)


def count_trading_positions(positions: list[_PositionLike]) -> int:
    """Count open trading positions (excludes BNB, USDT, USDC, and other stables)."""
    return sum(1 for p in positions if is_trading_position(p.symbol, p.amount))