"""Tests for dashboard agent logic panel formatting."""

from __future__ import annotations

from genesis.core.models import RulesConfig
from dashboard.agent_logic import (
    _build_logic_explanation,
    _extract_token_metrics,
    build_logic_view,
)


def test_build_logic_view_from_composites():
    rules = RulesConfig()
    audit = {
        "cycle_id": "abc123",
        "timestamp": "2026-06-20T12:00:00+00:00",
        "duration_ms": 4500,
        "decision": {
            "action": "HOLD",
            "asset": "AAVE",
            "confidence": 0.55,
            "reason": "Rule-based HOLD",
        },
        "composites": [
            {
                "symbol": "AAVE",
                "conviction": 0.50,
                "direction": "neutral",
                "components": {
                    "derivatives": 0.47,
                    "news": 0.67,
                    "onchain": 0.50,
                    "sentiment": 0.32,
                    "technicals": 0.56,
                },
                "summary": "derivatives=0.47, news=0.67, onchain=0.50, sentiment=0.32, technicals=0.56",
            }
        ],
        "signals": [
            {
                "category": "technical",
                "symbol": "AAVE",
                "value": 0.12,
                "summary": "RSI neutral",
                "raw_data": {"rsi": 52},
            },
            {
                "category": "onchain",
                "symbol": "AAVE",
                "value": 0.3,
                "summary": "holders growing",
                "raw_data": {
                    "parsed": {
                        "holders": 125000,
                        "traders": 4200,
                        "cryptoTotalHolderCount30dChangePercent": 8.5,
                    }
                },
            },
        ],
    }

    view = build_logic_view(audit, rules)

    assert view["has_data"] is True
    assert view["tokens"][0]["symbol"] == "AAVE"
    assert view["tokens"][0]["conviction"] == 0.50
    assert "derivatives=0.47" in view["tokens"][0]["one_liner"]
    assert view["tokens"][0]["is_decision_target"] is True
    token = view["tokens"][0]
    assert token["metrics"]["rsi14"] == 52.0
    assert token["metrics"]["holders"] == 125000
    assert "RSI 52" in token["chip_preview"]
    assert "125.0K holders" in token["chip_preview"]
    assert any("Conviction" in line for line in token["logic_lines"])
    assert any("wallet holders" in line for line in token["logic_lines"])


def test_extract_token_metrics_from_serialized_signals():
    signals = [
        {
            "category": "technicals",
            "value": 0.2,
            "highlights": {"rsi": 61, "macd": "bullish cross"},
        },
        {
            "category": "onchain",
            "value": 0.1,
            "highlights": {
                "holders": 50000,
                "cryptoTotalHolderCount30dChangePercent": -2.1,
            },
        },
    ]
    metrics = _extract_token_metrics(signals)
    assert metrics["rsi14"] == 61.0
    assert metrics["holders"] == 50000


def test_build_logic_explanation_neutral_band():
    rules = RulesConfig()
    row = {
        "conviction": 0.52,
        "direction": "neutral",
        "components": {"technicals": 0.55, "sentiment": 0.48},
        "weights": {
            "technicals": rules.signal_weights.technicals,
            "sentiment": rules.signal_weights.sentiment,
            "derivatives": rules.signal_weights.derivatives,
            "onchain": rules.signal_weights.onchain,
            "news": rules.signal_weights.news,
        },
        "metrics": {"rsi14": 48},
    }
    lines = _build_logic_explanation(row, rules)
    assert any("neutral" in line.lower() for line in lines)
    assert any("RSI 48" in line for line in lines)


def test_build_logic_view_infers_from_signals():
    rules = RulesConfig()
    audit = {
        "cycle_id": "x1",
        "signals": [
            {"category": "technical", "symbol": "BNB", "value": 0.2, "summary": "bullish"},
            {"category": "sentiment", "symbol": "BNB", "value": -0.1, "summary": "weak"},
        ],
    }
    view = build_logic_view(audit, rules)
    assert view["has_data"] is True
    assert view["tokens"][0]["symbol"] == "BNB"
    assert "technicals" in view["tokens"][0]["components"]