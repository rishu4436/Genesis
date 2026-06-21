"""Tests for adaptive aggression after idle swap cycles."""

from genesis.core.models import Action, CompositeSignal, PortfolioSnapshot, Position, RulesConfig
from genesis.decision.adaptive_mode import (
    audit_had_swap,
    count_consecutive_idle_swap_cycles,
    is_aggressive_mode,
)
from genesis.decision.strategy_engine import StrategyEngine
from genesis.decision.risk_manager import RiskManager
from genesis.core.config import EnvSettings


def _composite(symbol: str, conviction: float, direction: str = "neutral") -> CompositeSignal:
    return CompositeSignal(
        symbol=symbol,
        conviction=conviction,
        direction=direction,
        components={"technicals": 0.5, "sentiment": 0.5},
        summary=f"{symbol} test",
    )


def test_count_idle_cycles_since_last_swap():
    audits = [
        {"cycle_id": "c1", "decision": {"action": "HOLD"}},
        {"cycle_id": "c2", "decision": {"action": "BUY"}, "trade": {"tx_hash": "0xabc"}},
        {"cycle_id": "c3"},
    ]
    assert count_consecutive_idle_swap_cycles(audits) == 1
    assert audit_had_swap(audits[1]) is True


def test_aggressive_mode_after_threshold():
    rules = RulesConfig()
    assert not is_aggressive_mode(rules, 9)
    assert is_aggressive_mode(rules, 10)
    assert is_aggressive_mode(rules, 15)


def test_conservative_hold_becomes_aggressive_buy():
    env = EnvSettings(llm_enabled=False)
    rules = RulesConfig()
    engine = StrategyEngine(env, rules, RiskManager(rules))
    portfolio = PortfolioSnapshot(
        total_value_usd=110.0,
        available_usd=100.0,
        positions=[Position(symbol="USDT", amount=100.0, entry_price=0, current_price=1.0)],
    )
    composites = [
        _composite("BNB", 0.50, "neutral"),
        _composite("CAKE", 0.48, "neutral"),
    ]

    hold = engine.decide_rule_based(composites, portfolio, idle_swap_cycles=0)
    assert hold.action == Action.HOLD

    buy = engine.decide_rule_based(composites, portfolio, idle_swap_cycles=10)
    assert buy.action == Action.BUY
    assert buy.asset in {"BNB", "CAKE"}
    assert "adaptive aggression" in buy.reason.lower()