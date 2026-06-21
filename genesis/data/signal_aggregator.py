"""Fuse multiple CMC signals into composite conviction score."""

from __future__ import annotations

from loguru import logger

from typing import Any

from genesis.core.models import CompositeSignal, RulesConfig, Signal, SignalCategory
from genesis.decision.candidate_selection import buy_alignment_score
from genesis.utils import utc_now


def _parse_float(raw: Any) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def _parse_rank(raw: Any) -> int:
    try:
        rank = int(raw)
    except (TypeError, ValueError):
        return 0
    return rank if rank > 0 else 0


def _change_24h_from_signals(signals: list[Signal]) -> float | None:
    """24h percent change from CMC quote signal."""
    for signal in signals:
        if signal.category != SignalCategory.QUOTE:
            continue
        raw = signal.raw_data or {}
        for key in ("percent_change_24h", "percentChange24h"):
            if key not in raw:
                continue
            try:
                return float(raw[key])
            except (TypeError, ValueError):
                continue
    return None


def _price_usd_from_signals(signals: list[Signal]) -> float | None:
    """Latest USD spot price from CMC quote signal raw_data."""
    for signal in signals:
        if signal.category != SignalCategory.QUOTE:
            continue
        raw = signal.raw_data or {}
        price_raw = raw.get("price")
        if price_raw is None:
            continue
        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def _extract_cmc_rank(data: Any) -> int:
    if isinstance(data, list):
        for row in data:
            rank = _extract_cmc_rank(row)
            if rank > 0:
                return rank
        return 0
    if not isinstance(data, dict):
        return 0

    for key in ("cmc_rank", "rank", "cmcRank", "market_cap_rank", "marketCapRank"):
        rank = _parse_rank(data.get(key))
        if rank > 0:
            return rank

    for nested_key in ("quote", "data"):
        nested = data.get(nested_key)
        if isinstance(nested, dict):
            rank = _extract_cmc_rank(nested)
            if rank > 0:
                return rank
    return 0


def _extract_market_cap_usd(data: Any) -> float:
    if isinstance(data, list):
        for row in data:
            cap = _extract_market_cap_usd(row)
            if cap > 0:
                return cap
        return 0.0
    if not isinstance(data, dict):
        return 0.0

    for key in ("market_cap", "marketCap", "fully_diluted_market_cap", "fullyDilutedMarketCap"):
        cap = _parse_float(data.get(key))
        if cap > 0:
            return cap

    for nested_key in ("quote", "data"):
        nested = data.get(nested_key)
        if isinstance(nested, dict):
            cap = _extract_market_cap_usd(nested)
            if cap > 0:
                return cap
    return 0.0


def _market_metrics_from_signals(signals: list[Signal]) -> tuple[int, float]:
    rank = 0
    market_cap = 0.0
    for signal in signals:
        if signal.category == SignalCategory.QUOTE:
            market_cap = max(market_cap, _extract_market_cap_usd(signal.raw_data))
            rank = rank or _extract_cmc_rank(signal.raw_data)
        elif signal.category == SignalCategory.METADATA:
            rank = rank or _extract_cmc_rank(signal.raw_data)
    return rank, market_cap


class SignalAggregator:
    """Aggregate multi-source signals with configurable weights."""

    CATEGORY_KEY_MAP = {
        SignalCategory.TECHNICAL: "technicals",
        SignalCategory.SENTIMENT: "sentiment",
        SignalCategory.DERIVATIVES: "derivatives",
        SignalCategory.ONCHAIN: "onchain",
        SignalCategory.NEWS: "news",
        SignalCategory.QUOTE: "quote",
        SignalCategory.METADATA: "metadata",
        SignalCategory.DISCOVERY: "discovery",
    }

    def __init__(self, rules: RulesConfig) -> None:
        self.rules = rules
        self._last_conviction: dict[str, float] = {}

    def aggregate(self, symbol: str, signals: list[Signal]) -> CompositeSignal:
        """Fuse signals into composite conviction score (0-1)."""
        weights = self.rules.signal_weights
        weight_map = {
            "technicals": weights.technicals,
            "sentiment": weights.sentiment,
            "derivatives": weights.derivatives,
            "onchain": weights.onchain,
            "news": weights.news,
            "metadata": weights.metadata,
            "discovery": weights.discovery,
        }

        components: dict[str, float] = {}
        weighted_sum = 0.0
        total_weight = 0.0

        for signal in signals:
            key = self.CATEGORY_KEY_MAP.get(signal.category)
            if not key or key == "quote":
                continue

            w = weight_map.get(key, 0.0)
            if w <= 0:
                continue

            # Normalize signal value (-1 to 1) to conviction contribution (0 to 1)
            contribution = (signal.value + 1.0) / 2.0
            components[key] = contribution
            weighted_sum += contribution * w
            total_weight += w

        if total_weight == 0:
            conviction = 0.5
            direction = "neutral"
        else:
            conviction = weighted_sum / total_weight
            thresholds = self.rules.signals
            if conviction >= thresholds.buy_conviction_min:
                direction = "bullish"
            elif conviction <= thresholds.sell_conviction_max:
                direction = "bearish"
            else:
                direction = "neutral"

        cmc_rank, market_cap_usd = _market_metrics_from_signals(signals)
        price_usd = _price_usd_from_signals(signals)
        change_24h_pct = _change_24h_from_signals(signals)
        alignment_probe = CompositeSignal(
            symbol=symbol,
            conviction=conviction,
            direction=direction,
            components=components,
        )
        features = {
            "funding_neutral": self._check_funding_neutral(signals),
            "sentiment_positive": self._check_sentiment_positive(signals),
            "technicals_bullish": components.get("technicals", 0.5) > 0.6,
            "onchain_accumulation": components.get("onchain", 0.5) > 0.55,
            "news_supportive": components.get("news", 0.5) > 0.55,
            "cmc_rank": cmc_rank,
            "market_cap_usd": market_cap_usd,
            "price_usd": price_usd,
            "change_24h_pct": change_24h_pct,
            "buy_alignment": buy_alignment_score(alignment_probe),
        }

        summary_parts = [
            f"{k}={v:.2f}" for k, v in sorted(components.items())
        ]
        summary = (
            f"{symbol}: conviction={conviction:.2f} ({direction}) "
            f"[{', '.join(summary_parts)}]"
        )

        composite = CompositeSignal(
            symbol=symbol,
            conviction=conviction,
            direction=direction,
            components=components,
            features=features,
            timestamp=utc_now(),
            summary=summary,
        )

        self._last_conviction[symbol] = conviction
        logger.info(summary)
        return composite

    def has_significant_change(self, symbol: str, new_conviction: float) -> bool:
        """Check if conviction changed beyond threshold."""
        prev = self._last_conviction.get(symbol)
        if prev is None:
            return True
        threshold = self.rules.loop.signal_change_threshold
        return abs(new_conviction - prev) >= threshold

    def _check_funding_neutral(self, signals: list[Signal]) -> bool:
        """Check if funding rate is within neutral band."""
        for s in signals:
            if s.category == SignalCategory.DERIVATIVES:
                raw = s.raw_data
                funding_raw = raw.get("funding_rate", raw.get("fundingRate", 0))
                if isinstance(funding_raw, dict):
                    funding_raw = funding_raw.get("current", 0)
                try:
                    funding = abs(float(funding_raw))
                except (TypeError, ValueError):
                    funding = 0.0
                return funding <= self.rules.signals.funding_rate_neutral_max
        return True

    def _check_sentiment_positive(self, signals: list[Signal]) -> bool:
        """Check if sentiment exceeds positive threshold."""
        for s in signals:
            if s.category == SignalCategory.SENTIMENT:
                score = (s.value + 1.0) / 2.0
                return score >= self.rules.signals.sentiment_positive_min
        return False