"""Replay audit composites through Genesis rule gates (Track 2 backtest)."""

from __future__ import annotations

from typing import Any

from genesis.core.models import CompositeSignal, RulesConfig
from genesis.decision.adaptive_mode import buy_params_for_idle
from genesis.decision.candidate_selection import is_buy_eligible
from genesis.decision.strategy_engine import NON_TRADABLE_SYMBOLS


def _composite_from_dict(row: dict[str, Any]) -> CompositeSignal:
    return CompositeSignal(
        symbol=str(row.get("symbol", "")),
        conviction=float(row.get("conviction", 0.5)),
        direction=str(row.get("direction", "neutral")),
        components=row.get("components") or {},
        features=row.get("features") or {},
        summary=str(row.get("summary", "")),
    )


def backtest_from_audits(
    audits: list[dict[str, Any]],
    rules: RulesConfig,
    *,
    idle_swap_cycles: int = 0,
) -> dict[str, Any]:
    """
    Simple audit replay: count how often rule gates would BUY/SELL/HOLD.

    Uses the same is_buy_eligible + conviction thresholds as the live rule engine.
    """
    buy_gate = buy_params_for_idle(rules, idle_swap_cycles)

    evaluated = 0
    buy_count = 0
    sell_count = 0
    hold_count = 0
    by_symbol: dict[str, dict[str, int]] = {}

    for audit in audits:
        composites_raw = audit.get("composites") or []
        if not composites_raw and audit.get("composite"):
            composites_raw = [audit["composite"]]

        composites = [_composite_from_dict(c) for c in composites_raw if isinstance(c, dict)]
        if not composites:
            continue

        for composite in composites:
            sym = composite.symbol.upper()
            if sym in NON_TRADABLE_SYMBOLS:
                continue

            evaluated += 1
            bucket = by_symbol.setdefault(sym, {"buy": 0, "sell": 0, "hold": 0})

            if composite.conviction <= rules.signals.sell_conviction_max:
                sell_count += 1
                bucket["sell"] += 1
            elif is_buy_eligible(
                composite,
                rules,
                non_tradable=NON_TRADABLE_SYMBOLS,
                params=buy_gate,
            ):
                buy_count += 1
                bucket["buy"] += 1
            else:
                hold_count += 1
                bucket["hold"] += 1

    round_trips, wins = _simulate_round_trips(audits, rules, buy_gate)

    total = max(evaluated, 1)
    trips = len(round_trips)
    win_rate = round(100 * wins / trips, 1) if trips else 0.0

    return {
        "audits_processed": len(audits),
        "signals_evaluated": evaluated,
        "buy_signals": buy_count,
        "sell_signals": sell_count,
        "hold_signals": hold_count,
        "buy_pct": round(100 * buy_count / total, 1),
        "sell_pct": round(100 * sell_count / total, 1),
        "hold_pct": round(100 * hold_count / total, 1),
        "idle_swap_cycles_simulated": idle_swap_cycles,
        "simulated_round_trips": trips,
        "estimated_win_rate_pct": win_rate,
        "round_trip_samples": round_trips[:5],
        "top_symbols": sorted(
            (
                {"symbol": sym, **counts}
                for sym, counts in by_symbol.items()
            ),
            key=lambda x: x["buy"] + x["sell"],
            reverse=True,
        )[:10],
    }


def _simulate_round_trips(
    audits: list[dict[str, Any]],
    rules: RulesConfig,
    buy_gate: Any,
) -> tuple[list[dict[str, Any]], int]:
    """
    Pair sequential buy→sell signals per symbol for a simple win-rate estimate.

    Win = exit conviction lower than entry (bearish fade) OR sell after buy.
    """
    open_positions: dict[str, float] = {}
    trips: list[dict[str, Any]] = []
    wins = 0

    for audit in audits:
        composites_raw = audit.get("composites") or []
        if not composites_raw and audit.get("composite"):
            composites_raw = [audit["composite"]]

        for row in composites_raw:
            if not isinstance(row, dict):
                continue
            composite = _composite_from_dict(row)
            sym = composite.symbol.upper()
            if sym in NON_TRADABLE_SYMBOLS:
                continue

            if sym in open_positions:
                if composite.conviction <= rules.signals.sell_conviction_max:
                    entry_conv = open_positions.pop(sym)
                    outcome = "win" if composite.conviction < entry_conv else "loss"
                    if outcome == "win":
                        wins += 1
                    trips.append(
                        {
                            "symbol": sym,
                            "entry_conviction": round(entry_conv, 3),
                            "exit_conviction": round(composite.conviction, 3),
                            "outcome": outcome,
                        }
                    )
            elif is_buy_eligible(
                composite,
                rules,
                non_tradable=NON_TRADABLE_SYMBOLS,
                params=buy_gate,
            ):
                open_positions[sym] = composite.conviction

    return trips, wins