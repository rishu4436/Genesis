"""Tests for CMC-first price resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from genesis.core.models import TokenConfig
from genesis.data.cmc_provider import CMCProvider
from genesis.data.price_cache import cache_clear
from genesis.data.price_resolver import PriceResolver

TAG = TokenConfig(
    symbol="TAG",
    address="0x208bf3e7da9639f1eaefa2de78c23396b0682025",
    cmc_id=34958,
)


@pytest.fixture(autouse=True)
def _clear_price_cache():
    cache_clear()
    yield
    cache_clear()


@pytest.mark.asyncio
async def test_price_resolver_prefers_cmc_over_twak():
    twak = MagicMock()
    twak.get_price_for_token = AsyncMock(return_value=0.001)

    cmc = MagicMock(spec=CMCProvider)
    cmc.get_usd_price = AsyncMock(return_value=0.00094)

    resolver = PriceResolver(twak, cmc)
    price, source = await resolver.usd_price_for_token(TAG)

    assert price == pytest.approx(0.00094)
    assert source == "cmc"
    twak.get_price_for_token.assert_not_called()


@pytest.mark.asyncio
async def test_price_resolver_twak_fallback_when_cmc_fails():
    twak = MagicMock()
    twak.get_price_for_token = AsyncMock(return_value=0.001)

    cmc = MagicMock(spec=CMCProvider)
    cmc.get_usd_price = AsyncMock(side_effect=RuntimeError("cmc down"))

    resolver = PriceResolver(twak, cmc)
    price, source = await resolver.usd_price_for_token(TAG)

    assert price == pytest.approx(0.001)
    assert source == "twak"


def test_parse_batch_quotes():
    data = {
        "data": {
            "34958": {
                "quote": {
                    "USD": {"price": 0.00094},
                },
            },
        },
    }
    prices = CMCProvider._parse_batch_quotes(data, {34958: "TAG"})
    assert prices["TAG"] == pytest.approx(0.00094)