"""Tests for TWAK swap metric parsing."""

from genesis.core.models import Action
from genesis.execution.twak_provider import TWAKProvider


def test_parse_swap_leg():
    amount, symbol = TWAKProvider._parse_swap_leg("2844.969656294314458912 TAG")
    assert amount == 2844.969656294314458912
    assert symbol == "TAG"


def test_swap_trade_metrics_buy():
    result = {
        "input": "8.3 USDT",
        "output": "7865.12 TAG",
        "hash": "0xabc",
    }
    qty, price = TWAKProvider()._swap_trade_metrics(
        result,
        from_token="USDT",
        to_token="TAG",
        amount_usd=8.3,
        buying_asset=True,
    )
    assert qty == 7865.12
    assert price is not None
    assert abs(price - 8.3 / 7865.12) < 1e-9


def test_swap_builds_trade_with_entry_price():
    provider = TWAKProvider(cli_path="twak", chain="bsc")
    result = {
        "input": "8.3 USDT",
        "output": "7865.12 TAG",
        "hash": "0xabc",
    }
    trade = provider._build_trade_from_swap_result(
        result,
        from_token="USDT",
        to_token="TAG",
        amount=8.3,
        slippage_bps=100,
        amount_is_usd=True,
        trade_side=Action.BUY,
    )
    assert trade.amount_token == 7865.12
    assert trade.price is not None
    assert trade.amount_usd == 8.3