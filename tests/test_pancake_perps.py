"""Tests for PancakeSwap Perps calldata and parameter building."""

from __future__ import annotations

import pytest

from genesis.execution.pancake_perps import (
    MIN_NOTIONAL_USD,
    build_open_params,
    compute_acceptable_price,
    compute_qty,
    resolve_perps_market,
)
from genesis.execution.perps_executor import PerpsExecutor


def test_resolve_perps_market_bnb():
    pair, market = resolve_perps_market("BNB")
    assert market == "BNBUSD"
    assert pair.startswith("0x")


def test_resolve_perps_market_aster():
    pair, market = resolve_perps_market("ASTER")
    assert market == "ASTERUSD"


def test_resolve_perps_market_trx():
    pair, market = resolve_perps_market("TRX")
    assert market == "TRXUSD"
    assert pair.endswith("b12e3")


def test_resolve_perps_market_twt_unavailable():
    with pytest.raises(ValueError, match="No PancakeSwap Perps market"):
        resolve_perps_market("TWT")


def test_resolve_perps_market_unknown():
    with pytest.raises(ValueError, match="No PancakeSwap Perps market"):
        resolve_perps_market("TAG")


def test_compute_qty_from_notional():
    # $500 notional at $500 index → qty = 500 * 1e18 / (500 * 1e8) = 1e10
    qty = compute_qty(500.0, int(500 * 1e8))
    assert qty == 10**10


def test_compute_acceptable_price_long_slippage():
    price = compute_acceptable_price(100.0, is_long=True, slippage_bps=100)
    assert price == int(101.0 * 1e8)


def test_build_open_params_rejects_below_min():
    with pytest.raises(ValueError, match=str(int(MIN_NOTIONAL_USD))):
        build_open_params(
            "BNB",
            notional_usd=50.0,
            leverage=2,
            is_long=True,
            index_price=600.0,
            slippage_bps=50,
        )


def test_build_open_params_success():
    params = build_open_params(
        "ETH",
        notional_usd=250.0,
        leverage=5,
        is_long=True,
        index_price=3000.0,
        slippage_bps=50,
    )
    assert params.market_symbol == "ETHUSD"
    assert params.is_long is True
    assert params.leverage == 5
    assert params.amount_in_wei == int(50 * 1e18)


def test_perps_executor_supports_symbol():
    from genesis.core.config import get_rules
    from unittest.mock import MagicMock

    twak = MagicMock()
    rules = get_rules()
    executor = PerpsExecutor(twak, rules)
    assert executor.supports_symbol("BNB") is True
    assert executor.supports_symbol("ASTER") is True
    assert executor.supports_symbol("UNI") is True
    assert executor.supports_symbol("TRX") is True
    assert executor.supports_symbol("TWT") is False  # allowlisted but no ApolloX market
    assert executor.supports_symbol("TAG") is False  # not in perps allowlist


@pytest.mark.asyncio
async def test_perps_open_dry_run(monkeypatch):
    from genesis.core.config import get_rules
    from genesis.core.models import Action, TradeStatus
    from unittest.mock import MagicMock

    async def fake_price(_market: str) -> float:
        return 600.0

    monkeypatch.setattr(
        "genesis.execution.perps_executor.fetch_index_price",
        fake_price,
    )

    twak = MagicMock()
    rules = get_rules()
    executor = PerpsExecutor(twak, rules)

    trade = await executor.open_position(
        "BNB",
        Action.BUY,
        250.0,
        leverage=2,
        dry_run=True,
    )
    assert trade.simulated is True
    assert trade.execution_type == "perps"
    assert trade.status == TradeStatus.SIMULATED
    assert trade.leverage == 2