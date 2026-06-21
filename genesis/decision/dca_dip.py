"""DCA dip-buy strategy for top CMC coins with large 24h drawdowns."""

from __future__ import annotations

from genesis.core.models import (
    Action,
    CompositeSignal,
    DcaPositionState,
    Decision,
    PortfolioSnapshot,
    RulesConfig,
)
from genesis.decision.exit_rules import current_price_usd
from genesis.decision.trade_sizing import max_spot_trade_size_usd, stable_quote_balance_usd

STABLE_SYMBOLS = frozenset(
    {
        "USDT", "USDC", "DAI", "TUSD", "USDD", "USD1", "USDe", "U", "FDUSD", "FRAX",
        "BUSD", "USDP", "LUSD", "SUSD", "GUSD", "EURI", "XUSD", "DUSD", "FRXUSD",
        "USDf", "USDF", "lisUSD", "XAUt", "BTCB", "ETH",
    }
)


def dca_dip_active(rules: RulesConfig) -> bool:
    return bool(rules.dca_dip.enabled or rules.strategy.name == "dca_dip")


def _rank(composite: CompositeSignal) -> int:
    raw = composite.features.get("cmc_rank")
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0


def _change_24h(composite: CompositeSignal) -> float | None:
    raw = composite.features.get("change_24h_pct")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _eligible_dca_symbol(composite: CompositeSignal, rules: RulesConfig) -> bool:
    sym = composite.symbol.upper()
    if sym in STABLE_SYMBOLS:
        return False
    rank = _rank(composite)
    if rank <= 0 or rank > rules.dca_dip.max_cmc_rank:
        return False
    change = _change_24h(composite)
    if change is None:
        return False
    return True


def _profit_pct(avg_entry: float, current: float) -> float:
    if avg_entry <= 0:
        return 0.0
    return ((current - avg_entry) / avg_entry) * 100.0


def _find_dca_take_profit_sell(
    composites: list[CompositeSignal],
    portfolio: PortfolioSnapshot,
    rules: RulesConfig,
    dca_states: dict[str, DcaPositionState],
    trade_size_usd: float,
) -> Decision | None:
    cfg = rules.dca_dip
    held = {p.symbol.upper(): p for p in portfolio.positions}

    for symbol, state in dca_states.items():
        if not state.active or state.avg_entry_price_usd <= 0:
            continue
        sym = symbol.upper()
        position = held.get(sym)
        if not position or position.amount <= 0:
            continue
        current = current_price_usd(
            sym,
            composites,
            position_current=position.current_price,
            position_entry=state.avg_entry_price_usd,
        )
        if not current or current <= 0:
            continue
        gain = _profit_pct(state.avg_entry_price_usd, current)
        if gain < cfg.take_profit_pct:
            continue
        target = state.avg_entry_price_usd * (1.0 + cfg.take_profit_pct / 100.0)
        position_value = getattr(position, "value_usd", None) or (position.amount * current)
        return Decision(
            action=Action.SELL,
            asset=state.symbol,
            size_usd=min(trade_size_usd, float(position_value or trade_size_usd)),
            reason=(
                f"DCA take-profit SELL: {state.symbol} +{gain:.1f}% "
                f"(avg entry ${state.avg_entry_price_usd:.4f} → ${current:.4f}, "
                f"target +{cfg.take_profit_pct:.0f}% @ ${target:.4f}, "
                f"{state.buy_count} DCA buys)"
            ),
            confidence=0.85,
            risk_notes="DCA dip exit",
            signals_used=["dca_take_profit"],
            current_price_usd=current,
            take_profit_pct=cfg.take_profit_pct,
            take_profit_price_usd=target,
            exit_trigger="dca_take_profit",
            strategy_mode="dca_dip",
            dca_step=state.buy_count,
        )
    return None


def _next_dca_buy(
    composite: CompositeSignal,
    state: DcaPositionState | None,
    rules: RulesConfig,
    trade_size_usd: float,
) -> Decision | None:
    cfg = rules.dca_dip
    sym = composite.symbol.upper()
    price = current_price_usd(sym, [composite])
    change = _change_24h(composite)
    if not price or price <= 0 or change is None:
        return None

    if state is None or not state.active:
        if change > -cfg.trigger_drop_24h_pct:
            return None
        step = 1
        reason = (
            f"DCA dip BUY #{step}: {composite.symbol} rank #{_rank(composite)} "
            f"24h {change:+.1f}% (trigger ≤ -{cfg.trigger_drop_24h_pct:.0f}%) "
            f"@ ${price:.4f} — next ladder buy after -{cfg.step_drop_pct:.0f}% "
            f"from ${price:.4f}"
        )
    else:
        threshold = state.last_buy_price_usd * (1.0 - cfg.step_drop_pct / 100.0)
        if price > threshold:
            return None
        step = state.buy_count + 1
        reason = (
            f"DCA dip BUY #{step}: {composite.symbol} "
            f"${price:.4f} ≤ -{cfg.step_drop_pct:.0f}% from last buy "
            f"${state.last_buy_price_usd:.4f} (avg ${state.avg_entry_price_usd:.4f}, "
            f"24h {change:+.1f}%)"
        )

    target = price * (1.0 + cfg.take_profit_pct / 100.0)
    return Decision(
        action=Action.BUY,
        asset=composite.symbol,
        size_usd=trade_size_usd,
        reason=reason,
        confidence=0.75,
        risk_notes="DCA dip ladder",
        signals_used=["dca_dip"],
        current_price_usd=price,
        take_profit_pct=cfg.take_profit_pct,
        take_profit_price_usd=target,
        exit_trigger="dca_take_profit_target",
        strategy_mode="dca_dip",
        change_24h_pct=change,
        dca_step=step,
    )


def decide_dca_dip(
    composites: list[CompositeSignal],
    portfolio: PortfolioSnapshot,
    rules: RulesConfig,
    dca_states: dict[str, DcaPositionState],
    *,
    trade_size_usd: float | None = None,
) -> Decision:
    """Top-100 CMC dip DCA: ladder buys on 24h crash, exit at configured profit %."""
    cfg = rules.dca_dip
    size = trade_size_usd if trade_size_usd is not None else max_spot_trade_size_usd(portfolio, rules)
    stables = stable_quote_balance_usd(portfolio)

    if size <= 0 or stables < rules.risk.min_swap_usd:
        return Decision(
            action=Action.HOLD,
            asset="USDT",
            reason="DCA dip HOLD: insufficient USDT/USDC for next ladder buy",
            confidence=0.5,
            risk_notes="DCA dip",
            strategy_mode="dca_dip",
        )

    sell = _find_dca_take_profit_sell(composites, portfolio, rules, dca_states, size)
    if sell:
        return sell

    active_count = sum(1 for s in dca_states.values() if s.active)
    candidates: list[tuple[float, CompositeSignal, Decision]] = []

    for composite in composites:
        if not _eligible_dca_symbol(composite, rules):
            continue
        sym = composite.symbol.upper()
        state = dca_states.get(sym)
        if state is None and active_count >= cfg.max_concurrent_positions:
            continue
        buy = _next_dca_buy(composite, state, rules, size)
        if buy:
            change = _change_24h(composite) or 0.0
            rank = _rank(composite) or 999
            candidates.append((change, composite, buy))

    if candidates:
        candidates.sort(key=lambda row: (row[0], -row[1].features.get("market_cap_usd", 0) or 0))
        return candidates[0][2]

    best_drop = None
    for composite in composites:
        if not _eligible_dca_symbol(composite, rules):
            continue
        change = _change_24h(composite)
        if change is None or change > -cfg.trigger_drop_24h_pct:
            continue
        if best_drop is None or change < best_drop[0]:
            best_drop = (change, composite)

    for sym, state in dca_states.items():
        if not state.active:
            continue
        composite = next((c for c in composites if c.symbol.upper() == sym.upper()), None)
        if not composite:
            continue
        price = current_price_usd(sym, [composite], position_entry=state.avg_entry_price_usd)
        change = _change_24h(composite)
        if price and price > 0:
            gain = _profit_pct(state.avg_entry_price_usd, price)
            next_step = state.last_buy_price_usd * (1.0 - cfg.step_drop_pct / 100.0)
            return Decision(
                action=Action.HOLD,
                asset=state.symbol,
                reason=(
                    f"DCA dip HOLD: {state.symbol} avg ${state.avg_entry_price_usd:.4f}, "
                    f"now ${price:.4f} ({gain:+.1f}%), "
                    f"exit +{cfg.take_profit_pct:.0f}% or next buy ≤ ${next_step:.4f} "
                    f"({state.buy_count} buys)"
                ),
                confidence=0.5,
                risk_notes="DCA dip",
                strategy_mode="dca_dip",
                change_24h_pct=change,
                current_price_usd=price,
                dca_step=state.buy_count,
            )

    if best_drop:
        change, composite = best_drop
        return Decision(
            action=Action.HOLD,
            asset=composite.symbol,
            reason=(
                f"DCA dip HOLD: {composite.symbol} 24h {change:+.1f}% qualifies "
                f"(≤ -{cfg.trigger_drop_24h_pct:.0f}%) — awaiting first ladder entry"
            ),
            confidence=0.5,
            risk_notes="DCA dip",
            strategy_mode="dca_dip",
            change_24h_pct=change,
            current_price_usd=current_price_usd(composite.symbol, [composite]),
        )

    return Decision(
        action=Action.HOLD,
        asset=composites[0].symbol if composites else "BNB",
        reason=(
            f"DCA dip HOLD: no top-{cfg.max_cmc_rank} coin down "
            f"≥ {cfg.trigger_drop_24h_pct:.0f}% in 24h"
        ),
        confidence=0.4,
        risk_notes="DCA dip",
        strategy_mode="dca_dip",
    )


def compute_dca_state_after_buy(
    state: DcaPositionState | None,
    *,
    symbol: str,
    buy_price: float,
    buy_usd: float,
    change_24h_pct: float | None,
    dca_step: int,
) -> DcaPositionState:
    """Update average entry after a confirmed DCA buy."""
    if state is None or not state.active:
        return DcaPositionState(
            symbol=symbol,
            buy_count=dca_step,
            last_buy_price_usd=buy_price,
            avg_entry_price_usd=buy_price,
            total_cost_usd=buy_usd,
            trigger_change_24h_pct=change_24h_pct or 0.0,
            active=True,
        )
    new_cost = state.total_cost_usd + buy_usd
    tokens_bought = buy_usd / buy_price if buy_price > 0 else 0.0
    prev_tokens = state.total_cost_usd / state.avg_entry_price_usd if state.avg_entry_price_usd > 0 else 0.0
    total_tokens = prev_tokens + tokens_bought
    avg = new_cost / total_tokens if total_tokens > 0 else buy_price
    return DcaPositionState(
        symbol=symbol,
        buy_count=dca_step,
        last_buy_price_usd=buy_price,
        avg_entry_price_usd=avg,
        total_cost_usd=new_cost,
        trigger_change_24h_pct=state.trigger_change_24h_pct,
        active=True,
    )