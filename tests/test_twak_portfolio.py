"""Tests for TWAK portfolio parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from genesis.core.models import TokenConfig
from genesis.execution.twak_provider import TWAKProvider

TAG_TOKEN = TokenConfig(
    symbol="TAG",
    address="0x208bf3e7da9639f1eaefa2de78c23396b0682025",
    cmc_id=34958,
)


def test_parse_flat_portfolio_data():
    result = {
        "data": [
            {
                "chain": "bsc",
                "symbol": "BNB",
                "balance": "0.0025",
                "usdValue": 1.46,
            },
            {
                "chain": "bsc",
                "symbol": "USDT",
                "balance": "9.78",
                "usdValue": 9.77,
            },
        ]
    }
    holdings = TWAKProvider._parse_portfolio_holdings(result)
    assert len(holdings) == 2
    assert holdings[1]["symbol"] == "USDT"
    assert holdings[1]["balance"] == 9.78


@pytest.mark.asyncio
async def test_get_portfolio_uses_usdt_as_available():
    twak = TWAKProvider(cli_path="twak", chain="bsc")
    portfolio_payload = {
        "data": [
            {"symbol": "BNB", "balance": "0.0025", "usdValue": 1.46},
            {"symbol": "USDT", "balance": "9.78", "usdValue": 9.77},
        ]
    }
    with patch.object(twak, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = portfolio_payload
        snap = await twak.get_portfolio("USDT")

    assert snap.total_value_usd == pytest.approx(11.23, rel=0.01)
    assert snap.available_usd == pytest.approx(9.77, rel=0.01)
    assert {p.symbol for p in snap.positions} == {"BNB", "USDT"}


def test_resolve_supplement_tokens_maps_allowlist():
    tokens = TWAKProvider.resolve_supplement_tokens(
        ["TAG", "TAG", "UNKNOWN"],
        [TAG_TOKEN],
    )
    assert len(tokens) == 1
    assert tokens[0].symbol == "TAG"


@pytest.mark.asyncio
async def test_get_portfolio_supplements_onchain_tag():
    twak = TWAKProvider(cli_path="twak", chain="bsc")
    portfolio_payload = {
        "data": [
            {"symbol": "BNB", "balance": "0.0025", "usdValue": 1.46},
            {"symbol": "USDT", "balance": "9.78", "usdValue": 9.77},
        ]
    }

    async def fake_balance(*_args, **_kwargs):
        return 7944.2

    with (
        patch.object(twak, "_run", new_callable=AsyncMock) as mock_run,
        patch.object(twak, "get_wallet_address", new_callable=AsyncMock) as mock_addr,
        patch(
            "genesis.execution.twak_provider.erc20_balance_of",
            new_callable=AsyncMock,
            side_effect=fake_balance,
        ),
        patch.object(
            twak,
            "get_price_for_token",
            new_callable=AsyncMock,
            return_value=0.0011,
        ),
    ):
        mock_run.return_value = portfolio_payload
        mock_addr.return_value = "0x000000000000000000000000000000000000dEaD"
        snap = await twak.get_portfolio("USDT", supplement_tokens=[TAG_TOKEN])

    symbols = {p.symbol for p in snap.positions}
    assert "TAG" in symbols
    tag = next(p for p in snap.positions if p.symbol == "TAG")
    assert tag.amount == pytest.approx(7944.2, rel=1e-6)
    assert tag.current_price == pytest.approx(0.0011, rel=1e-6)
    assert snap.total_value_usd == pytest.approx(11.23 + 7944.2 * 0.0011, rel=0.01)


@pytest.mark.asyncio
async def test_supplement_removes_stale_zero_balance():
    twak = TWAKProvider(cli_path="twak", chain="bsc")
    portfolio_payload = {
        "data": [
            {"symbol": "BNB", "balance": "0.0025", "usdValue": 1.46},
            {"symbol": "USDT", "balance": "9.78", "usdValue": 9.77},
            {"symbol": "TAG", "balance": "8394.0", "usdValue": 9.0},
        ]
    }

    with (
        patch.object(twak, "_run", new_callable=AsyncMock) as mock_run,
        patch.object(twak, "get_wallet_address", new_callable=AsyncMock) as mock_addr,
        patch(
            "genesis.execution.twak_provider.erc20_balance_of",
            new_callable=AsyncMock,
            return_value=0.0,
        ),
    ):
        mock_run.return_value = portfolio_payload
        mock_addr.return_value = "0x000000000000000000000000000000000000dEaD"
        snap = await twak.get_portfolio("USDT", supplement_tokens=[TAG_TOKEN])

    assert "TAG" not in {p.symbol for p in snap.positions}