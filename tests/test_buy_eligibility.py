"""Tests for stricter BUY candidate filtering."""

from genesis.core.config import EnvSettings
from genesis.core.models import Action, CompositeSignal, PortfolioSnapshot, RulesConfig, TokenConfig
from genesis.decision.candidate_selection import is_buy_eligible
from genesis.decision.risk_manager import RiskManager
from genesis.decision.strategy_engine import NON_TRADABLE_SYMBOLS, StrategyEngine


def _composite(**kwargs) -> CompositeSignal:
    defaults = {
        "symbol": "CAKE",
        "conviction": 0.65,
        "direction": "bullish",
        "components": {
            "technicals": 0.62,
            "sentiment": 0.58,
            "onchain": 0.50,
            "news": 0.50,
        },
        "features": {"buy_alignment": 2},
        "summary": "test",
    }
    defaults.update(kwargs)
    return CompositeSignal(**defaults)


def test_buy_eligible_requires_multi_signal_alignment():
    rules = RulesConfig()
    ok = _composite()
    weak = _composite(
        components={
            "technicals": 0.70,
            "sentiment": 0.40,
            "onchain": 0.45,
            "news": 0.45,
        }
    )

    assert is_buy_eligible(ok, rules, non_tradable=NON_TRADABLE_SYMBOLS)
    assert not is_buy_eligible(weak, rules, non_tradable=NON_TRADABLE_SYMBOLS)


def test_rule_based_rejects_single_signal_spike():
    env = EnvSettings(llm_enabled=False, xai_api_key="")
    rules = RulesConfig()
    rules.allowed_tokens = [
        TokenConfig(symbol="CAKE", address="0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", cmc_id=7186),
    ]
    engine = StrategyEngine(env, rules, RiskManager(rules))
    portfolio = PortfolioSnapshot(total_value_usd=11.0, available_usd=9.78)

    decision = engine.decide_rule_based(
        [
            _composite(
                conviction=0.68,
                components={
                    "technicals": 0.80,
                    "sentiment": 0.40,
                    "onchain": 0.48,
                    "news": 0.48,
                    "derivatives": 0.75,
                    "discovery": 0.75,
                },
            )
        ],
        portfolio,
    )

    assert decision.action == Action.HOLD