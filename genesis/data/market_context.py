"""Market-wide CMC context — macro events and market-cap technical bias."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from genesis.core.models import CompositeSignal, MarketContext, RulesConfig, Signal, SignalCategory


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _parse_event_time(raw: Any) -> datetime | None:
    """Best-effort parse of macro event timestamps from MCP payloads."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        ts = float(text)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _impact_score(raw: Any) -> float:
    """Map impact labels to 0–1 severity."""
    text = str(raw or "").strip().lower()
    if not text:
        return 0.5
    if "high" in text or "critical" in text:
        return 1.0
    if "medium" in text or "moderate" in text:
        return 0.6
    if "low" in text:
        return 0.3
    return 0.5


def _extract_macro_rows(data: Any) -> list[dict[str, Any]]:
    """Normalize macro event payloads to row dicts."""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []

    for key in ("rows", "events", "eventList", "data"):
        block = data.get(key)
        if isinstance(block, list):
            return [row for row in block if isinstance(row, dict)]
        if isinstance(block, dict):
            nested_rows = block.get("rows")
            if isinstance(nested_rows, list):
                return [row for row in nested_rows if isinstance(row, dict)]
    return []


def _macro_row_fields(row: dict[str, Any]) -> tuple[str, datetime | None, float]:
    """Extract name, time, and impact from a macro event row."""
    name = str(
        row.get("eventName")
        or row.get("name")
        or row.get("title")
        or row.get("event")
        or "Macro event"
    )
    when = _parse_event_time(
        row.get("eventTime")
        or row.get("time")
        or row.get("date")
        or row.get("startTime")
        or row.get("timestamp")
    )
    impact = _impact_score(
        row.get("impact")
        or row.get("importance")
        or row.get("impactLevel")
        or row.get("severity")
    )
    return name, when, impact


def macro_blocks_buys(
    data: Any,
    *,
    hours_ahead: float = 2.0,
    min_impact: float = 0.6,
) -> tuple[bool, str]:
    """True when a high-impact macro event is within the lookahead window."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=hours_ahead)
    rows = _extract_macro_rows(data)

    for row in rows:
        name, when, impact = _macro_row_fields(row)
        if when is None or impact < min_impact:
            continue
        if now <= when <= horizon:
            delta_min = int((when - now).total_seconds() / 60)
            return True, f"Macro event '{name}' in {delta_min}m (impact={impact:.1f})"

    return False, ""


def build_market_context(
    market_signal: Signal | None,
    macro_signal: Signal | None,
    *,
    derivatives_signal: Signal | None = None,
    hours_ahead: float = 2.0,
) -> MarketContext:
    """Assemble cycle-level market context from market-wide CMC signals."""
    signals: list[Signal] = []
    blocks_buys = False
    block_reason = ""
    market_delta = 0.0

    if market_signal is not None:
        signals.append(market_signal)
        market_delta += _clamp(market_signal.value * 0.03, -0.05, 0.05)

    if derivatives_signal is not None:
        signals.append(derivatives_signal)
        market_delta += _clamp(derivatives_signal.value * 0.02, -0.03, 0.03)

    if macro_signal is not None:
        signals.append(macro_signal)
        blocks_buys, block_reason = macro_blocks_buys(
            macro_signal.raw_data,
            hours_ahead=hours_ahead,
        )

    return MarketContext(
        signals=signals,
        blocks_buys=blocks_buys,
        block_reason=block_reason,
        market_conviction_delta=market_delta,
    )


def direction_for_conviction(conviction: float, rules: RulesConfig) -> str:
    """Map conviction score to bullish/bearish/neutral."""
    thresholds = rules.signals
    if conviction >= thresholds.buy_conviction_min:
        return "bullish"
    if conviction <= thresholds.sell_conviction_max:
        return "bearish"
    return "neutral"


def apply_market_context(
    composites: list[CompositeSignal],
    context: MarketContext,
    rules: RulesConfig,
) -> list[CompositeSignal]:
    """Shift per-token conviction using market-cap technical bias."""
    if not context.market_conviction_delta:
        return composites

    adjusted: list[CompositeSignal] = []
    delta = context.market_conviction_delta
    for composite in composites:
        conviction = max(0.0, min(1.0, composite.conviction + delta))
        direction = direction_for_conviction(conviction, rules)
        adjusted.append(
            composite.model_copy(
                update={
                    "conviction": conviction,
                    "direction": direction,
                    "features": {
                        **composite.features,
                        "market_ta_bias": delta,
                    },
                    "summary": (
                        f"{composite.symbol}: conviction={conviction:.2f} ({direction}) "
                        f"[market_ta={delta:+.3f}]"
                    ),
                }
            )
        )
    return adjusted


def symbol_in_narrative_text(symbol: str, text: str) -> bool:
    """Check if a token symbol appears as a word in narrative text."""
    if not text:
        return False
    return bool(re.search(rf"\b{re.escape(symbol.upper())}\b", text.upper()))