"""Strategy Skill Generator — produce backtestable strategy JSON from conditions."""

from __future__ import annotations

from typing import Any

from genesis.core.models import RulesConfig
from genesis.strategy_skill.models import MarketRegime, RiskProfile, StrategyConditions

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

RISK_PROFILE_PARAMS: dict[RiskProfile, dict[str, float | int]] = {
    "conservative": {
        "buy_conviction_min": 0.60,
        "sell_conviction_max": 0.35,
        "min_aligned_core_signals": 2,
        "spot_stable_pct": 5.0,
        "max_open_positions": 4,
        "target_rr": 2.5,
        "est_win_rate": 42.0,
    },
    "moderate": {
        "buy_conviction_min": 0.55,
        "sell_conviction_max": 0.38,
        "min_aligned_core_signals": 2,
        "spot_stable_pct": 7.0,
        "max_open_positions": 5,
        "target_rr": 2.0,
        "est_win_rate": 48.0,
    },
    "aggressive": {
        "buy_conviction_min": 0.50,
        "sell_conviction_max": 0.40,
        "min_aligned_core_signals": 1,
        "spot_stable_pct": 10.0,
        "max_open_positions": 6,
        "target_rr": 1.5,
        "est_win_rate": 52.0,
    },
}

REGIME_WEIGHT_BOOSTS: dict[MarketRegime, dict[str, float]] = {
    "bullish": {"technicals": 0.04, "sentiment": 0.03, "onchain": 0.02},
    "bearish": {"derivatives": 0.05, "news": 0.04, "sentiment": -0.02},
    "neutral": {"metadata": 0.03, "discovery": 0.02},
    "volatile": {"derivatives": 0.06, "technicals": 0.03, "news": 0.02},
}


def _normalize_weights(base: dict[str, float], boosts: dict[str, float]) -> dict[str, float]:
    merged = {k: max(0.0, base.get(k, 0.0) + boosts.get(k, 0.0)) for k in base}
    total = sum(merged.values()) or 1.0
    return {k: round(v / total, 4) for k, v in merged.items()}


def _indicator_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "rsi_14",
            "name": "RSI (14)",
            "source_tool": "get_crypto_technical_analysis",
            "params": {"period": 14, "interval": "5m"},
            "component": "technicals",
            "bullish_when": "rsi >= 60 OR (rsi <= 40 AND macd_histogram > 0)",
            "bearish_when": "rsi <= 35 AND macd_histogram < 0",
        },
        {
            "id": "macd_histogram",
            "name": "MACD Histogram",
            "source_tool": "get_crypto_technical_analysis",
            "params": {"fast": 12, "slow": 26, "signal": 9},
            "component": "technicals",
            "bullish_when": "histogram > 0 AND rising",
            "bearish_when": "histogram < 0 AND falling",
        },
        {
            "id": "fear_greed_index",
            "name": "Fear & Greed Index",
            "source_tool": "get_global_metrics_latest",
            "params": {},
            "component": "sentiment",
            "bullish_when": "index > 55 (greed) with stable macro",
            "bearish_when": "index < 25 (extreme fear) blocks new buys",
        },
        {
            "id": "holder_growth_30d",
            "name": "Holder Count (30d change)",
            "source_tool": "get_crypto_metrics",
            "params": {"window_days": 30},
            "component": "onchain",
            "bullish_when": "holders_30d_change_pct > 0",
            "bearish_when": "holders_30d_change_pct < -5%",
        },
        {
            "id": "funding_rate",
            "name": "Perpetual Funding Rate",
            "source_tool": "get_global_crypto_derivatives_metrics",
            "params": {},
            "component": "derivatives",
            "bullish_when": "abs(funding_rate) <= 0.01 (neutral)",
            "bearish_when": "funding_rate > 0.03 (overheated longs)",
        },
        {
            "id": "news_sentiment_ratio",
            "name": "News Bull/Bear Ratio",
            "source_tool": "get_crypto_latest_news",
            "params": {"limit": 20},
            "component": "news",
            "bullish_when": "bullish_articles / total >= 0.55",
            "bearish_when": "bearish_articles / total >= 0.55",
        },
    ]


def generate_strategy(
    conditions: StrategyConditions,
    rules: RulesConfig,
) -> dict[str, Any]:
    """
    Build a complete, backtestable strategy spec from user conditions.

    Shares CMC data sources and fusion logic with Track 1; parameters are
    tuned by risk profile and market regime without mutating live agent rules.
    """
    profile = RISK_PROFILE_PARAMS[conditions.risk_profile]
    base_weights = rules.signal_weights.model_dump()
    boosts = REGIME_WEIGHT_BOOSTS[conditions.market_regime]
    weights = _normalize_weights(base_weights, boosts)

    cycle_sec = TIMEFRAME_SECONDS.get(conditions.timeframe, rules.loop.interval_seconds)
    asset = conditions.primary_asset.upper().strip()
    allowed_symbols = [t.symbol for t in rules.allowed_tokens]
    assets = [asset] if asset in allowed_symbols else [asset, *allowed_symbols[:5]]

    buy_min = float(profile["buy_conviction_min"])
    sell_max = float(profile["sell_conviction_max"])
    adaptive = rules.loop.adaptive_aggression

    return {
        "hackathon": {
            "track": 2,
            "track_name": "Strategy Skills",
            "prize_pool_usd": 6000,
            "deliverable": "backtestable_strategy_spec",
            "generator": "genesis-strategy-skill-generator",
            "track_1_compatible": True,
        },
        "skill": {
            "name": "genesis-momentum-sentiment",
            "version": "1.1.0",
            "description": rules.strategy.description.strip(),
            "strategy_name": f"{conditions.risk_profile}_{conditions.market_regime}_momentum",
            "generated_from_conditions": conditions.model_dump(),
        },
        "market_scope": {
            "primary_asset": asset,
            "assets": assets,
            "network": "bsc",
            "timeframe": conditions.timeframe,
            "cycle_interval_seconds": cycle_sec,
            "eligible_universe": "hackathon_bep20_allowlist",
            "market_regime": conditions.market_regime,
        },
        "indicators": _indicator_catalog(),
        "data_sources": {
            "provider": "coinmarketcap_mcp",
            "tools": [
                "get_crypto_quotes_latest",
                "get_crypto_technical_analysis",
                "get_crypto_metrics",
                "get_crypto_latest_news",
                "get_global_metrics_latest",
                "get_global_crypto_derivatives_metrics",
                "get_crypto_marketcap_technical_analysis",
                "trending_crypto_narratives",
                "get_upcoming_macro_events",
                "search_cryptos",
                "get_crypto_info",
            ],
            "x402_fallback": True,
        },
        "signal_fusion": {
            "method": "weighted_mean",
            "weights": weights,
            "focus_signals": conditions.focus_signals,
            "components": list(weights.keys()),
            "conviction_range": [0.0, 1.0],
            "direction_rules": {
                "bullish": "conviction >= buy_threshold AND aligned_core >= min_aligned",
                "bearish": "conviction <= sell_threshold",
                "neutral": "otherwise",
            },
        },
        "entry_rules": {
            "type": "multi_signal_confluence",
            "conservative": {
                "direction": "bullish",
                "min_conviction": buy_min,
                "min_aligned_core_signals": int(profile["min_aligned_core_signals"]),
                "core_signals": conditions.focus_signals,
                "bullish_component_min": 0.55,
                "technicals_or_sentiment_min": [0.52, 0.55],
                "macro_buy_block": "fear_greed < 20 OR market_context.blocks_buys",
                "candidate_selection": "highest_market_cap_among_eligible",
            },
            "adaptive_aggression": {
                "enabled": adaptive.enabled,
                "idle_cycles_threshold": adaptive.idle_cycles_threshold,
                "min_conviction": adaptive.buy_conviction_min,
                "min_aligned_core_signals": adaptive.min_aligned_components,
                "allow_neutral_direction": adaptive.allow_neutral_direction,
                "neutral_min_conviction": adaptive.neutral_conviction_min,
                "force_best_candidate": adaptive.force_best_candidate,
            },
        },
        "exit_rules": {
            "signal_sell": {
                "condition": "held_position AND conviction <= sell_max_conviction",
                "sell_max_conviction": sell_max,
            },
            "take_profit": {
                "type": "percent_gain",
                "value_pct": conditions.take_profit_pct,
                "alternate": "conviction drops below 0.50 while in profit",
            },
            "stop_loss": {
                "type": "percent_loss",
                "value_pct": conditions.stop_loss_pct,
                "hard_halt_on_portfolio_drawdown_pct": rules.risk.max_drawdown_pct,
            },
            "trailing_stop": {
                "enabled": conditions.risk_profile != "conservative",
                "trail_pct": round(conditions.stop_loss_pct * 0.6, 1),
            },
            "cooldown_after_exit_minutes": rules.risk.cooldown_minutes,
        },
        "position_sizing": {
            "method": "percent_of_available_stables",
            "spot_stable_pct": float(profile["spot_stable_pct"]),
            "min_swap_usd": rules.risk.min_swap_usd,
            "max_open_positions": int(profile["max_open_positions"]),
            "max_portfolio_risk_per_trade_pct": rules.risk.max_portfolio_risk_per_trade_pct,
            "quote_tokens": ["USDT", "USDC"],
            "sizing_formula": "swap_usd = max(min_swap_usd, stable_balance * spot_stable_pct / 100)",
        },
        "risk_management": {
            "max_drawdown_pct": rules.risk.max_drawdown_pct,
            "max_slippage_bps": rules.risk.max_slippage_bps,
            "cooldown_minutes": rules.risk.cooldown_minutes,
            "token_allowlist_enforced": True,
            "stablecoin_excluded_from_trades": ["USDT", "USDC", "DAI", "BUSD", "U", "USDe"],
            "perps": {
                "enabled": rules.perps.enabled,
                "max_leverage": rules.perps.max_leverage,
                "margin_stable_pct": rules.perps.margin_stable_pct,
            },
        },
        "expected_performance": {
            "target_risk_reward_ratio": float(profile["target_rr"]),
            "estimated_win_rate_pct": float(profile["est_win_rate"]),
            "take_profit_pct": conditions.take_profit_pct,
            "stop_loss_pct": conditions.stop_loss_pct,
            "implied_expectancy": round(
                (float(profile["est_win_rate"]) / 100) * conditions.take_profit_pct
                - (1 - float(profile["est_win_rate"]) / 100) * conditions.stop_loss_pct,
                2,
            ),
            "optimal_conditions": _optimal_conditions(conditions.market_regime),
            "caveats": [
                "Estimates are heuristic; run audit backtest for empirical signal rates.",
                "Live execution adds slippage, gas, and TWAK latency not modeled here.",
            ],
        },
        "backtest": {
            "method": "audit_replay",
            "description": "Replay stored cycle composites through the same entry/exit gates as Track 1.",
            "cli": "python -m genesis.cli strategy-skill backtest --limit 50",
            "api": "/api/strategy-skill/backtest",
            "metrics": [
                "signals_evaluated",
                "buy_signals",
                "sell_signals",
                "hold_pct",
                "simulated_round_trips",
                "estimated_win_rate_pct",
            ],
        },
        "natural_language_summary": _nl_summary(conditions, buy_min, sell_max, weights),
    }


def _optimal_conditions(regime: MarketRegime) -> list[str]:
    mapping: dict[MarketRegime, list[str]] = {
        "bullish": ["RSI momentum confirmation", "positive holder growth", "neutral funding"],
        "bearish": ["defensive sizing", "quick take-profit", "news-driven exits"],
        "neutral": ["range-bound technicals", "low conviction entries only"],
        "volatile": ["wider stops", "derivatives-aware gates", "reduced position count"],
    }
    return mapping[regime]


def _nl_summary(
    conditions: StrategyConditions,
    buy_min: float,
    sell_max: float,
    weights: dict[str, float],
) -> str:
    w = weights
    return (
        f"{conditions.risk_profile.title()} {conditions.market_regime} strategy on "
        f"{conditions.primary_asset.upper()} ({conditions.timeframe}): fuse CMC signals "
        f"(technicals {w['technicals']:.0%}, sentiment {w['sentiment']:.0%}, "
        f"on-chain {w['onchain']:.0%}) into conviction. "
        f"BUY when conviction ≥ {buy_min:.2f}; SELL when ≤ {sell_max:.2f}. "
        f"TP {conditions.take_profit_pct:.0f}% / SL {conditions.stop_loss_pct:.0f}%."
    )