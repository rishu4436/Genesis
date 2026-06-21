"""Format agent signal fusion and decision reasoning for the dashboard logic panel."""

from __future__ import annotations

from typing import Any

from dashboard.holdings import _format_decision_price_line
from genesis.core.config import RulesConfig
from genesis.core.models import SignalCategory

COMPONENT_ORDER = ("technicals", "sentiment", "derivatives", "onchain", "news", "quote")

CATEGORY_TO_COMPONENT: dict[str, str] = {
    SignalCategory.TECHNICAL.value: "technicals",
    "technical": "technicals",
    "technicals": "technicals",
    SignalCategory.SENTIMENT.value: "sentiment",
    SignalCategory.DERIVATIVES.value: "derivatives",
    SignalCategory.ONCHAIN.value: "onchain",
    SignalCategory.NEWS.value: "news",
    SignalCategory.QUOTE.value: "quote",
    "quote": "quote",
}


def _normalize_category(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("name") or ""
    return str(raw).lower().strip()


def _signal_contribution(value: float) -> float:
    return round((float(value) + 1.0) / 2.0, 4)


def _format_components(components: dict[str, float]) -> str:
    parts = [
        f"{key}={components[key]:.2f}"
        for key in COMPONENT_ORDER
        if key in components
    ]
    for key in sorted(components):
        if key not in COMPONENT_ORDER:
            parts.append(f"{key}={components[key]:.2f}")
    return "[" + ", ".join(parts) + "]"


def _infer_direction(conviction: float, rules: RulesConfig) -> str:
    thresholds = rules.signals
    if conviction >= thresholds.buy_conviction_min:
        return "bullish"
    if conviction <= thresholds.sell_conviction_max:
        return "bearish"
    return "neutral"


def _infer_composites_from_signals(
    signals: list[dict[str, Any]],
    rules: RulesConfig,
) -> list[dict[str, Any]]:
    """Rebuild per-token composites from raw CMC signals (legacy audits)."""
    if not signals:
        return []

    by_symbol: dict[str, dict[str, float]] = {}
    details: dict[str, list[dict[str, Any]]] = {}

    for sig in signals:
        if not isinstance(sig, dict):
            continue
        symbol = str(sig.get("symbol", "")).upper()
        if not symbol:
            continue
        cat = _normalize_category(sig.get("category"))
        key = CATEGORY_TO_COMPONENT.get(cat, cat or "other")
        if key == "quote":
            continue
        contribution = _signal_contribution(sig.get("value", 0))
        by_symbol.setdefault(symbol, {})[key] = contribution
        details.setdefault(symbol, []).append(_serialize_signal(sig))

    weights = rules.signal_weights
    weight_map = {
        "technicals": weights.technicals,
        "sentiment": weights.sentiment,
        "derivatives": weights.derivatives,
        "onchain": weights.onchain,
        "news": weights.news,
    }

    composites: list[dict[str, Any]] = []
    for symbol, components in by_symbol.items():
        weighted_sum = 0.0
        total_weight = 0.0
        for key, val in components.items():
            w = weight_map.get(key, 0.0)
            if w <= 0:
                continue
            weighted_sum += val * w
            total_weight += w
        conviction = weighted_sum / total_weight if total_weight else 0.5
        composites.append(
            {
                "symbol": symbol,
                "conviction": round(conviction, 4),
                "direction": _infer_direction(conviction, rules),
                "components": components,
                "features": {},
                "summary": _format_components(components).strip("[]"),
                "_signals": details.get(symbol, []),
                "_inferred": True,
            }
        )
    return composites


def _composites_from_audit(audit: dict[str, Any], rules: RulesConfig) -> list[dict[str, Any]]:
    """Extract composite rows from audit (composites[], signals[], or legacy composite)."""
    stored = audit.get("composites") or []
    if stored:
        return [c for c in stored if isinstance(c, dict) and c.get("symbol")]

    signals = audit.get("signals") or []
    inferred = _infer_composites_from_signals(signals, rules)
    if inferred:
        return inferred

    single = audit.get("composite")
    if isinstance(single, dict) and single.get("symbol"):
        return [single]
    return []


def _serialize_signal(sig: dict[str, Any]) -> dict[str, Any]:
    cat = _normalize_category(sig.get("category"))
    key = CATEGORY_TO_COMPONENT.get(cat, cat)
    raw = sig.get("raw_data") if isinstance(sig.get("raw_data"), dict) else {}
    return {
        "category": key or cat,
        "value": round(float(sig.get("value", 0)), 4),
        "contribution": round(_signal_contribution(sig.get("value", 0)), 4),
        "summary": sig.get("summary") or "",
        "source": sig.get("source") or "cmc",
        "highlights": _signal_highlights(key, raw),
    }


def _coerce_float(value: Any) -> float | None:
    """Best-effort numeric parse (handles nested CMC rsi blocks)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("rsi14", "value", "rsi"):
            if key in value and value[key] is not None:
                parsed = _coerce_float(value[key])
                if parsed is not None:
                    return parsed
        return None
    if isinstance(value, str):
        try:
            return float(value.strip().replace("%", ""))
        except ValueError:
            return None
    return None


def _format_count(value: float | int) -> str:
    """Compact display for large holder/trader counts."""
    n = float(value)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def _extract_token_metrics(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull headline metrics (RSI, holders, etc.) from serialized CMC signals."""
    metrics: dict[str, Any] = {}
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        serialized = sig if "highlights" in sig else _serialize_signal(sig)
        cat = str(serialized.get("category") or "")
        highlights = serialized.get("highlights") or {}
        if cat == "technicals":
            rsi = _coerce_float(highlights.get("rsi14", highlights.get("rsi")))
            if rsi is not None:
                metrics["rsi14"] = rsi
            for key in ("macd", "trend", "signal", "recommendation"):
                if key in highlights and highlights[key] is not None:
                    val = highlights[key]
                    metrics[key] = val if isinstance(val, (str, int, float, bool)) else str(val)
        elif cat == "onchain":
            mapping = {
                "holders": "holders",
                "traders": "traders",
                "cruisers": "cruisers",
                "cryptoTotalHolderCount30dChangePercent": "holder_change_30d",
                "top10_holder_pct": "top10_holder_pct",
            }
            for src, dest in mapping.items():
                if src in highlights and highlights[src] is not None:
                    num = _coerce_float(highlights[src])
                    metrics[dest] = num if num is not None else highlights[src]
        elif cat == "sentiment":
            for key in ("score", "sentiment", "social_volume", "galaxy_score"):
                if key in highlights and highlights[key] is not None:
                    metrics[key] = highlights[key]
        elif cat == "derivatives":
            for key in ("funding_rate", "open_interest", "long_short_ratio"):
                if key in highlights and highlights[key] is not None:
                    metrics[key] = highlights[key]
        elif cat == "quote":
            for key in ("price", "percent_change_24h", "volume_24h", "market_cap"):
                if key in highlights and highlights[key] is not None:
                    num = _coerce_float(highlights[key])
                    metrics[key] = num if num is not None else highlights[key]
    return metrics


def _build_chip_preview(metrics: dict[str, Any]) -> str:
    """One-line preview for the insight chip (RSI, holders)."""
    parts: list[str] = []
    if metrics.get("rsi14") is not None:
        parts.append(f"RSI {float(metrics['rsi14']):.0f}")
    if metrics.get("holders") is not None:
        parts.append(f"{_format_count(metrics['holders'])} holders")
    return " · ".join(parts)


def _build_logic_explanation(row: dict[str, Any], rules: RulesConfig) -> list[str]:
    """Human-readable bullets explaining conviction and direction."""
    lines: list[str] = []
    conviction = float(row.get("conviction", 0.5))
    direction = str(row.get("direction") or "neutral")
    thresholds = rules.signals
    buy_min = thresholds.buy_conviction_min
    sell_max = thresholds.sell_conviction_max

    if direction == "bullish":
        lines.append(
            f"Conviction {conviction:.2f} is at or above the buy threshold ({buy_min:.2f}) → bullish."
        )
    elif direction == "bearish":
        lines.append(
            f"Conviction {conviction:.2f} is at or below the sell threshold ({sell_max:.2f}) → bearish."
        )
    else:
        lines.append(
            f"Conviction {conviction:.2f} sits between sell ({sell_max:.2f}) and buy ({buy_min:.2f}) → neutral."
        )

    components = row.get("components") or {}
    weights = row.get("weights") or {}
    weighted_parts: list[str] = []
    for key in COMPONENT_ORDER:
        if key not in components:
            continue
        weight = float(weights.get(key, 0) or 0)
        if weight <= 0:
            continue
        val = float(components[key])
        weighted_parts.append(f"{key} {val:.2f}×{weight:.0%}")
    if weighted_parts:
        lines.append("Weighted signal blend: " + ", ".join(weighted_parts) + ".")

    for key in COMPONENT_ORDER:
        if key not in components:
            continue
        val = float(components[key])
        label = key.capitalize()
        if val >= 0.6:
            lines.append(f"{label} component {val:.2f} reads bullish (≥0.60).")
        elif val <= 0.4:
            lines.append(f"{label} component {val:.2f} reads bearish (≤0.40).")

    metrics = row.get("metrics") or {}
    rsi = metrics.get("rsi14")
    if rsi is not None:
        rsi_f = float(rsi)
        if rsi_f >= 60:
            lines.append(f"RSI {rsi_f:.1f} is elevated (≥60) — momentum tilts bullish on technicals.")
        elif rsi_f <= 40:
            lines.append(f"RSI {rsi_f:.1f} is depressed (≤40) — momentum tilts bearish on technicals.")
        else:
            lines.append(f"RSI {rsi_f:.1f} is neutral (40–60) — no strong RSI tilt.")

    holders = metrics.get("holders")
    if holders is not None:
        lines.append(f"On-chain: {_format_count(holders)} wallet holders (CMC).")
        change = metrics.get("holder_change_30d")
        if change is not None:
            change_f = float(change)
            if change_f > 5:
                lines.append(f"Holder count up {change_f:+.1f}% over 30d — accumulation bias.")
            elif change_f < -5:
                lines.append(f"Holder count down {change_f:+.1f}% over 30d — distribution bias.")
            else:
                lines.append(f"Holder count changed {change_f:+.1f}% over 30d — stable base.")

    traders = metrics.get("traders")
    if traders is not None and holders is not None:
        lines.append(
            f"Active traders: {_format_count(traders)} vs holders {_format_count(holders)}."
        )

    macd = metrics.get("macd")
    if macd is not None:
        lines.append(f"MACD context: {macd}.")

    if row.get("is_decision_target"):
        lines.append("This token was selected as the latest cycle decision target.")

    return lines


def _enrich_token(row: dict[str, Any], rules: RulesConfig) -> dict[str, Any]:
    metrics = _extract_token_metrics(row.get("signals") or [])
    row["metrics"] = metrics
    row["chip_preview"] = _build_chip_preview(metrics)
    row["logic_lines"] = _build_logic_explanation({**row, "metrics": metrics}, rules)
    return row


def _signal_highlights(category: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Pull readable fields from CMC MCP raw payloads."""
    if not raw:
        return {}
    highlights: dict[str, Any] = {}
    if category == "technicals":
        for k in ("rsi", "macd", "trend", "signal", "recommendation"):
            if k in raw and raw[k] is not None:
                highlights[k] = raw[k]
    elif category == "sentiment":
        for k in ("score", "sentiment", "social_volume", "galaxy_score"):
            if k in raw and raw[k] is not None:
                highlights[k] = raw[k]
    elif category == "derivatives":
        for k in ("funding_rate", "open_interest", "long_short_ratio"):
            if k in raw and raw[k] is not None:
                highlights[k] = raw[k]
    elif category == "onchain":
        parsed = raw.get("parsed") if isinstance(raw.get("parsed"), dict) else {}
        for k in (
            "holders",
            "traders",
            "cruisers",
            "cryptoTotalHolderCount30dChangePercent",
            "top10_holder_pct",
            "data_available",
        ):
            if k in parsed and parsed[k] is not None:
                highlights[k] = parsed[k]
        for k in ("netflow", "active_addresses", "whale_activity", "accumulation"):
            if k in raw and raw[k] is not None:
                highlights[k] = raw[k]
    elif category == "news":
        for k in ("headline", "title", "sentiment", "count"):
            if k in raw and raw[k] is not None:
                highlights[k] = raw[k]
    elif category == "quote":
        for k in ("price", "percent_change_24h", "volume_24h", "market_cap"):
            if k in raw and raw[k] is not None:
                highlights[k] = raw[k]
    if not highlights and raw:
        for k, v in list(raw.items())[:6]:
            if isinstance(v, (str, int, float, bool)):
                highlights[k] = v
    return highlights


def _signals_for_symbol(audit: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    symbol = symbol.upper()
    signals = audit.get("signals") or []
    return [
        _serialize_signal(s)
        for s in signals
        if isinstance(s, dict) and str(s.get("symbol", "")).upper() == symbol
    ]


def _token_row(
    composite: dict[str, Any],
    audit: dict[str, Any],
    rules: RulesConfig,
    decision_asset: str,
) -> dict[str, Any]:
    symbol = str(composite.get("symbol", "")).upper()
    components = composite.get("components") or {}
    if isinstance(components, dict):
        components = {str(k): float(v) for k, v in components.items()}
    else:
        components = {}

    conviction = float(composite.get("conviction", 0.5))
    direction = composite.get("direction") or _infer_direction(conviction, rules)
    features = composite.get("features") or {}
    signals = composite.get("_signals") or _signals_for_symbol(audit, symbol)

    thresholds = rules.signals
    return _enrich_token(
        {
            "symbol": symbol,
            "conviction": round(conviction, 2),
            "direction": direction,
            "components": {k: round(v, 2) for k, v in components.items()},
            "components_line": _format_components(components),
            "one_liner": (
                f"{symbol}: conviction={conviction:.2f} ({direction}) "
                f"{_format_components(components)}"
            ),
            "summary": composite.get("summary") or "",
            "features": features,
            "signals": signals,
            "is_decision_target": symbol == decision_asset.upper(),
            "flags": {
                "bullish_candidate": conviction >= thresholds.buy_conviction_min and direction == "bullish",
                "sell_candidate": conviction <= thresholds.sell_conviction_max,
                "inferred": bool(composite.get("_inferred")),
            },
            "weights": {
                "technicals": rules.signal_weights.technicals,
                "sentiment": rules.signal_weights.sentiment,
                "derivatives": rules.signal_weights.derivatives,
                "onchain": rules.signal_weights.onchain,
                "news": rules.signal_weights.news,
            },
        },
        rules,
    )


def _decision_panel_row(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": decision.get("action"),
        "asset": decision.get("asset"),
        "confidence": decision.get("confidence"),
        "size_usd": decision.get("size_usd"),
        "reason": decision.get("reason"),
        "signals_used": decision.get("signals_used") or [],
        "current_price_usd": decision.get("current_price_usd"),
        "take_profit_pct": decision.get("take_profit_pct"),
        "take_profit_price_usd": decision.get("take_profit_price_usd"),
        "exit_trigger": decision.get("exit_trigger"),
        "price_line": _format_decision_price_line(decision),
    }


def build_logic_view(
    audit: dict[str, Any] | None,
    rules: RulesConfig,
    *,
    llm_enabled: bool = False,
) -> dict[str, Any]:
    """Build logic panel payload from the latest audit record."""
    empty: dict[str, Any] = {
        "has_data": False,
        "cycle_id": None,
        "timestamp": None,
        "duration_ms": None,
        "mode": "llm" if llm_enabled else "rule-based",
        "thresholds": {
            "buy_conviction_min": rules.signals.buy_conviction_min,
            "sell_conviction_max": rules.signals.sell_conviction_max,
            "take_profit_pct": rules.exit.take_profit_pct,
        },
        "decision": None,
        "risk": None,
        "trade": None,
        "reasoning": [],
        "tokens": [],
        "categories": {"bullish": [], "neutral": [], "bearish": []},
        "stats": {"total": 0, "bullish": 0, "bearish": 0, "neutral": 0},
    }
    if not audit:
        return empty

    decision = audit.get("decision") or {}
    decision_asset = str(decision.get("asset") or "").upper()
    composites = _composites_from_audit(audit, rules)

    tokens = [
        _token_row(c, audit, rules, decision_asset)
        for c in composites
        if c.get("symbol")
    ]
    tokens.sort(key=lambda t: t["conviction"], reverse=True)

    stats = {
        "total": len(tokens),
        "bullish": sum(1 for t in tokens if t["direction"] == "bullish"),
        "bearish": sum(1 for t in tokens if t["direction"] == "bearish"),
        "neutral": sum(1 for t in tokens if t["direction"] == "neutral"),
    }

    risk = audit.get("risk_validation")
    trade = audit.get("trade")
    reasoning: list[str] = []

    if decision.get("reason"):
        reasoning.append(str(decision["reason"]))
    if decision.get("risk_notes"):
        reasoning.append(f"Risk notes: {decision['risk_notes']}")
    if risk and isinstance(risk, dict):
        reasoning.append(f"Risk: {risk.get('reason', '')}")
    if trade and isinstance(trade, dict) and trade.get("tx_hash"):
        reasoning.append(f"Executed: {trade.get('side')} {trade.get('symbol')} ${trade.get('amount_usd', 0):.2f}")

    categories = categorize_tokens(tokens)

    return {
        "has_data": bool(tokens or decision),
        "cycle_id": audit.get("cycle_id"),
        "timestamp": audit.get("timestamp"),
        "duration_ms": audit.get("duration_ms"),
        "mode": "llm" if llm_enabled else "rule-based",
        "thresholds": empty["thresholds"],
        "categories": categories,
        "decision": _decision_panel_row(decision)
        if decision
        else None,
        "risk": risk,
        "trade": trade,
        "reasoning": [r for r in reasoning if r],
        "tokens": tokens,
        "stats": stats,
    }


def categorize_tokens(tokens: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split token rows into bullish / neutral / bearish buckets."""
    return {
        "bullish": [t for t in tokens if t.get("direction") == "bullish"],
        "neutral": [t for t in tokens if t.get("direction") == "neutral"],
        "bearish": [t for t in tokens if t.get("direction") == "bearish"],
    }


def build_feed_token(composite: dict[str, Any], rules: RulesConfig) -> dict[str, Any]:
    """Token row for live cycle feed cards (includes metrics when signals are present)."""
    symbol = str(composite.get("symbol", "")).upper()
    components = composite.get("components") or {}
    if not isinstance(components, dict):
        components = {}
    conviction = float(composite.get("conviction", 0.5))
    direction = composite.get("direction") or _infer_direction(conviction, rules)
    signals_raw = composite.get("_signals") or []
    signals = [
        s if isinstance(s, dict) and "highlights" in s else _serialize_signal(s)
        for s in signals_raw
        if isinstance(s, dict)
    ]
    return _enrich_token(
        {
            "symbol": symbol,
            "conviction": round(conviction, 2),
            "direction": direction,
            "components": {str(k): round(float(v), 2) for k, v in components.items()},
            "components_line": _format_components(components),
            "summary": composite.get("summary") or "",
            "signals": signals,
            "is_decision_target": False,
            "weights": {
                "technicals": rules.signal_weights.technicals,
                "sentiment": rules.signal_weights.sentiment,
                "derivatives": rules.signal_weights.derivatives,
                "onchain": rules.signal_weights.onchain,
                "news": rules.signal_weights.news,
            },
        },
        rules,
    )


def build_cycle_feed_view(
    feed: dict[str, Any],
    rules: RulesConfig,
    *,
    llm_enabled: bool = False,
) -> dict[str, Any]:
    """Format in-progress cycle composites for the public live feed."""
    composites = feed.get("composites") or []
    tokens = [
        build_feed_token(c, rules)
        for c in composites
        if isinstance(c, dict) and c.get("symbol")
    ]
    tokens.sort(key=lambda t: t["conviction"], reverse=True)
    categories = categorize_tokens(tokens)
    stats = {
        "total": len(tokens),
        "bullish": len(categories["bullish"]),
        "neutral": len(categories["neutral"]),
        "bearish": len(categories["bearish"]),
    }
    return {
        "active": bool(feed.get("active")),
        "state": "cycling" if feed.get("active") else "idle",
        "cycle_id": feed.get("cycle_id"),
        "phase": feed.get("phase") or "idle",
        "current_symbol": feed.get("current_symbol"),
        "progress": {
            "scanned": int(feed.get("scanned") or 0),
            "total": int(feed.get("total") or 0),
        },
        "updated_at": feed.get("updated_at"),
        "mode": "llm" if llm_enabled else "rule-based",
        "tokens": tokens,
        "categories": categories,
        "stats": stats,
        "has_data": bool(tokens),
    }


async def fetch_cycle_feed(rules: RulesConfig, *, llm_enabled: bool = False) -> dict[str, Any]:
    """Live in-progress feed when cycling, otherwise latest completed audit."""
    from dashboard.agent_runner import get_agent_runner

    runner = get_agent_runner()
    live = runner.cycle_feed()
    if live.get("active"):
        view = build_cycle_feed_view(live, rules, llm_enabled=llm_enabled)
        view["state"] = runner.state
        return view

    logic = await fetch_latest_logic(rules, llm_enabled=llm_enabled)
    tokens = logic.get("tokens") or []
    categories = categorize_tokens(tokens)
    return {
        "active": False,
        "state": runner.state,
        "cycle_id": logic.get("cycle_id"),
        "phase": "idle" if runner.state == "idle" else runner.state,
        "current_symbol": None,
        "progress": {"scanned": len(tokens), "total": len(tokens)},
        "updated_at": logic.get("timestamp"),
        "mode": logic.get("mode"),
        "decision": logic.get("decision"),
        "tokens": tokens,
        "categories": categories,
        "stats": logic.get("stats") or {},
        "has_data": logic.get("has_data", False),
        "duration_ms": logic.get("duration_ms"),
    }


async def fetch_latest_logic(rules: RulesConfig, *, llm_enabled: bool = False) -> dict[str, Any]:
    """Load the most recent audit and format for the logic panel."""
    from genesis.core.config import get_env_settings
    from genesis.core.database import Database

    env = get_env_settings()
    db = Database(env.genesis_db_path)
    await db.initialize()
    audits = await db.get_recent_audits(1)
    audit = audits[0] if audits else None
    return build_logic_view(audit, rules, llm_enabled=llm_enabled)