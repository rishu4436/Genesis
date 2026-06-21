"""CoinMarketCap Agent Hub — production MCP integration (12 tools)."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, Literal

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from genesis.core.models import CompositeSignal, MarketContext, Signal, SignalCategory
from genesis.data.market_context import build_market_context
from genesis.data.mcp_client import CMCMCPClient
from genesis.data.signal_aggregator import SignalAggregator
from genesis.utils import utc_now

if TYPE_CHECKING:
    from genesis.execution.twak_provider import TWAKProvider

X402Mode = Literal["fallback", "only"]


def _parse_pct(value: Any) -> float:
    """Parse '+1.23%' or numeric percent to float."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("%", "").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _as_dict(data: Any) -> dict[str, Any]:
    """Normalize MCP payloads that may be a dict or single-row list."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        row = data[0]
        return row if isinstance(row, dict) else {}
    return {}


def _extract_cmc_rank(data: Any) -> int:
    """Extract CMC market-cap rank from info, quotes, or nested MCP payloads."""
    if isinstance(data, list):
        for row in data:
            rank = _extract_cmc_rank(row)
            if rank > 0:
                return rank
        return 0
    if not isinstance(data, dict):
        return 0

    for key in ("cmc_rank", "rank", "cmcRank", "market_cap_rank", "marketCapRank"):
        raw = data.get(key)
        if raw is None:
            continue
        try:
            rank = int(raw)
        except (TypeError, ValueError):
            continue
        if rank > 0:
            return rank

    for nested_key in ("quote", "data"):
        nested = data.get(nested_key)
        if isinstance(nested, dict):
            rank = _extract_cmc_rank(nested)
            if rank > 0:
                return rank

    return 0


def _parse_positive_float(raw: Any) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def _rank_tier_score(rank: int) -> float:
    """Higher-ranked (larger cap) assets score better for discovery."""
    if rank <= 0:
        return 0.0
    if rank <= 20:
        return 0.35
    if rank <= 100:
        return 0.25
    if rank <= 300:
        return 0.15
    if rank <= 1000:
        return 0.05
    return -0.10


def _volume_tier_score(volume_24h: float) -> float:
    if volume_24h >= 1_000_000_000:
        return 0.15
    if volume_24h >= 100_000_000:
        return 0.10
    if volume_24h >= 10_000_000:
        return 0.05
    return 0.0


def _discovery_score(
    *,
    exact_id_match: bool,
    symbol_match: bool,
    result_count: int,
    search_index: int,
    matched_row: dict[str, Any] | None,
) -> float:
    """Per-token discoverability — rank and liquidity differentiate assets."""
    if not result_count:
        return -0.4
    if not exact_id_match and not symbol_match:
        return -0.1

    score = 0.0
    if exact_id_match:
        score += 0.15
        if search_index == 0:
            score += 0.10
    elif symbol_match:
        score += 0.05

    row = matched_row or {}
    score += _rank_tier_score(_extract_cmc_rank(row))
    vol = _parse_positive_float(row.get("volume_24h", row.get("volume24h")))
    score += _volume_tier_score(vol)
    return _clamp(score)


def _metadata_conviction(rank: int, is_active: bool) -> float:
    """Map listing rank and active status to metadata signal value."""
    if not is_active:
        return -0.5
    if 0 < rank <= 50:
        return 0.4
    if rank <= 200:
        return 0.2
    if rank > 0:
        return 0.05
    return 0.0


def _build_metadata_summary(symbol: str, rank: int, is_active: bool, tag_count: int) -> str:
    rank_label = str(rank) if rank > 0 else "?"
    return f"{symbol} rank=#{rank_label}, active={bool(is_active)}, tags={tag_count}"


def _nested_count(block: Any, *keys: str) -> float:
    """Read a numeric count from nested dicts like addressesByHoldingTime.holders.count."""
    if not isinstance(block, dict):
        return 0.0
    for key in keys:
        block = block.get(key) if isinstance(block, dict) else None
        if block is None:
            return 0.0
    try:
        return float(block)
    except (TypeError, ValueError):
        return 0.0


def parse_onchain_metrics(data: dict[str, Any]) -> tuple[float, float, float, dict[str, Any]]:
    """
    Parse get_crypto_metrics into holders, traders, signal value, and feature dict.

    CMC uses several shapes:
    - addressesByHoldingTime.{holders,traders,cruisers}.count
    - coinMarketCapCryptoTotalHolderData (growth + latest counts)
    - coinMarketCapCryptoHolderData (concentration + total addresses)
    """
    features: dict[str, Any] = {}
    holders = traders = 0.0

    holding_time = data.get("addressesByHoldingTime", {})
    if isinstance(holding_time, dict):
        holders = _nested_count(holding_time, "holders", "count")
        traders = _nested_count(holding_time, "traders", "count")
        cruisers = _nested_count(holding_time, "cruisers", "count")
        if cruisers > 0:
            features["cruisers"] = cruisers

    total_holder_data = data.get("coinMarketCapCryptoTotalHolderData", {})
    if isinstance(total_holder_data, dict) and total_holder_data:
        features["total_holder_data"] = total_holder_data
        if holders <= 0:
            holders = float(total_holder_data.get("latestCryptoTotalHolderCount", 0) or 0)
        for key in (
            "cryptoTotalHolderCount30dChangePercent",
            "cryptoTotalHolderCount1yChangePercent",
            "cryptoHolderMarketCapUsd30dChangePercent",
        ):
            raw = total_holder_data.get(key)
            if raw is not None:
                features[key] = _parse_pct(raw)

    holder_data = data.get("coinMarketCapCryptoHolderData", {})
    if isinstance(holder_data, dict) and holder_data:
        features["holder_data"] = holder_data
        if holders <= 0:
            holders = float(holder_data.get("totalHolderAddressCount", 0) or 0)
        top10_pct = holder_data.get("top10HolderBalancePercent")
        if top10_pct is not None:
            features["top10_holder_pct"] = _parse_pct(top10_pct)

    value = 0.0
    if holders + traders > 0 and traders > 0:
        # Long-term holders vs recent traders (addressesByHoldingTime semantics)
        ratio = (holders - traders) / (holders + traders)
        value = _clamp(ratio)
    elif features.get("cryptoTotalHolderCount30dChangePercent") is not None:
        change_30d = float(features["cryptoTotalHolderCount30dChangePercent"])
        value = _clamp(change_30d / 30.0)
    elif holders > 0 and features.get("top10_holder_pct") is not None:
        # High whale concentration is mildly bearish; broad holder base mildly bullish
        concentration = float(features["top10_holder_pct"])
        value = _clamp((50.0 - concentration) / 50.0) * 0.5

    features["holders"] = holders
    features["traders"] = traders
    features["data_available"] = bool(
        holders > 0 or traders > 0 or features.get("cryptoTotalHolderCount30dChangePercent") is not None
    )
    return holders, traders, value, features


class CMCProvider:
    """
    CMC Agent Hub integration for Genesis.

    Primary: Official MCP tools at https://mcp.coinmarketcap.com/mcp (API key)
    Optional: x402 endpoint for pay-per-call (~0.01 USDC) — no API key
    Fallback: CMC Pro REST API (when MCP unavailable)
    """

    def __init__(
        self,
        mcp_url: str = "https://mcp.coinmarketcap.com/mcp",
        api_key: str = "",
        x402_enabled: bool = False,
        twak: TWAKProvider | None = None,
        x402_mode: X402Mode = "fallback",
        x402_max_payment: str = "10000",
        x402_prefer_network: str = "bsc",
        aggregator: SignalAggregator | None = None,
    ) -> None:
        self.mcp_url = mcp_url
        self.api_key = api_key
        self.x402_enabled = x402_enabled
        self.x402_mode: X402Mode = x402_mode if api_key else "only"
        if x402_enabled and not api_key:
            self.x402_mode = "only"

        self._mcp = CMCMCPClient(api_key=api_key, mcp_url=mcp_url) if api_key else None
        self._x402_mcp = None
        if x402_enabled:
            if twak is None:
                logger.warning("CMC_X402_ENABLED but TWAK not wired — x402 calls will fail")
            else:
                from genesis.data.x402_mcp_client import X402MCPClient

                self._x402_mcp = X402MCPClient(
                    twak=twak,
                    max_payment=x402_max_payment,
                    prefer_network=x402_prefer_network,
                    wallet_password=getattr(twak, "wallet_password", "") or "",
                )

        self._http = httpx.AsyncClient(timeout=30.0)
        self._global_cache: dict[str, Any] = {}
        self._aggregator = aggregator

    async def close(self) -> None:
        if self._mcp:
            await self._mcp.close()
        if self._x402_mcp:
            await self._x402_mcp.close()
        await self._http.aclose()

    async def health_check(self) -> dict[str, Any]:
        """MCP connectivity and tool inventory."""
        if self._x402_mcp and (self.x402_mode == "only" or not self._mcp):
            return await self._x402_mcp.health_check()
        if self._mcp:
            return await self._mcp.health_check()
        return {"authenticated": False, "error": "Set CMC_API_KEY or CMC_X402_ENABLED=true"}

    async def _call_mcp(self, tool: str, args: dict[str, Any]) -> Any:
        """Call MCP tool via API key and/or x402."""
        if self.x402_enabled and self.x402_mode == "only":
            if not self._x402_mcp:
                raise ValueError("x402 mode requires TWAK provider (CMC_X402_ENABLED=true)")
            return await self._x402_mcp.call_tool(tool, args)

        if self._mcp:
            try:
                return await self._mcp.call_tool(tool, args)
            except Exception as e:
                if self._x402_mcp and self.x402_enabled:
                    logger.info(f"MCP auth call failed, trying x402 for {tool}: {e}")
                    return await self._x402_mcp.call_tool(tool, args)
                raise

        if self._x402_mcp and self.x402_enabled:
            return await self._x402_mcp.call_tool(tool, args)

        raise ValueError("CMC_API_KEY or CMC_X402_ENABLED required for MCP calls")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _api_fallback(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """Fallback to CMC Pro REST API."""
        if not self.api_key:
            raise ValueError("CMC API key required for REST fallback")

        url = f"https://pro-api.coinmarketcap.com/v1/{endpoint}"
        headers = {"X-CMC_PRO_API_KEY": self.api_key, "Accept": "application/json"}
        response = await self._http.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def _safe_mcp(
        self,
        tool: str,
        args: dict[str, Any],
        fallback_endpoint: str | None = None,
        fallback_params: dict[str, Any] | None = None,
    ) -> Any:
        """Try MCP first, fall back to REST API when API key available."""
        try:
            return await self._call_mcp(tool, args)
        except Exception as mcp_err:
            logger.warning(f"MCP {tool} failed: {mcp_err}")
            if fallback_endpoint and fallback_params and self.api_key:
                try:
                    return await self._api_fallback(fallback_endpoint, fallback_params)
                except Exception as api_err:
                    logger.error(f"REST fallback failed for {tool}: {api_err}")
            return {}

    async def _get_global_metrics(self) -> dict[str, Any]:
        """Cached global metrics (fear/greed, market cap)."""
        if not self._global_cache.get("global_metrics"):
            data = await self._safe_mcp("get_global_metrics_latest", {})
            self._global_cache["global_metrics"] = data if isinstance(data, dict) else {}
        return self._global_cache["global_metrics"]

    async def _get_derivatives_global(self) -> dict[str, Any]:
        """Cached global derivatives metrics."""
        if not self._global_cache.get("derivatives"):
            data = await self._safe_mcp("get_global_crypto_derivatives_metrics", {})
            self._global_cache["derivatives"] = data if isinstance(data, dict) else {}
        return self._global_cache["derivatives"]

    def _coin_id(self, symbol: str, cmc_id: int | None) -> str:
        if cmc_id:
            return str(cmc_id)
        raise ValueError(f"CMC id required for {symbol} — add to config/rules.yaml allowlist")

    async def get_usd_price(self, symbol: str, cmc_id: int | None = None) -> float:
        """Latest USD price from CMC quotes (MCP or REST)."""
        coin_id = self._coin_id(symbol, cmc_id)
        data = await self._safe_mcp(
            "get_crypto_quotes_latest",
            {"id": coin_id},
            "cryptocurrency/quotes/latest",
            {"id": coin_id, "convert": "USD"},
        )
        quote = self._extract_quote_row(data, symbol)
        price = quote.get("price")
        if price is None:
            raise ValueError(f"CMC returned no price for {symbol} (id={coin_id})")
        return float(price)

    async def get_usd_prices_batch(
        self,
        tokens: list[tuple[str, int]],
    ) -> dict[str, float]:
        """Fetch multiple USD prices in one CMC quotes call."""
        if not tokens:
            return {}

        id_to_symbol = {cmc_id: sym.upper() for sym, cmc_id in tokens}
        ids = ",".join(str(cmc_id) for _, cmc_id in tokens)
        data = await self._safe_mcp(
            "get_crypto_quotes_latest",
            {"id": ids},
            "cryptocurrency/quotes/latest",
            {"id": ids, "convert": "USD"},
        )
        prices = self._parse_batch_quotes(data, id_to_symbol)

        for sym, cmc_id in tokens:
            key = sym.upper()
            if key not in prices:
                try:
                    prices[key] = await self.get_usd_price(sym, cmc_id)
                except Exception:
                    pass
        return prices

    @staticmethod
    def _parse_batch_quotes(
        data: Any,
        id_to_symbol: dict[int, str],
    ) -> dict[str, float]:
        """Extract symbol → USD price from CMC quotes payload."""
        prices: dict[str, float] = {}
        if not isinstance(data, dict):
            return prices

        nested = data.get("data", data)
        if not isinstance(nested, dict):
            return prices

        for cid, row in nested.items():
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                continue
            sym = id_to_symbol.get(cid_int)
            if not sym or not isinstance(row, dict):
                continue
            usd = row.get("quote", {})
            if isinstance(usd, dict):
                usd = usd.get("USD", usd)
            if isinstance(usd, dict):
                p = usd.get("price")
                if p is not None:
                    prices[sym] = float(p)
        return prices

    async def get_quotes(self, symbol: str, cmc_id: int | None = None) -> Signal:
        """Tool: get_crypto_quotes_latest"""
        coin_id = self._coin_id(symbol, cmc_id)
        data = await self._safe_mcp(
            "get_crypto_quotes_latest",
            {"id": coin_id},
            "cryptocurrency/quotes/latest",
            {"id": coin_id, "convert": "USD"},
        )

        quote = self._extract_quote_row(data, symbol)
        change_24h = _parse_pct(quote.get("percent_change_24h", quote.get("percentChange24h", 0)))
        normalized = _clamp(change_24h / 20.0)

        return Signal(
            category=SignalCategory.QUOTE,
            symbol=symbol,
            value=normalized,
            raw_data=quote,
            source="cmc_mcp:get_crypto_quotes_latest",
            summary=f"{symbol} ${quote.get('price', 0):.2f}, 24h {change_24h:+.2f}%",
        )

    async def get_technicals(self, symbol: str, cmc_id: int | None = None) -> Signal:
        """Tool: get_crypto_technical_analysis"""
        coin_id = self._coin_id(symbol, cmc_id)
        data = _as_dict(
            await self._safe_mcp(
                "get_crypto_technical_analysis",
                {"id": coin_id},
            )
        )

        rsi = float(data.get("rsi", {}).get("rsi14", 50) if isinstance(data.get("rsi"), dict) else 50)
        macd_hist = 0.0
        if isinstance(data.get("macd"), dict):
            try:
                macd_hist = float(data["macd"].get("histogram", 0))
            except (TypeError, ValueError):
                macd_hist = 0.0

        # RSI signal
        if rsi >= 60:
            rsi_val = _clamp((rsi - 50) / 30)
        elif rsi <= 40:
            rsi_val = _clamp((rsi - 50) / 30)
        else:
            rsi_val = 0.0

        # Blend MACD histogram direction
        macd_boost = _clamp(macd_hist / 5.0) * 0.3
        value = _clamp(rsi_val + macd_boost)

        return Signal(
            category=SignalCategory.TECHNICAL,
            symbol=symbol,
            value=value,
            raw_data=data,
            source="cmc_mcp:get_crypto_technical_analysis",
            summary=f"{symbol} RSI14={rsi:.1f}, MACD hist={macd_hist:.2f}",
        )

    async def get_sentiment(self, symbol: str, cmc_id: int | None = None) -> Signal:
        """Tools: get_global_metrics_latest (fear/greed) + search_crypto_info"""
        coin_id = self._coin_id(symbol, cmc_id)

        global_m = await self._get_global_metrics()
        fear_greed = (
            global_m.get("sentiment", {})
            .get("fear_greed", {})
            .get("current", {})
            .get("index", 50)
        )
        try:
            fg_index = float(fear_greed)
        except (TypeError, ValueError):
            fg_index = 50.0

        # Per-asset semantic search for narrative sentiment
        search_data = await self._safe_mcp(
            "search_crypto_info",
            {"id": coin_id, "prompt": f"What is the current market sentiment and outlook for {symbol}?"},
        )
        article_count = 0
        if isinstance(search_data, dict) and "cryptoList" in search_data:
            crypto_list = search_data["cryptoList"]
            if isinstance(crypto_list, list):
                article_count = len(crypto_list)
            elif isinstance(crypto_list, dict):
                article_count = len(crypto_list.get("rows", []))

        # Fear/greed: low fear = contrarian bullish, high greed = cautious
        fg_normalized = _clamp((fg_index - 50) / 50)
        # More research hits = higher engagement (mild bullish bias)
        engagement = _clamp(article_count / 10.0) * 0.2
        value = _clamp(fg_normalized * 0.7 + engagement)

        return Signal(
            category=SignalCategory.SENTIMENT,
            symbol=symbol,
            value=value,
            raw_data={"fear_greed_index": fg_index, "articles": article_count, "search": search_data},
            source="cmc_mcp:get_global_metrics_latest+search_crypto_info",
            summary=f"{symbol} Fear&Greed={fg_index:.0f}, research hits={article_count}",
        )

    async def get_onchain(self, symbol: str, cmc_id: int | None = None) -> Signal:
        """Tool: get_crypto_metrics (holder distribution)"""
        coin_id = self._coin_id(symbol, cmc_id)
        data = _as_dict(await self._safe_mcp("get_crypto_metrics", {"id": coin_id}))
        holders, traders, value, features = parse_onchain_metrics(data)

        if features.get("cryptoTotalHolderCount30dChangePercent") is not None:
            change_30d = features["cryptoTotalHolderCount30dChangePercent"]
            summary = (
                f"{symbol} on-chain holders={holders:.0f}, traders={traders:.0f}, "
                f"30d holder Δ {change_30d:+.1f}%"
            )
        elif not features.get("data_available"):
            summary = f"{symbol} on-chain: no holder breakdown from CMC for this asset"
        else:
            summary = f"{symbol} on-chain holders={holders:.0f}, traders={traders:.0f}"

        return Signal(
            category=SignalCategory.ONCHAIN,
            symbol=symbol,
            value=value,
            raw_data={**data, "parsed": features},
            source="cmc_mcp:get_crypto_metrics",
            summary=summary,
        )

    def _token_momentum_signal(self, symbol: str, quote_signal: Signal) -> Signal:
        """Per-token price/volume momentum from quotes (not global funding)."""
        quote = quote_signal.raw_data
        change_24h = _parse_pct(quote.get("percent_change_24h", quote.get("percentChange24h", 0)))
        change_7d = _parse_pct(quote.get("percent_change_7d", quote.get("percentChange7d", 0)))
        vol_change = _parse_pct(quote.get("volume_change_24h", quote.get("volumeChange24h", 0)))

        momentum = (
            _clamp(change_24h / 12.0) * 0.55
            + _clamp(change_7d / 20.0) * 0.30
            + _clamp(vol_change / 25.0) * 0.15
        )

        return Signal(
            category=SignalCategory.DERIVATIVES,
            symbol=symbol,
            value=_clamp(momentum),
            raw_data={
                "percent_change_24h": change_24h,
                "percent_change_7d": change_7d,
                "volume_change_24h": vol_change,
            },
            source="cmc:token_momentum",
            summary=(
                f"{symbol} momentum 24h {change_24h:+.1f}% "
                f"7d {change_7d:+.1f}% volΔ {vol_change:+.1f}%"
            ),
        )

    async def get_derivatives(self, symbol: str, cmc_id: int | None = None) -> Signal:
        """Per-token momentum signal derived from latest quote."""
        quote = await self.get_quotes(symbol, cmc_id)
        return self._token_momentum_signal(symbol, quote)

    async def get_global_derivatives_signal(self) -> Signal:
        """Market-wide funding/OI bias — applied once per cycle, not per token."""
        data = await self._get_derivatives_global()

        funding_raw = (
            data.get("fundingRate", {}).get("current", 0)
            if isinstance(data.get("fundingRate"), dict)
            else 0
        )
        try:
            funding = float(funding_raw)
        except (TypeError, ValueError):
            funding = 0.0

        oi_change = _parse_pct(
            data.get("totalOpenInterest", {}).get("percentage_change_24h", 0)
            if isinstance(data.get("totalOpenInterest"), dict)
            else 0
        )

        funding_signal = _clamp(-funding * 100)
        oi_signal = _clamp(oi_change / 20.0) * 0.3
        value = _clamp(funding_signal + oi_signal)

        return Signal(
            category=SignalCategory.MARKET,
            symbol="MARKET",
            value=value,
            raw_data=data,
            source="cmc_mcp:get_global_crypto_derivatives_metrics",
            summary=f"Global funding={funding:.4f}, OI 24h {oi_change:+.1f}%",
        )

    async def get_news(self, symbol: str, cmc_id: int | None = None) -> Signal:
        """Tool: get_crypto_latest_news"""
        coin_id = self._coin_id(symbol, cmc_id)
        data = await self._safe_mcp("get_crypto_latest_news", {"id": coin_id})

        rows = data.get("rows", []) if isinstance(data, dict) else []
        bullish_words = re.compile(
            r"\b(surge|rally|bull|growth|adoption|partnership|launch|record|gain)\b", re.I
        )
        bearish_words = re.compile(
            r"\b(crash|drop|bear|hack|exploit|ban|lawsuit|decline|loss|fear)\b", re.I
        )

        bull = bear = 0
        for row in rows[:10]:
            text = " ".join(str(x) for x in row) if isinstance(row, list) else str(row)
            bull += len(bullish_words.findall(text))
            bear += len(bearish_words.findall(text))

        total = bull + bear
        if total > 0:
            score = (bull - bear) / total
            value = _clamp(score)
        else:
            value = 0.0

        return Signal(
            category=SignalCategory.NEWS,
            symbol=symbol,
            value=value,
            raw_data={"headlines": rows[:5], "bullish_hits": bull, "bearish_hits": bear},
            source="cmc_mcp:get_crypto_latest_news",
            summary=f"{symbol} news: {len(rows)} articles, bull/bear {bull}/{bear}",
        )

    def _finalize_metadata_signal(
        self,
        symbol: str,
        data: dict[str, Any],
        *,
        rank: int,
    ) -> Signal:
        """Build metadata signal from info payload and resolved CMC rank."""
        is_active = data.get("is_active", data.get("isActive", True))
        if isinstance(is_active, str):
            is_active = is_active.lower() not in {"false", "0", "no"}

        tags = data.get("tags", [])
        tag_count = len(tags) if isinstance(tags, list) else 0
        raw_data = {**data}
        if rank > 0:
            raw_data["rank"] = rank
            raw_data["cmc_rank"] = rank

        return Signal(
            category=SignalCategory.METADATA,
            symbol=symbol,
            value=_clamp(_metadata_conviction(rank, bool(is_active))),
            raw_data=raw_data,
            source="cmc_mcp:get_crypto_info",
            summary=_build_metadata_summary(symbol, rank, bool(is_active), tag_count),
        )

    async def _resolve_cmc_rank(self, symbol: str, coin_id: str, info_data: dict[str, Any]) -> int:
        """Resolve rank from info payload, with quotes fallback when MCP info omits it."""
        rank = _extract_cmc_rank(info_data)
        if rank > 0:
            return rank

        try:
            quote_payload = await self._safe_mcp(
                "get_crypto_quotes_latest",
                {"id": coin_id},
                "cryptocurrency/quotes/latest",
                {"id": coin_id, "convert": "USD"},
            )
            rank = _extract_cmc_rank(quote_payload)
            if rank <= 0:
                rank = _extract_cmc_rank(self._extract_quote_row(quote_payload, symbol))
        except Exception as e:
            logger.debug(f"Rank fallback via quotes failed for {symbol}: {e}")

        return rank

    async def get_crypto_info_signal(self, symbol: str, cmc_id: int | None = None) -> Signal:
        """Tool: get_crypto_info — listing metadata and rank quality."""
        coin_id = self._coin_id(symbol, cmc_id)
        data = _as_dict(await self._safe_mcp("get_crypto_info", {"id": coin_id}))
        rank = await self._resolve_cmc_rank(symbol, coin_id, data)
        return self._finalize_metadata_signal(symbol, data, rank=rank)

    async def search_cryptos_signal(self, symbol: str, cmc_id: int | None = None) -> Signal:
        """Tool: search_cryptos — confirm discoverability and listing match."""
        data = await self._safe_mcp("search_cryptos", {"query": symbol})
        rows: list[Any] = []
        if isinstance(data, dict):
            for key in ("cryptos", "cryptoList", "data", "rows", "results"):
                block = data.get(key)
                if isinstance(block, list):
                    rows = block
                    break
                if isinstance(block, dict):
                    nested = block.get("rows")
                    if isinstance(nested, list):
                        rows = nested
                        break
        elif isinstance(data, list):
            rows = data

        target = symbol.upper()
        exact_id_match = False
        symbol_match = False
        matched_row: dict[str, Any] | None = None
        search_index = -1

        for idx, row in enumerate(rows[:20]):
            if isinstance(row, dict):
                row_sym = str(row.get("symbol", "")).upper()
                row_id = str(row.get("id", row.get("cmc_id", "")))
                if row_sym == target:
                    symbol_match = True
                    if matched_row is None:
                        matched_row = row
                        search_index = idx
                if cmc_id is not None and row_id == str(cmc_id):
                    exact_id_match = True
                    matched_row = row
                    search_index = idx
            elif isinstance(row, list) and row:
                row_sym = str(row[0]).upper()
                if row_sym == target:
                    symbol_match = True
                    if matched_row is None:
                        matched_row = {"symbol": row_sym}
                        search_index = idx

        value = _discovery_score(
            exact_id_match=exact_id_match,
            symbol_match=symbol_match,
            result_count=len(rows),
            search_index=search_index,
            matched_row=matched_row,
        )
        rank = _extract_cmc_rank(matched_row or {})

        return Signal(
            category=SignalCategory.DISCOVERY,
            symbol=symbol,
            value=value,
            raw_data={
                "rows": rows[:5],
                "result_count": len(rows),
                "matched_row": matched_row,
                "search_index": search_index,
                "rank": rank,
            },
            source="cmc_mcp:search_cryptos",
            summary=(
                f"{symbol} discovery rank=#{rank or '?'}, "
                f"id_match={exact_id_match}, score={value:+.2f}"
            ),
        )

    async def get_marketcap_technicals(self) -> Signal:
        """Tool: get_crypto_marketcap_technical_analysis — total market TA bias."""
        data = _as_dict(
            await self._safe_mcp("get_crypto_marketcap_technical_analysis", {})
        )

        rsi = 50.0
        rsi_block = data.get("rsi", {})
        if isinstance(rsi_block, dict):
            try:
                rsi = float(rsi_block.get("rsi14", rsi_block.get("value", 50)))
            except (TypeError, ValueError):
                rsi = 50.0

        macd_hist = 0.0
        macd_block = data.get("macd", {})
        if isinstance(macd_block, dict):
            try:
                macd_hist = float(macd_block.get("histogram", 0))
            except (TypeError, ValueError):
                macd_hist = 0.0

        if rsi >= 60:
            rsi_val = _clamp((rsi - 50) / 30)
        elif rsi <= 40:
            rsi_val = _clamp((rsi - 50) / 30)
        else:
            rsi_val = 0.0

        value = _clamp(rsi_val + _clamp(macd_hist / 5.0) * 0.3)

        return Signal(
            category=SignalCategory.MARKET,
            symbol="MARKET",
            value=value,
            raw_data=data,
            source="cmc_mcp:get_crypto_marketcap_technical_analysis",
            summary=f"Market-cap TA RSI14={rsi:.1f}, MACD hist={macd_hist:.2f}",
        )

    async def get_macro_signal(self) -> Signal:
        """Tool: get_upcoming_macro_events — macro calendar risk signal."""
        data = await self._safe_mcp("get_upcoming_macro_events", {})
        rows: list[Any] = []
        if isinstance(data, dict):
            for key in ("rows", "events", "eventList", "data"):
                block = data.get(key)
                if isinstance(block, list):
                    rows = block
                    break
                if isinstance(block, dict) and isinstance(block.get("rows"), list):
                    rows = block["rows"]
                    break
        elif isinstance(data, list):
            rows = data

        high_impact = sum(
            1
            for row in rows
            if isinstance(row, dict)
            and str(row.get("impact", row.get("importance", ""))).lower().find("high") >= 0
        )
        event_count = len(rows)

        if high_impact >= 2:
            value = -0.6
        elif high_impact == 1:
            value = -0.3
        elif event_count > 0:
            value = -0.1
        else:
            value = 0.1

        return Signal(
            category=SignalCategory.MACRO,
            symbol="MARKET",
            value=_clamp(value),
            raw_data=data if isinstance(data, dict) else {"rows": rows},
            source="cmc_mcp:get_upcoming_macro_events",
            summary=f"Macro calendar: {event_count} events, {high_impact} high-impact",
        )

    async def fetch_market_context(self, *, hours_ahead: float = 2.0) -> MarketContext:
        """Fetch market-wide CMC tools once per agent cycle."""
        market_result, macro_result, deriv_result = await asyncio.gather(
            self.get_marketcap_technicals(),
            self.get_macro_signal(),
            self.get_global_derivatives_signal(),
            return_exceptions=True,
        )

        market_signal = market_result if isinstance(market_result, Signal) else None
        macro_signal = macro_result if isinstance(macro_result, Signal) else None
        derivatives_signal = deriv_result if isinstance(deriv_result, Signal) else None

        if isinstance(market_result, Exception):
            logger.warning(f"Market-cap TA fetch failed: {market_result}")
        if isinstance(macro_result, Exception):
            logger.warning(f"Macro events fetch failed: {macro_result}")
        if isinstance(deriv_result, Exception):
            logger.warning(f"Global derivatives fetch failed: {deriv_result}")

        return build_market_context(
            market_signal,
            macro_signal,
            derivatives_signal=derivatives_signal,
            hours_ahead=hours_ahead,
        )

    async def get_narratives(self) -> Signal:
        """Tool: trending_crypto_narratives (market-wide)"""
        data = await self._safe_mcp("trending_crypto_narratives", {})
        rows = []
        if isinstance(data, dict) and "categoryList" in data:
            rows = data["categoryList"].get("rows", [])

        # Positive market cap change in trending narratives = risk-on
        changes = []
        for row in rows[:10]:
            if isinstance(row, list) and len(row) > 4:
                changes.append(_parse_pct(row[4]))  # marketCapChangePercent column

        avg_change = sum(changes) / len(changes) if changes else 0.0
        value = _clamp(avg_change / 10.0)

        return Signal(
            category=SignalCategory.COMPOSITE,
            symbol="MARKET",
            value=value,
            raw_data=data,
            source="cmc_mcp:trending_crypto_narratives",
            summary=f"Trending narratives avg cap change {avg_change:+.2f}%",
        )

    async def get_macro_events(self) -> dict[str, Any]:
        """Tool: get_upcoming_macro_events (raw payload)."""
        data = await self._safe_mcp("get_upcoming_macro_events", {})
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"rows": data}
        return {}

    async def get_all_signals(self, symbol: str, cmc_id: int | None = None) -> list[Signal]:
        """Fetch all signal categories concurrently via MCP."""
        tasks = [
            self.get_quotes(symbol, cmc_id),
            self.get_technicals(symbol, cmc_id),
            self.get_sentiment(symbol, cmc_id),
            self.get_onchain(symbol, cmc_id),
            self.get_news(symbol, cmc_id),
            self.get_crypto_info_signal(symbol, cmc_id),
            self.search_cryptos_signal(symbol, cmc_id),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals: list[Signal] = []
        quote_signal: Signal | None = None
        metadata_signal: Signal | None = None

        for result in results:
            if isinstance(result, Signal):
                if result.category == SignalCategory.QUOTE:
                    quote_signal = result
                elif result.category == SignalCategory.METADATA:
                    metadata_signal = result
                signals.append(result)
            elif isinstance(result, Exception):
                logger.warning(f"CMC signal fetch failed: {result}")

        if quote_signal is not None:
            signals.append(self._token_momentum_signal(symbol, quote_signal))
        else:
            signals.append(
                Signal(
                    category=SignalCategory.DERIVATIVES,
                    symbol=symbol,
                    value=0.0,
                    raw_data={},
                    source="cmc:token_momentum",
                    summary=f"{symbol} momentum: no quote data",
                )
            )

        if metadata_signal and quote_signal:
            rank = _extract_cmc_rank(quote_signal.raw_data)
            if rank > 0 and _extract_cmc_rank(metadata_signal.raw_data) <= 0:
                finalized = self._finalize_metadata_signal(
                    metadata_signal.symbol,
                    metadata_signal.raw_data,
                    rank=rank,
                )
                signals = [
                    finalized if s.category == SignalCategory.METADATA else s
                    for s in signals
                ]

        return signals

    async def get_composite_signal(
        self,
        symbol: str,
        cmc_id: int | None = None,
        aggregator: SignalAggregator | None = None,
    ) -> CompositeSignal:
        """Fetch all signals and fuse into composite conviction score."""
        agg = aggregator or self._aggregator
        if not agg:
            raise ValueError("SignalAggregator required for get_composite_signal")

        signals = await self.get_all_signals(symbol, cmc_id)
        return agg.aggregate(symbol, signals)

    def _extract_quote_row(self, data: Any, symbol: str) -> dict[str, Any]:
        """Normalize quote response shapes from MCP or REST."""
        if isinstance(data, list) and data:
            row = data[0]
            return row if isinstance(row, dict) else {}
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, dict):
                for k, v in nested.items():
                    if str(k).upper() == symbol.upper():
                        if isinstance(v, dict) and "quote" in v:
                            return v["quote"].get("USD", v)
                        return v if isinstance(v, dict) else {}
            return data
        return {}