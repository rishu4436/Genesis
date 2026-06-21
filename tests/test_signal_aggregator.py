"""Unit tests for signal aggregation."""

from genesis.core.models import RulesConfig, Signal, SignalCategory, SignalWeights
from genesis.data.signal_aggregator import SignalAggregator


def test_bullish_aggregation():
    rules = RulesConfig()
    rules.signal_weights = SignalWeights(
        technicals=0.4, sentiment=0.3, derivatives=0.1, onchain=0.1, news=0.1,
    )
    agg = SignalAggregator(rules)

    signals = [
        Signal(category=SignalCategory.TECHNICAL, symbol="BNB", value=0.6),
        Signal(category=SignalCategory.SENTIMENT, symbol="BNB", value=0.5),
        Signal(category=SignalCategory.ONCHAIN, symbol="BNB", value=0.4),
        Signal(category=SignalCategory.DERIVATIVES, symbol="BNB", value=0.1),
        Signal(category=SignalCategory.NEWS, symbol="BNB", value=0.3),
    ]

    composite = agg.aggregate("BNB", signals)
    assert composite.symbol == "BNB"
    assert 0.0 <= composite.conviction <= 1.0
    assert composite.direction in ("bullish", "bearish", "neutral")
    assert len(composite.components) > 0


def test_significant_change_detection():
    rules = RulesConfig()
    agg = SignalAggregator(rules)

    assert agg.has_significant_change("BNB", 0.7)

    agg._last_conviction["BNB"] = 0.7
    assert not agg.has_significant_change("BNB", 0.72)
    assert agg.has_significant_change("BNB", 0.9)