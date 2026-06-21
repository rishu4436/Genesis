"""Pick the best BUY candidate when multiple assets look bullish."""

from __future__ import annotations

from genesis.core.models import CompositeSignal, RulesConfig
from genesis.decision.adaptive_mode import BuyEligibilityParams, conservative_buy_params

CORE_BUY_COMPONENTS = ("technicals", "sentiment", "onchain", "news")
BULLISH_COMPONENT_MIN = 0.55
MIN_ALIGNED_COMPONENTS = 2


def _market_cap_usd(composite: CompositeSignal) -> float:
    raw = composite.features.get("market_cap_usd")
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _cmc_rank(composite: CompositeSignal) -> int:
    raw = composite.features.get("cmc_rank")
    try:
        rank = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0
    return rank if rank > 0 else 999_999


def buy_alignment_score(
    composite: CompositeSignal,
    *,
    bullish_component_min: float = BULLISH_COMPONENT_MIN,
) -> int:
    """Count core signal components that are independently bullish."""
    return sum(
        1
        for key in CORE_BUY_COMPONENTS
        if composite.components.get(key, 0.5) >= bullish_component_min
    )


def is_buy_eligible(
    composite: CompositeSignal,
    rules: RulesConfig,
    *,
    non_tradable: frozenset[str],
    params: BuyEligibilityParams | None = None,
) -> bool:
    """
    Stricter BUY gate: composite conviction plus multi-signal alignment.

    Requires at least two bullish core components and price/sentiment confirmation.
    """
    gate = params or conservative_buy_params(rules)
    sym = composite.symbol.upper()
    if sym in non_tradable:
        return False

    if composite.direction == "bullish":
        if composite.conviction < gate.buy_conviction_min:
            return False
    elif composite.direction == "neutral" and gate.allow_neutral_direction:
        if composite.conviction < gate.neutral_conviction_min:
            return False
    else:
        return False

    aligned = buy_alignment_score(
        composite,
        bullish_component_min=gate.bullish_component_min,
    )
    if aligned < gate.min_aligned_components:
        return False

    technicals = composite.components.get("technicals", 0.5)
    sentiment = composite.components.get("sentiment", 0.5)
    if technicals < gate.min_technicals and sentiment < gate.min_sentiment:
        return False

    return True


def buy_priority_key(composite: CompositeSignal) -> tuple[float, float, float, float]:
    """
    Sort key for bullish BUY candidates: market cap, alignment, conviction.

    Uses market_cap_usd when present; otherwise lower CMC rank (higher cap) wins.
    """
    mcap = _market_cap_usd(composite)
    rank = _cmc_rank(composite)
    conviction = composite.conviction
    alignment = float(buy_alignment_score(composite))
    if mcap > 0:
        return (mcap, alignment, conviction, -rank)
    if rank < 999_999:
        return (0.0, alignment, -rank, conviction)
    return (0.0, alignment, conviction, 0.0)


def pick_best_buy_candidate(candidates: list[CompositeSignal]) -> CompositeSignal:
    """Choose the bullish candidate with the largest market cap (conviction tiebreak)."""
    return max(candidates, key=buy_priority_key)