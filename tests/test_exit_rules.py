"""Tests for take-profit exit rules and decision price annotations."""

from genesis.core.models import (
    Action,
    CompositeSignal,
    ExitRulesConfig,
    PortfolioSnapshot,
    Position,
    RulesConfig,
)
from genesis.decision.exit_rules import (
    annotate_buy_decision,
    find_take_profit_candidate,
)
from genesis.decision.strategy_engine import StrategyEngine
from genesis.decision.risk_manager import RiskManager
from genesis.core.config import EnvSettings


def _rules() -> RulesConfig:
    rules = RulesConfig()
    rules.exit = ExitRulesConfig(take_profit_pct=10.0, prefer_take_profit_over_conviction=True)
    return rules


def _composite(symbol: str, price: float, conviction: float = 0.7) -> CompositeSignal:
    return CompositeSignal(
        symbol=symbol,
        conviction=conviction,
        direction="bullish",
        components={"technicals": conviction},
        features={"price_usd": price},
    )


def test_annotate_buy_adds_price_and_take_profit_target():
    rules = _rules()
    from genesis.core.models import Decision

    decision = Decision(
        action=Action.BUY,
        asset="LINK",
        size_usd=5.0,
        reason="Rule-based BUY: LINK",
        confidence=0.7,
    )
    annotated = annotate_buy_decision(decision, _composite("LINK", 15.0), rules)
    assert annotated.current_price_usd == 15.0
    assert annotated.take_profit_pct == 10.0
    assert annotated.take_profit_price_usd == 16.5
    assert "@ $15.0000" in annotated.reason
    assert "+10%" in annotated.reason


def test_take_profit_sell_triggers_at_10_percent_gain():
    rules = _rules()
    portfolio = PortfolioSnapshot(
        total_value_usd=60.0,
        available_usd=10.0,
        positions=[
            Position(
                symbol="LINK",
                amount=2.0,
                entry_price=10.0,
                current_price=11.5,
                unrealized_pnl_pct=15.0,
            )
        ],
    )
    composites = [_composite("LINK", 11.5, conviction=0.8)]

    sell = find_take_profit_candidate(composites, portfolio, rules, trade_size_usd=5.0)

    assert sell is not None
    assert sell.action == Action.SELL
    assert sell.asset == "LINK"
    assert sell.exit_trigger == "take_profit"
    assert "Take-profit SELL" in sell.reason
    assert sell.current_price_usd == 11.5


def test_take_profit_sell_skips_below_threshold():
    rules = _rules()
    portfolio = PortfolioSnapshot(
        total_value_usd=55.0,
        available_usd=10.0,
        positions=[
            Position(symbol="CAKE", amount=5.0, entry_price=2.0, current_price=2.15)
        ],
    )
    composites = [_composite("CAKE", 2.15)]

    assert find_take_profit_candidate(composites, portfolio, rules, 5.0) is None


def test_strategy_engine_buy_includes_price_in_reason():
    env = EnvSettings(llm_enabled=False, xai_api_key="")
    rules = _rules()
    from genesis.core.models import TokenConfig

    rules.allowed_tokens = [
        TokenConfig(symbol="LINK", address="0x", cmc_id=1975),
    ]
    engine = StrategyEngine(env, rules, RiskManager(rules))
    portfolio = PortfolioSnapshot(
        total_value_usd=110.0,
        available_usd=100.0,
        positions=[Position(symbol="USDT", amount=100.0, entry_price=1.0, current_price=1.0)],
    )
    composites = [
        CompositeSignal(
            symbol="LINK",
            conviction=0.72,
            direction="bullish",
            components={"technicals": 0.62, "sentiment": 0.58},
            features={"price_usd": 14.25, "cmc_rank": 12, "market_cap_usd": 9e9},
        )
    ]

    decision = engine.decide_rule_based(composites, portfolio)

    assert decision.action == Action.BUY
    assert decision.current_price_usd == 14.25
    assert decision.take_profit_price_usd == 14.25 * 1.1