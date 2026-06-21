"""Switch to relaxed buy rules after consecutive cycles with no executed swap."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from genesis.core.models import CompositeSignal, RulesConfig


@dataclass(frozen=True)
class BuyEligibilityParams:
    """Configurable gates for rule-based BUY candidate selection."""

    buy_conviction_min: float
    min_aligned_components: int = 2
    bullish_component_min: float = 0.55
    min_technicals: float = 0.52
    min_sentiment: float = 0.55
    allow_neutral_direction: bool = False
    neutral_conviction_min: float = 0.55


def audit_had_swap(audit: dict[str, Any]) -> bool:
    """True when the cycle recorded an executed or simulated swap."""
    trade = audit.get("trade")
    if not isinstance(trade, dict):
        return False
    if trade.get("tx_hash"):
        return True
    return bool(trade.get("simulated"))


def count_consecutive_idle_swap_cycles(audits: list[dict[str, Any]]) -> int:
    """Count recent cycles (newest first) since the last successful swap."""
    idle = 0
    for audit in audits:
        if audit_had_swap(audit):
            break
        idle += 1
    return idle


def adaptive_aggression_enabled(rules: RulesConfig) -> bool:
    cfg = rules.loop.adaptive_aggression
    return bool(cfg and cfg.enabled)


def is_aggressive_mode(rules: RulesConfig, idle_swap_cycles: int) -> bool:
    if not adaptive_aggression_enabled(rules):
        return False
    threshold = rules.loop.adaptive_aggression.idle_cycles_threshold
    return idle_swap_cycles >= threshold


def conservative_buy_params(rules: RulesConfig) -> BuyEligibilityParams:
    return BuyEligibilityParams(
        buy_conviction_min=rules.signals.buy_conviction_min,
    )


def aggressive_buy_params(rules: RulesConfig) -> BuyEligibilityParams:
    cfg = rules.loop.adaptive_aggression
    return BuyEligibilityParams(
        buy_conviction_min=cfg.buy_conviction_min,
        min_aligned_components=cfg.min_aligned_components,
        bullish_component_min=cfg.bullish_component_min,
        min_technicals=cfg.min_technicals,
        min_sentiment=cfg.min_sentiment,
        allow_neutral_direction=cfg.allow_neutral_direction,
        neutral_conviction_min=cfg.neutral_conviction_min,
    )


def buy_params_for_idle(rules: RulesConfig, idle_swap_cycles: int) -> BuyEligibilityParams:
    if is_aggressive_mode(rules, idle_swap_cycles):
        return aggressive_buy_params(rules)
    return conservative_buy_params(rules)


def pick_force_buy_candidate(
    composites: list[CompositeSignal],
    params: BuyEligibilityParams,
    *,
    non_tradable: frozenset[str],
) -> CompositeSignal | None:
    """Last-resort BUY when aggressive mode must place at least one swap."""
    if not composites:
        return None

    ranked: list[CompositeSignal] = []
    for composite in composites:
        sym = composite.symbol.upper()
        if sym in non_tradable:
            continue
        if composite.direction == "bearish":
            continue

        min_conv = params.buy_conviction_min
        if composite.direction == "neutral":
            if not params.allow_neutral_direction:
                continue
            min_conv = max(min_conv, params.neutral_conviction_min)
        elif composite.direction != "bullish":
            continue

        if composite.conviction >= min_conv:
            ranked.append(composite)

    if not ranked:
        ranked = [
            c
            for c in composites
            if c.symbol.upper() not in non_tradable and c.direction != "bearish"
        ]

    if not ranked:
        return None

    from genesis.decision.candidate_selection import pick_best_buy_candidate

    return pick_best_buy_candidate(ranked)