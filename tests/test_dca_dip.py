"""Tests for DCA dip-buy strategy."""

from genesis.core.models import (
    Action,
    CompositeSignal,
    DcaDipConfig,
    DcaPositionState,
    PortfolioSnapshot,
    Position,
    RulesConfig,
)
from genesis.decision.dca_dip import (
    compute_dca_state_after_buy,
    decide_dca_dip,
)


def _rules() -> RulesConfig:
    rules = RulesConfig()
    rules.dca_dip = DcaDipConfig(
        enabled=True,
        max_cmc_rank=100,
        trigger_drop_24h_pct=20.0,
        step_drop_pct=10.0,
        take_profit_pct=20.0,
    )
    return rules


def _composite(
    symbol: str,
    *,
    rank: int,
    change_24h: float,
    price: float,
) -> CompositeSignal:
    return CompositeSignal(
        symbol=symbol,
        conviction=0.5,
        direction="bearish",
        components={},
        features={
            "cmc_rank": rank,
            "change_24h_pct": change_24h,
            "price_usd": price,
            "market_cap_usd": 1_000_000_000,
        },
    )


def _portfolio(usdt: float = 100.0, positions: list[Position] | None = None) -> PortfolioSnapshot:
    base = [Position(symbol="USDT", amount=usdt, entry_price=1.0, current_price=1.0)]
    return PortfolioSnapshot(
        total_value_usd=usdt + sum(getattr(p, "value_usd", 0) or 0 for p in (positions or [])),
        available_usd=usdt,
        positions=base + (positions or []),
    )


def test_first_dca_buy_on_20pct_24h_drop():
    rules = _rules()
    composites = [_composite("LINK", rank=12, change_24h=-22.0, price=10.0)]
    decision = decide_dca_dip(composites, _portfolio(), rules, {})

    assert decision.action == Action.BUY
    assert decision.asset == "LINK"
    assert decision.dca_step == 1
    assert decision.strategy_mode == "dca_dip"
    assert decision.current_price_usd == 10.0
    assert decision.take_profit_price_usd == 12.0
    assert "-22.0%" in decision.reason or "-22%" in decision.reason


def test_no_buy_when_24h_drop_below_threshold():
    rules = _rules()
    composites = [_composite("ETH", rank=2, change_24h=-15.0, price=3000.0)]
    decision = decide_dca_dip(composites, _portfolio(), rules, {})

    assert decision.action == Action.HOLD


def test_ladder_buy_after_10pct_fall_from_last_buy():
    rules = _rules()
    state = DcaPositionState(
        symbol="LINK",
        buy_count=1,
        last_buy_price_usd=10.0,
        avg_entry_price_usd=10.0,
        total_cost_usd=5.0,
        trigger_change_24h_pct=-25.0,
    )
    composites = [_composite("LINK", rank=12, change_24h=-30.0, price=8.9)]
    decision = decide_dca_dip(composites, _portfolio(), rules, {"LINK": state})

    assert decision.action == Action.BUY
    assert decision.dca_step == 2


def test_take_profit_sell_at_20pct_gain():
    rules = _rules()
    state = DcaPositionState(
        symbol="CAKE",
        buy_count=2,
        last_buy_price_usd=2.0,
        avg_entry_price_usd=2.0,
        total_cost_usd=10.0,
        trigger_change_24h_pct=-24.0,
    )
    composites = [_composite("CAKE", rank=50, change_24h=-10.0, price=2.5)]
    portfolio = _portfolio(
        usdt=50.0,
        positions=[
            Position(symbol="CAKE", amount=5.0, entry_price=2.0, current_price=2.5),
        ],
    )
    decision = decide_dca_dip(composites, portfolio, rules, {"CAKE": state})

    assert decision.action == Action.SELL
    assert decision.exit_trigger == "dca_take_profit"
    assert "+25.0%" in decision.reason or "+25%" in decision.reason


def test_compute_dca_average_entry():
    state = compute_dca_state_after_buy(
        None,
        symbol="LINK",
        buy_price=10.0,
        buy_usd=5.0,
        change_24h_pct=-22.0,
        dca_step=1,
    )
    assert state.buy_count == 1
    assert state.avg_entry_price_usd == 10.0

    state2 = compute_dca_state_after_buy(
        state,
        symbol="LINK",
        buy_price=9.0,
        buy_usd=5.0,
        change_24h_pct=-30.0,
        dca_step=2,
    )
    assert state2.buy_count == 2
    assert state2.avg_entry_price_usd < 10.0
    assert state2.avg_entry_price_usd > 9.0