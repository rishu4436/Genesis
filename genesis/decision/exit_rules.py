"""Take-profit exit rules and decision price annotations."""

from __future__ import annotations

from genesis.core.models import Action, CompositeSignal, Decision, PortfolioSnapshot, RulesConfig


def current_price_usd(
    symbol: str,
    composites: list[CompositeSignal],
    *,
    position_entry: float | None = None,
    position_current: float | None = None,
) -> float | None:
    """Resolve latest USD price from composite features or position snapshot."""
    sym = symbol.upper()
    for composite in composites:
        if composite.symbol.upper() != sym:
            continue
        raw = composite.features.get("price_usd")
        if raw is not None:
            try:
                price = float(raw)
            except (TypeError, ValueError):
                price = 0.0
            if price > 0:
                return price
    if position_current and position_current > 0:
        return float(position_current)
    if position_entry and position_entry > 0:
        return float(position_entry)
    return None


def take_profit_target_price(entry_price: float, take_profit_pct: float) -> float:
    return entry_price * (1.0 + take_profit_pct / 100.0)


def profit_pct(entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return ((current_price - entry_price) / entry_price) * 100.0


def annotate_buy_decision(
    decision: Decision,
    composite: CompositeSignal | None,
    rules: RulesConfig,
) -> Decision:
    """Attach spot price and take-profit target to a BUY decision."""
    if decision.action != Action.BUY or not composite:
        return decision

    tp_pct = rules.exit.take_profit_pct
    price = current_price_usd(composite.symbol, [composite])
    if not price or price <= 0:
        return decision

    target = take_profit_target_price(price, tp_pct)
    extra = (
        f" @ ${price:.4f} — limit sell target +{tp_pct:.0f}% "
        f"(${target:.4f})"
    )
    reason = decision.reason
    if extra.strip() not in reason:
        reason = f"{reason}{extra}"

    return decision.model_copy(
        update={
            "reason": reason,
            "current_price_usd": price,
            "take_profit_pct": tp_pct,
            "take_profit_price_usd": target,
            "exit_trigger": "take_profit_target",
        }
    )


def take_profit_sell_decision(
    symbol: str,
    *,
    entry_price: float,
    current_price: float,
    position_value: float,
    trade_size_usd: float,
    rules: RulesConfig,
) -> Decision:
    """Build a SELL decision when unrealized gain hits the take-profit threshold."""
    tp_pct = rules.exit.take_profit_pct
    gain = profit_pct(entry_price, current_price)
    target = take_profit_target_price(entry_price, tp_pct)
    return Decision(
        action=Action.SELL,
        asset=symbol,
        size_usd=min(trade_size_usd, position_value),
        reason=(
            f"Take-profit SELL: {symbol} +{gain:.1f}% "
            f"(entry ${entry_price:.4f} → now ${current_price:.4f}, "
            f"target +{tp_pct:.0f}% @ ${target:.4f})"
        ),
        confidence=max(0.7, rules.risk.min_confidence),
        risk_notes="Take-profit exit (replaces conviction-based sell)",
        signals_used=["take_profit"],
        current_price_usd=current_price,
        take_profit_pct=tp_pct,
        take_profit_price_usd=target,
        exit_trigger="take_profit",
    )


def find_take_profit_candidate(
    composites: list[CompositeSignal],
    portfolio: PortfolioSnapshot,
    rules: RulesConfig,
    trade_size_usd: float,
) -> Decision | None:
    """Return SELL decision if any held spot position cleared take-profit."""
    if not rules.exit.prefer_take_profit_over_conviction:
        return None

    tp_pct = rules.exit.take_profit_pct
    for position in portfolio.positions:
        sym = position.symbol.upper()
        if sym in {"USDT", "USDC", "BUSD", "FDUSD", "DAI", "USD1"}:
            continue
        entry = position.entry_price or 0.0
        if entry <= 0:
            continue
        current = current_price_usd(
            sym,
            composites,
            position_current=position.current_price,
            position_entry=entry,
        )
        if not current or current <= 0:
            continue
        if profit_pct(entry, current) < tp_pct:
            continue
        position_value = getattr(position, "value_usd", None) or (position.amount * current)
        return take_profit_sell_decision(
            position.symbol,
            entry_price=entry,
            current_price=current,
            position_value=float(position_value or trade_size_usd),
            trade_size_usd=trade_size_usd,
            rules=rules,
        )
    return None