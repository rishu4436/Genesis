"""Unit tests for Pydantic models."""

import pytest
from pydantic import ValidationError

from genesis.core.models import Action, Decision, RiskRules, RulesConfig, Signal, SignalCategory


def test_decision_valid():
    d = Decision(
        action=Action.BUY,
        asset="BNB",
        size_pct=2.0,
        reason="Test buy",
        confidence=0.75,
    )
    assert d.action == Action.BUY
    assert d.size_pct == 2.0


def test_decision_invalid_confidence():
    with pytest.raises(ValidationError):
        Decision(
            action=Action.HOLD,
            asset="BNB",
            reason="Test",
            confidence=1.5,
        )


def test_signal_normalized_range():
    s = Signal(category=SignalCategory.QUOTE, symbol="BNB", value=0.5)
    assert -1.0 <= s.value <= 1.0


def test_risk_rules_defaults():
    rules = RiskRules()
    assert rules.max_drawdown_pct == 30.0
    assert rules.min_confidence == 0.65


def test_rules_config_load():
    rules = RulesConfig()
    assert rules.strategy.name == "conservative_momentum_sentiment"
    assert len(rules.allowed_tokens) == 0  # empty default