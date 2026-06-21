"""Tests for market-wide CMC context helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from genesis.core.models import CompositeSignal, RulesConfig, Signal, SignalCategory
from genesis.data.market_context import (
    apply_market_context,
    build_market_context,
    macro_blocks_buys,
)


def test_macro_blocks_buys_within_window():
    soon = datetime.now(timezone.utc) + timedelta(minutes=45)
    data = {
        "rows": [
            {
                "eventName": "CPI Release",
                "eventTime": soon.isoformat(),
                "impact": "high",
            }
        ]
    }
    blocked, reason = macro_blocks_buys(data, hours_ahead=2.0)
    assert blocked is True
    assert "CPI Release" in reason


def test_macro_blocks_buys_outside_window():
    later = datetime.now(timezone.utc) + timedelta(hours=6)
    data = {
        "rows": [
            {
                "eventName": "FOMC",
                "eventTime": later.isoformat(),
                "impact": "high",
            }
        ]
    }
    blocked, _ = macro_blocks_buys(data, hours_ahead=2.0)
    assert blocked is False


def test_build_market_context_sets_delta():
    market = Signal(
        category=SignalCategory.MARKET,
        symbol="MARKET",
        value=0.5,
        source="cmc_mcp:get_crypto_marketcap_technical_analysis",
    )
    macro = Signal(
        category=SignalCategory.MACRO,
        symbol="MARKET",
        value=-0.1,
        raw_data={"rows": []},
        source="cmc_mcp:get_upcoming_macro_events",
    )
    derivatives = Signal(
        category=SignalCategory.MARKET,
        symbol="MARKET",
        value=0.2,
        source="cmc_mcp:get_global_crypto_derivatives_metrics",
    )
    ctx = build_market_context(market, macro, derivatives_signal=derivatives)
    assert len(ctx.signals) == 3
    assert ctx.market_conviction_delta > 0
    assert ctx.blocks_buys is False


def test_apply_market_context_shifts_conviction():
    rules = RulesConfig()
    ctx = build_market_context(
        Signal(
            category=SignalCategory.MARKET,
            symbol="MARKET",
            value=1.0,
            source="cmc_mcp:get_crypto_marketcap_technical_analysis",
        ),
        None,
    )
    composites = [
        CompositeSignal(
            symbol="BNB",
            conviction=0.52,
            direction="bullish",
            components={"technicals": 0.6},
        )
    ]
    adjusted = apply_market_context(composites, ctx, rules)
    assert adjusted[0].conviction > composites[0].conviction
    assert adjusted[0].features.get("market_ta_bias") is not None