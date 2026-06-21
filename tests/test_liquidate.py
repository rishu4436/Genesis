"""Tests for sell-all liquidation helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from genesis.core.models import PortfolioSnapshot, Position, TokenConfig
from genesis.core.models import TradeStatus
from genesis.execution.liquidate import (
    collect_sell_targets,
    format_sell_amount,
    is_unsellable_dust,
    sell_all_to_usdt,
)

TAG = TokenConfig(
    symbol="TAG",
    address="0x208bf3e7da9639f1eaefa2de78c23396b0682025",
    cmc_id=34958,
)
USDT = TokenConfig(
    symbol="USDT",
    address="0x55d398326f99059fF775485246999027B3197955",
    cmc_id=825,
)


def test_is_unsellable_dust():
    assert is_unsellable_dust(0.005)
    assert is_unsellable_dust(0.009)
    assert not is_unsellable_dust(0.0)
    assert not is_unsellable_dust(0.02)


def test_format_sell_amount_buffers_large_balances():
    assert format_sell_amount(7944.2) == 7904.0


def test_format_sell_amount_small_balance():
    assert format_sell_amount(0.2048) == pytest.approx(0.2037, rel=1e-3)


def test_collect_sell_targets_skips_bnb_and_usdt():
    portfolio = PortfolioSnapshot(
        total_value_usd=20.0,
        available_usd=10.0,
        positions=[
            Position(symbol="BNB", amount=0.01, entry_price=0, current_price=600.0),
            Position(symbol="USDT", amount=10.0, entry_price=0, current_price=1.0),
            Position(symbol="TAG", amount=7944.0, entry_price=0, current_price=0.0011),
        ],
    )
    targets = collect_sell_targets(
        portfolio,
        {"TAG": TAG, "USDT": USDT},
        min_usd=0.0,
    )
    assert len(targets) == 1
    assert targets[0].symbol == "TAG"
    assert targets[0].amount == 7904.0


def test_collect_sell_targets_includes_dust_when_min_usd_zero():
    portfolio = PortfolioSnapshot(
        total_value_usd=1.0,
        available_usd=1.0,
        positions=[
            Position(symbol="TAG", amount=0.001055, entry_price=0, current_price=0.001),
        ],
    )
    targets = collect_sell_targets(portfolio, {"TAG": TAG}, min_usd=0.0)
    assert len(targets) == 1
    assert targets[0].symbol == "TAG"
    assert targets[0].amount > 0


def test_collect_sell_targets_uses_on_chain_balance():
    portfolio = PortfolioSnapshot(total_value_usd=1.0, available_usd=1.0, positions=[])
    targets = collect_sell_targets(
        portfolio,
        {"TAG": TAG},
        min_usd=0.0,
        on_chain_balances={"TAG": 0.001055},
    )
    assert len(targets) == 1
    assert targets[0].amount == pytest.approx(0.00104973, rel=1e-3)


def test_collect_sell_targets_skips_unknown_and_dust():
    portfolio = PortfolioSnapshot(
        total_value_usd=1.0,
        available_usd=1.0,
        positions=[
            Position(symbol="UNKNOWN", amount=100.0, entry_price=0, current_price=0.01),
            Position(symbol="TAG", amount=1.0, entry_price=0, current_price=0.001),
        ],
    )
    targets = collect_sell_targets(portfolio, {"TAG": TAG}, min_usd=0.5)
    assert targets == []


@pytest.mark.asyncio
async def test_sell_all_skips_dex_dust():
    from genesis.core.config import get_rules
    from genesis.execution.twak_provider import TWAKProvider

    rules = get_rules()
    twak = TWAKProvider(cli_path="twak", chain="bsc")
    portfolio = PortfolioSnapshot(
        total_value_usd=0.001,
        available_usd=0.001,
        positions=[
            Position(symbol="TAG", amount=5.27e-06, entry_price=0, current_price=0.001),
        ],
    )

    with (
        patch.object(twak, "get_portfolio", new_callable=AsyncMock) as mock_portfolio,
        patch.object(twak, "get_wallet_address", new_callable=AsyncMock) as mock_wallet,
        patch(
            "genesis.execution.liquidate._fetch_on_chain_balances",
            new_callable=AsyncMock,
            return_value={"TAG": 5.27e-06},
        ),
        patch(
            "genesis.execution.liquidate._on_chain_balance",
            new_callable=AsyncMock,
            return_value=5.27e-06,
        ),
    ):
        mock_portfolio.return_value = portfolio
        mock_wallet.return_value = "0xabc"
        targets, trades = await sell_all_to_usdt(
            twak,
            rules,
            dry_run=False,
            supplement_symbols=["TAG"],
            dust_threshold_usd=0.01,
        )

    assert len(targets) == 1
    assert len(trades) == 1
    assert trades[0].status == TradeStatus.SKIPPED
    assert "DEX minimum" in (trades[0].error or "")


@pytest.mark.asyncio
async def test_sell_all_dry_run():
    from genesis.core.config import get_rules
    from genesis.execution.twak_provider import TWAKProvider

    rules = get_rules()
    twak = TWAKProvider(cli_path="twak", chain="bsc")
    portfolio = PortfolioSnapshot(
        total_value_usd=20.0,
        available_usd=10.0,
        positions=[
            Position(symbol="TAG", amount=100.0, entry_price=0, current_price=0.01),
        ],
    )

    with (
        patch.object(twak, "get_portfolio", new_callable=AsyncMock) as mock_portfolio,
        patch.object(twak, "get_wallet_address", new_callable=AsyncMock) as mock_wallet,
        patch(
            "genesis.execution.liquidate._fetch_on_chain_balances",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        mock_portfolio.return_value = portfolio
        mock_wallet.return_value = "0xabc"
        targets, trades = await sell_all_to_usdt(
            twak,
            rules,
            dry_run=True,
            supplement_symbols=["TAG"],
        )

    assert len(targets) == 1
    assert trades == []
    mock_portfolio.assert_awaited_once()