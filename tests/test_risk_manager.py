"""Unit tests for RiskManager."""

from genesis.core.models import (
    Action,
    Decision,
    PortfolioSnapshot,
    Position,
    RulesConfig,
    TokenConfig,
)
from genesis.decision.risk_manager import RiskManager


def _make_rules() -> RulesConfig:
    rules = RulesConfig()
    rules.allowed_tokens = [
        TokenConfig(symbol="BNB", address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", cmc_id=1839),
    ]
    rules.risk.min_confidence = 0.65
    rules.risk.max_portfolio_risk_per_trade_pct = 2.0
    return rules


def _make_portfolio(value: float = 1000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(total_value_usd=value, available_usd=value * 0.8)


def test_hold_always_approved():
    rm = RiskManager(_make_rules())
    decision = Decision(action=Action.HOLD, asset="BNB", reason="Wait", confidence=0.5)
    result = rm.validate(decision, _make_portfolio())
    assert result.approved


def test_buy_rejected_low_confidence():
    rm = RiskManager(_make_rules())
    decision = Decision(
        action=Action.BUY, asset="BNB", size_pct=1.0,
        reason="Low conf", confidence=0.3,
    )
    result = rm.validate(decision, _make_portfolio())
    assert not result.approved
    assert any("Confidence" in v for v in result.violations)


def test_buy_rejected_unlisted_token():
    rm = RiskManager(_make_rules())
    decision = Decision(
        action=Action.BUY, asset="SCAM", size_pct=1.0,
        reason="Bad token", confidence=0.8,
    )
    result = rm.validate(decision, _make_portfolio())
    assert not result.approved
    assert any("allowlist" in v for v in result.violations)


def test_buy_approved_valid():
    rm = RiskManager(_make_rules())
    portfolio = PortfolioSnapshot(
        total_value_usd=1000.0,
        available_usd=800.0,
        positions=[
            Position(symbol="USDT", amount=800.0, entry_price=0, current_price=1.0),
        ],
    )
    decision = Decision(
        action=Action.BUY, asset="BNB", size_pct=0.5,
        reason="Good setup", confidence=0.8,
    )
    result = rm.validate(decision, portfolio)
    assert result.approved
    assert result.adjusted_size_usd == 4.0  # 0.5% of 800 USDT


def test_buy_allowed_when_only_bnb_usdt_and_one_trade():
    rules = _make_rules()
    rules.allowed_tokens.append(
        TokenConfig(symbol="TAG", address="0x208bf3e7da9639f1eaefa2de78c23396b0682025", cmc_id=34958),
    )
    rules.risk.max_open_positions = 3
    rm = RiskManager(rules)
    portfolio = PortfolioSnapshot(
        total_value_usd=11.0,
        available_usd=9.0,
        positions=[
            Position(symbol="BNB", amount=0.002, entry_price=0, current_price=600.0),
            Position(symbol="USDT", amount=9.0, entry_price=0, current_price=1.0),
            Position(symbol="TAG", amount=0.001, entry_price=0, current_price=0.001),
        ],
    )
    decision = Decision(
        action=Action.BUY,
        asset="TAG",
        size_usd=0.45,
        reason="Add to position",
        confidence=0.8,
    )
    result = rm.validate(decision, portfolio)
    assert result.approved
    assert result.adjusted_size_usd == 1.0


def test_buy_rejected_when_trading_positions_at_limit():
    rules = _make_rules()
    rules.allowed_tokens.extend(
        [
            TokenConfig(symbol="TAG", address="0x1", cmc_id=1),
            TokenConfig(symbol="CAKE", address="0x2", cmc_id=2),
            TokenConfig(symbol="ETH", address="0x3", cmc_id=3),
        ]
    )
    rules.risk.max_open_positions = 3
    rm = RiskManager(rules)
    portfolio = PortfolioSnapshot(
        total_value_usd=100.0,
        available_usd=50.0,
        positions=[
            Position(symbol="USDT", amount=10.0, entry_price=0, current_price=1.0),
            Position(symbol="TAG", amount=1.0, entry_price=0, current_price=1.0),
            Position(symbol="CAKE", amount=1.0, entry_price=0, current_price=1.0),
            Position(symbol="ETH", amount=0.1, entry_price=0, current_price=100.0),
        ],
    )
    decision = Decision(
        action=Action.BUY,
        asset="TAG",
        size_usd=5.0,
        reason="Fourth trade",
        confidence=0.9,
    )
    result = rm.validate(decision, portfolio)
    assert not result.approved
    assert any("Max open positions" in v for v in result.violations)


def test_drawdown_halt():
    rm = RiskManager(_make_rules())
    rules = _make_rules()
    rules.risk.max_drawdown_pct = 5.0
    rm = RiskManager(rules)

    portfolio = PortfolioSnapshot(total_value_usd=900.0, available_usd=800.0)
    rm._peak_portfolio_value = 1000.0
    rm.update_peak(portfolio)

    assert rm.is_halted
    decision = Decision(
        action=Action.BUY, asset="BNB", size_pct=1.0,
        reason="Should fail", confidence=0.9,
    )
    result = rm.validate(decision, portfolio)
    assert not result.approved