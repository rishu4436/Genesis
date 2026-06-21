"""Which allowlisted tokens warrant per-cycle CMC signal fetches."""

from __future__ import annotations

from genesis.core.models import DemoScanConfig, RulesConfig, TokenConfig
from genesis.core.wallet_tokens import is_stablecoin


def tokens_to_scan(
    rules: RulesConfig,
    *,
    demo: bool = False,
) -> list[TokenConfig]:
    """Tradable tokens to scan — excludes stablecoins (USDT, USDC, DAI, etc.)."""
    tradable = [t for t in rules.allowed_tokens if not is_stablecoin(t.symbol)]
    if not demo:
        return tradable
    return _apply_demo_limit(tradable, rules.loop.demo)


def _apply_demo_limit(tokens: list[TokenConfig], demo: DemoScanConfig) -> list[TokenConfig]:
    """Keep high-liquidity / priority symbols for faster demo cycles."""
    if not tokens:
        return []

    priority = {sym.upper(): idx for idx, sym in enumerate(demo.priority_symbols)}

    def _sort_key(token: TokenConfig) -> tuple[int, int, int]:
        sym = token.symbol.upper()
        if sym in priority:
            return (0, priority[sym], token.cmc_id)
        return (1, token.cmc_id, 0)

    ranked = sorted(tokens, key=_sort_key)
    limit = max(1, demo.token_limit)
    return ranked[: min(limit, len(ranked))]