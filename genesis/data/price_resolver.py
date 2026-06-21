"""USD price resolution — CoinMarketCap first (hackathon reference), TWAK fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from genesis.core.models import TokenConfig
from genesis.core.wallet_tokens import is_gas_or_stable
from genesis.data.price_cache import cache_get, cache_set

if TYPE_CHECKING:
    from genesis.data.cmc_provider import CMCProvider
    from genesis.execution.twak_provider import TWAKProvider

STABLE_USD: dict[str, float] = {
    "USDT": 1.0,
    "USDC": 1.0,
    "BUSD": 1.0,
    "DAI": 1.0,
    "TUSD": 1.0,
    "FDUSD": 1.0,
    "USDE": 1.0,
    "U": 1.0,
}

PRICE_TTL = 30.0


class PriceResolver:
    """Fetch USD prices: stables → CMC quotes → TWAK DEX price."""

    def __init__(
        self,
        twak: TWAKProvider,
        cmc: CMCProvider | None = None,
    ) -> None:
        self.twak = twak
        self.cmc = cmc

    def stable_price(self, symbol: str) -> float | None:
        return STABLE_USD.get(symbol.upper())

    async def usd_price_for_token(self, token: TokenConfig) -> tuple[float, str]:
        """Return (price, source) where source is stable|cmc|twak|cache."""
        sym = token.symbol.upper()
        stable = self.stable_price(sym)
        if stable is not None:
            return stable, "stable"

        cache_key = f"usd:{sym}"
        cached = cache_get(cache_key)
        if cached is not None:
            entry = cached if isinstance(cached, tuple) else (float(cached), "cache")
            return float(entry[0]), str(entry[1])

        if self.cmc and token.cmc_id:
            try:
                price = await self.cmc.get_usd_price(token.symbol, token.cmc_id)
                if price > 0:
                    cache_set(cache_key, (price, "cmc"), PRICE_TTL)
                    return price, "cmc"
            except Exception as e:
                logger.debug(f"CMC price for {token.symbol}: {e}")

        try:
            price = await self.twak.get_price_for_token(token.symbol, token.address)
            if price > 0:
                cache_set(cache_key, (price, "twak"), PRICE_TTL)
                return price, "twak"
        except Exception as e:
            logger.debug(f"TWAK price for {token.symbol}: {e}")

        return 0.0, "none"

    async def usd_prices_batch(
        self,
        tokens: list[TokenConfig],
    ) -> dict[str, tuple[float, str]]:
        """Batch-fetch CMC quotes where possible; per-token fallback otherwise."""
        result: dict[str, tuple[float, str]] = {}

        for token in tokens:
            stable = self.stable_price(token.symbol)
            if stable is not None:
                result[token.symbol.upper()] = (stable, "stable")

        need_cmc = [
            t for t in tokens
            if t.symbol.upper() not in result and t.cmc_id and self.cmc
        ]

        if need_cmc and self.cmc:
            try:
                batch = await self.cmc.get_usd_prices_batch(
                    [(t.symbol, t.cmc_id) for t in need_cmc if t.cmc_id]
                )
                for sym, price in batch.items():
                    if price > 0:
                        result[sym.upper()] = (price, "cmc")
                        cache_set(f"usd:{sym.upper()}", (price, "cmc"), PRICE_TTL)
            except Exception as e:
                logger.warning(f"CMC batch quotes failed: {e}")

        for token in tokens:
            key = token.symbol.upper()
            if key in result:
                continue
            price, source = await self.usd_price_for_token(token)
            if price > 0:
                result[key] = (price, source)

        return result