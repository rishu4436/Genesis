"""Tests for rule-based strategy engine."""

from genesis.core.config import EnvSettings
from genesis.core.models import (
    Action,
    CompositeSignal,
    PortfolioSnapshot,
    Position,
    RulesConfig,
    TokenConfig,
)
from genesis.decision.risk_manager import RiskManager
from genesis.decision.strategy_engine import StrategyEngine


def _engine(llm_enabled: bool = False) -> StrategyEngine:
    env = EnvSettings(llm_enabled=llm_enabled, xai_api_key="")
    rules = RulesConfig()
    rules.allowed_tokens = [
        TokenConfig(symbol="BNB", address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", cmc_id=1839),
        TokenConfig(symbol="CAKE", address="0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", cmc_id=7186),
    ]
    return StrategyEngine(env, rules, RiskManager(rules))


def _composite(
    symbol: str,
    conviction: float,
    direction: str = "bullish",
    *,
    rank: int = 0,
    mcap: float = 0.0,
) -> CompositeSignal:
    return CompositeSignal(
        symbol=symbol,
        conviction=conviction,
        direction=direction,
        components={"technicals": conviction, "sentiment": conviction},
        features={"cmc_rank": rank, "market_cap_usd": mcap},
        summary=f"{symbol} test",
    )


def test_rule_based_buy_picks_highest_conviction():
    engine = _engine()
    portfolio = PortfolioSnapshot(
        total_value_usd=110.0,
        available_usd=100.0,
        positions=[
            Position(symbol="USDT", amount=100.0, entry_price=0, current_price=1.0)
        ],
    )
    composites = [
        _composite("BNB", 0.55, "neutral"),
        _composite("CAKE", 0.72, "bullish"),
    ]

    decision = engine.decide_rule_based(composites, portfolio)

    assert decision.action == Action.BUY
    assert decision.asset == "CAKE"
    assert decision.size_usd == 5.0  # 5% of 100 USDT, above $1 min
    assert decision.confidence >= engine.rules.risk.min_confidence


def test_rule_based_skips_stablecoin_buy():
    engine = _engine()
    portfolio = PortfolioSnapshot(
        total_value_usd=110.0,
        available_usd=100.0,
        positions=[Position(symbol="USDT", amount=100.0, entry_price=0, current_price=1.0)],
    )
    composites = [
        _composite("USD1", 0.58, "bullish"),
        _composite("CAKE", 0.62, "bullish"),
    ]

    decision = engine.decide_rule_based(composites, portfolio)

    assert decision.action == Action.BUY
    assert decision.asset == "CAKE"


def test_rule_based_buy_prefers_larger_market_cap():
    engine = _engine()
    portfolio = PortfolioSnapshot(
        total_value_usd=110.0,
        available_usd=100.0,
        positions=[Position(symbol="USDT", amount=100.0, entry_price=0, current_price=1.0)],
    )
    composites = [
        _composite(
            "TAG",
            0.72,
            "bullish",
            rank=900,
            mcap=50_000_000,
        ),
        _composite(
            "BNB",
            0.65,
            "bullish",
            rank=4,
            mcap=90_000_000_000,
        ),
    ]
    for c in composites:
        c.components["technicals"] = max(c.conviction, 0.62)
        c.components["sentiment"] = max(c.conviction, 0.58)

    decision = engine.decide_rule_based(composites, portfolio)

    assert decision.action == Action.BUY
    assert decision.asset == "BNB"
    assert "largest cap" in decision.reason.lower()


def test_rule_based_hold_when_no_bullish_candidate():
    engine = _engine()
    portfolio = PortfolioSnapshot(total_value_usd=11.0, available_usd=9.78)
    composites = [
        _composite("BNB", 0.50, "neutral"),
        _composite("CAKE", 0.45, "neutral"),
    ]

    decision = engine.decide_rule_based(composites, portfolio)

    assert decision.action == Action.HOLD


def test_explicit_size_rejected_below_minimum_on_small_portfolio():
    rules = RulesConfig()
    rules.allowed_tokens = [
        TokenConfig(symbol="CAKE", address="0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", cmc_id=7186),
    ]
    rm = RiskManager(rules)
    from genesis.core.models import Decision

    decision = Decision(
        action=Action.BUY,
        asset="CAKE",
        size_usd=0.49,
        reason="test",
        confidence=0.7,
    )
    portfolio = PortfolioSnapshot(
        total_value_usd=11.0,
        available_usd=9.78,
        positions=[Position(symbol="USDT", amount=9.78, entry_price=0, current_price=1.0)],
    )
    result = rm.validate(decision, portfolio)

    assert result.approved
    assert result.adjusted_size_usd == 1.0


def test_explicit_size_passes_when_above_minimum():
    rules = RulesConfig()
    rules.allowed_tokens = [
        TokenConfig(symbol="CAKE", address="0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", cmc_id=7186),
    ]
    rm = RiskManager(rules)
    from genesis.core.models import Decision

    decision = Decision(
        action=Action.BUY,
        asset="CAKE",
        size_usd=3.0,
        reason="test",
        confidence=0.7,
    )
    portfolio = PortfolioSnapshot(
        total_value_usd=110.0,
        available_usd=100.0,
        positions=[Position(symbol="USDT", amount=100.0, entry_price=0, current_price=1.0)],
    )
    result = rm.validate(decision, portfolio)

    assert result.approved
    assert result.adjusted_size_usd == 3.0