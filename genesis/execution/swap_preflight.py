"""TWAK swap route checks before live execution."""

from __future__ import annotations

from genesis.core.models import Action, CompositeSignal, Decision, RulesConfig, TokenConfig
from genesis.decision.candidate_selection import buy_priority_key, is_buy_eligible
from genesis.decision.strategy_engine import NON_TRADABLE_SYMBOLS
from genesis.execution.liquidate import format_swap_error
from genesis.execution.twak_provider import TWAKProvider


def _token_lookup(tokens: list[TokenConfig]) -> dict[str, TokenConfig]:
    return {t.symbol.upper(): t for t in tokens}


def ordered_buy_attempts(
    decision: Decision,
    composites: list[CompositeSignal],
    rules: RulesConfig,
) -> list[str]:
    """
    Symbols to try for a BUY, highest priority first.

    Keeps the LLM/rule pick first, then other buy-eligible tokens by market cap.
    """
    eligible = [
        c
        for c in composites
        if is_buy_eligible(c, rules, non_tradable=NON_TRADABLE_SYMBOLS)
    ]
    ranked = sorted(eligible, key=buy_priority_key, reverse=True)
    ordered: list[str] = []

    primary = decision.asset.upper()
    if primary:
        ordered.append(primary)

    for composite in ranked:
        sym = composite.symbol.upper()
        if sym not in ordered:
            ordered.append(sym)

    return ordered


async def find_swappable_buy_asset(
    twak: TWAKProvider,
    *,
    decision: Decision,
    composites: list[CompositeSignal],
    rules: RulesConfig,
    quote: str,
    size_usd: float,
    slippage_bps: int,
    allowed_tokens: list[TokenConfig],
) -> tuple[str | None, str | None]:
    """
    Return (symbol, error_summary) for the first asset with a TWAK quote route.

    error_summary is set when every candidate lacks a route.
    """
    if decision.action != Action.BUY or size_usd <= 0:
        return decision.asset, None

    token_map = _token_lookup(allowed_tokens)
    quote_token = token_map.get(quote.upper())
    failures: list[str] = []

    for symbol in ordered_buy_attempts(decision, composites, rules):
        asset_token = token_map.get(symbol.upper())
        if not asset_token:
            failures.append(f"{symbol}: not in allowlist")
            continue
        try:
            await twak.quote_swap(
                quote,
                symbol,
                size_usd,
                slippage_bps=slippage_bps,
                from_address=quote_token.address if quote_token else None,
                to_address=asset_token.address,
            )
            if symbol.upper() != decision.asset.upper():
                from loguru import logger

                logger.warning(
                    f"TWAK has no route for {decision.asset} — "
                    f"falling back to {symbol}"
                )
            return symbol, None
        except Exception as exc:
            failures.append(f"{symbol}: {format_swap_error(exc)}")

    summary = "; ".join(failures[:4])
    if len(failures) > 4:
        summary += f" (+{len(failures) - 4} more)"
    return None, summary or "no swappable buy candidates"