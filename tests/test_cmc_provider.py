"""Tests for CMC MCP provider."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from genesis.core.models import RulesConfig, Signal, SignalCategory
from genesis.data.cmc_provider import (
    CMCProvider,
    _clamp,
    _extract_cmc_rank,
    _parse_pct,
    parse_onchain_metrics,
)
from genesis.data.mcp_client import CMCMCPClient, CMC_MCP_TOOLS
from genesis.data.signal_aggregator import SignalAggregator
from genesis.data.x402_mcp_client import X402MCPClient
from genesis.execution.twak_provider import TWAKProvider


def test_parse_pct():
    assert _parse_pct("+1.5%") == 1.5
    assert _parse_pct("-2.3%") == -2.3
    assert _parse_pct(3.14) == 3.14


def test_clamp():
    assert _clamp(2.0) == 1.0
    assert _clamp(-2.0) == -1.0


def test_mcp_tools_count():
    assert len(CMC_MCP_TOOLS) == 12


@pytest.mark.asyncio
async def test_get_quotes_from_mcp_response():
    provider = CMCProvider(api_key="test-key")
    mock_data = [{"symbol": "BNB", "price": 580.0, "percent_change_24h": 2.5}]

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.get_quotes("BNB", cmc_id=1839)

    assert signal.category == SignalCategory.QUOTE
    assert signal.symbol == "BNB"
    assert signal.value > 0
    assert "cmc_mcp" in signal.source
    await provider.close()


@pytest.mark.asyncio
async def test_get_sentiment_crypto_list_as_array():
    provider = CMCProvider(api_key="test-key")
    mock_global = {"sentiment": {"fear_greed": {"current": {"index": 55}}}}
    mock_search = {
        "cryptoList": [
            {"title": "Article 1", "url": "https://example.com/1"},
            {"title": "Article 2", "url": "https://example.com/2"},
        ]
    }

    with patch.object(provider, "_get_global_metrics", new_callable=AsyncMock) as mock_global_fn:
        mock_global_fn.return_value = mock_global
        with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
            mock_mcp.return_value = mock_search
            signal = await provider.get_sentiment("AXL", cmc_id=17799)

    assert signal.category == SignalCategory.SENTIMENT
    assert signal.raw_data["articles"] == 2
    await provider.close()


@pytest.mark.asyncio
async def test_get_technicals_from_list_payload():
    provider = CMCProvider(api_key="test-key")
    mock_data = [{"rsi": {"rsi14": "62"}, "macd": {"histogram": "1.5"}}]

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.get_technicals("BNB", cmc_id=1839)

    assert signal.category == SignalCategory.TECHNICAL
    assert signal.value > 0
    await provider.close()


@pytest.mark.asyncio
async def test_get_technicals_rsi_bearish():
    provider = CMCProvider(api_key="test-key")
    mock_data = {"rsi": {"rsi14": "35"}, "macd": {"histogram": "-1.2"}}

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.get_technicals("BNB", cmc_id=1839)

    assert signal.category == SignalCategory.TECHNICAL
    assert signal.value < 0
    await provider.close()


@pytest.mark.asyncio
async def test_mcp_client_parse_content():
    result = CMCMCPClient.parse_content(
        {"content": [{"type": "text", "text": json.dumps({"price": 100})}]}
    )
    assert result["price"] == 100


def test_extract_cmc_rank_from_quotes_row():
    assert _extract_cmc_rank({"rank": 4}) == 4
    assert _extract_cmc_rank([{"id": 1839, "rank": 4, "symbol": "BNB"}]) == 4


@pytest.mark.asyncio
async def test_get_crypto_info_signal_rank_from_quotes_fallback():
    provider = CMCProvider(api_key="test-key")
    info_data = {
        "id": 1839,
        "symbol": "BNB",
        "is_active": True,
        "tags": ["layer-1"],
    }
    quote_data = [{"id": 1839, "symbol": "BNB", "rank": 4, "price": 585.0}]

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.side_effect = [info_data, quote_data]
        signal = await provider.get_crypto_info_signal("BNB", cmc_id=1839)

    assert signal.summary == "BNB rank=#4, active=True, tags=1"
    assert signal.value > 0
    assert signal.raw_data["rank"] == 4
    await provider.close()


@pytest.mark.asyncio
async def test_get_all_signals_enriches_metadata_rank_from_quote():
    provider = CMCProvider(api_key="test-key")
    quote = Signal(
        category=SignalCategory.QUOTE,
        symbol="BNB",
        value=0.1,
        raw_data={"rank": 4, "price": 585.0},
    )
    metadata = Signal(
        category=SignalCategory.METADATA,
        symbol="BNB",
        value=0.0,
        raw_data={"tags": ["layer-1"]},
        summary="BNB rank=#?, active=True, tags=1",
    )

    with patch.object(provider, "get_quotes", new_callable=AsyncMock, return_value=quote):
        with patch.object(provider, "get_technicals", new_callable=AsyncMock) as mock_ta:
            with patch.object(provider, "get_sentiment", new_callable=AsyncMock) as mock_sent:
                with patch.object(provider, "get_onchain", new_callable=AsyncMock) as mock_on:
                    with patch.object(provider, "get_news", new_callable=AsyncMock) as mock_news:
                        with patch.object(
                            provider,
                            "get_crypto_info_signal",
                            new_callable=AsyncMock,
                            return_value=metadata,
                        ):
                            with patch.object(
                                provider,
                                "search_cryptos_signal",
                                new_callable=AsyncMock,
                            ) as mock_search:
                                mock_ta.return_value = Signal(
                                    category=SignalCategory.TECHNICAL, symbol="BNB", value=0.0
                                )
                                mock_sent.return_value = Signal(
                                    category=SignalCategory.SENTIMENT, symbol="BNB", value=0.0
                                )
                                mock_on.return_value = Signal(
                                    category=SignalCategory.ONCHAIN, symbol="BNB", value=0.0
                                )
                                mock_news.return_value = Signal(
                                    category=SignalCategory.NEWS, symbol="BNB", value=0.0
                                )
                                mock_search.return_value = Signal(
                                    category=SignalCategory.DISCOVERY, symbol="BNB", value=0.5
                                )
                                signals = await provider.get_all_signals("BNB", 1839)

    meta = next(s for s in signals if s.category == SignalCategory.METADATA)
    deriv = next(s for s in signals if s.category == SignalCategory.DERIVATIVES)
    assert "rank=#4" in meta.summary
    assert meta.raw_data["rank"] == 4
    assert deriv.source == "cmc:token_momentum"
    await provider.close()


@pytest.mark.asyncio
async def test_get_crypto_info_signal_top_rank():
    provider = CMCProvider(api_key="test-key")
    mock_data = {"is_active": True, "cmc_rank": 12, "tags": ["defi", "bnb-chain"]}

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.get_crypto_info_signal("BNB", cmc_id=1839)

    assert signal.category == SignalCategory.METADATA
    assert signal.value > 0
    await provider.close()


@pytest.mark.asyncio
async def test_search_cryptos_signal_id_match():
    provider = CMCProvider(api_key="test-key")
    mock_data = {
        "cryptos": [
            {
                "symbol": "BNB",
                "id": 1839,
                "rank": 4,
                "volume_24h": 2_000_000_000,
            }
        ]
    }

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.search_cryptos_signal("BNB", cmc_id=1839)

    assert signal.category == SignalCategory.DISCOVERY
    assert signal.value > 0.5
    await provider.close()


@pytest.mark.asyncio
async def test_search_cryptos_signal_differs_by_rank():
    provider = CMCProvider(api_key="test-key")

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = {
            "cryptos": [{"symbol": "BNB", "id": 1839, "rank": 4, "volume_24h": 2e9}]
        }
        top = await provider.search_cryptos_signal("BNB", cmc_id=1839)

        mock_mcp.return_value = {
            "cryptos": [{"symbol": "TAG", "id": 99999, "rank": 950, "volume_24h": 1e6}]
        }
        small = await provider.search_cryptos_signal("TAG", cmc_id=99999)

    assert top.value > small.value
    await provider.close()


@pytest.mark.asyncio
async def test_token_momentum_differs_by_quote():
    provider = CMCProvider(api_key="test-key")
    hot = Signal(
        category=SignalCategory.QUOTE,
        symbol="BNB",
        value=0.2,
        raw_data={"percent_change_24h": 8.0, "percent_change_7d": 12.0, "volume_change_24h": 20.0},
    )
    flat = Signal(
        category=SignalCategory.QUOTE,
        symbol="TAG",
        value=0.0,
        raw_data={"percent_change_24h": 0.0, "percent_change_7d": -1.0, "volume_change_24h": -5.0},
    )

    hot_momentum = provider._token_momentum_signal("BNB", hot)
    flat_momentum = provider._token_momentum_signal("TAG", flat)

    assert hot_momentum.value > flat_momentum.value
    assert hot_momentum.category == SignalCategory.DERIVATIVES
    await provider.close()


@pytest.mark.asyncio
async def test_get_marketcap_technicals():
    provider = CMCProvider(api_key="test-key")
    mock_data = {"rsi": {"rsi14": "65"}, "macd": {"histogram": "2.0"}}

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.get_marketcap_technicals()

    assert signal.category == SignalCategory.MARKET
    assert signal.symbol == "MARKET"
    assert signal.value > 0
    await provider.close()


@pytest.mark.asyncio
async def test_get_macro_signal_high_impact():
    provider = CMCProvider(api_key="test-key")
    mock_data = {
        "rows": [
            {"eventName": "CPI", "impact": "high"},
            {"eventName": "FOMC", "impact": "high"},
        ]
    }

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.get_macro_signal()

    assert signal.category == SignalCategory.MACRO
    assert signal.value < 0
    await provider.close()


@pytest.mark.asyncio
async def test_fetch_market_context():
    provider = CMCProvider(api_key="test-key")
    market = Signal(category=SignalCategory.MARKET, symbol="MARKET", value=0.2)
    macro = Signal(
        category=SignalCategory.MACRO,
        symbol="MARKET",
        value=-0.1,
        raw_data={"rows": []},
    )

    derivatives = Signal(
        category=SignalCategory.MARKET,
        symbol="MARKET",
        value=0.1,
        source="cmc_mcp:get_global_crypto_derivatives_metrics",
    )
    with patch.object(provider, "get_marketcap_technicals", new_callable=AsyncMock) as mock_ta:
        with patch.object(provider, "get_macro_signal", new_callable=AsyncMock) as mock_macro:
            with patch.object(
                provider,
                "get_global_derivatives_signal",
                new_callable=AsyncMock,
                return_value=derivatives,
            ):
                mock_ta.return_value = market
                mock_macro.return_value = macro
                ctx = await provider.fetch_market_context()

    assert len(ctx.signals) == 3
    assert ctx.market_conviction_delta != 0
    await provider.close()


def test_parse_onchain_metrics_holding_time():
    data = {
        "addressesByHoldingTime": {
            "holders": {"count": 9117.0},
            "traders": {"count": 677.0},
        }
    }
    holders, traders, value, features = parse_onchain_metrics(data)
    assert holders == 9117.0
    assert traders == 677.0
    assert value > 0
    assert features["data_available"] is True


def test_parse_onchain_metrics_holder_growth():
    data = {
        "coinMarketCapCryptoTotalHolderData": {
            "latestCryptoTotalHolderCount": 32773.0,
            "cryptoTotalHolderCount30dChangePercent": 14.1,
        }
    }
    holders, traders, value, features = parse_onchain_metrics(data)
    assert holders == 32773.0
    assert traders == 0.0
    assert value > 0
    assert features["cryptoTotalHolderCount30dChangePercent"] == 14.1


@pytest.mark.asyncio
async def test_get_onchain_cake_payload():
    provider = CMCProvider(api_key="test-key")
    mock_data = {
        "addressesByHoldingTime": {
            "holders": {"count": 9117.0},
            "traders": {"count": 677.0},
        }
    }

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.get_onchain("CAKE", cmc_id=7186)

    assert signal.category == SignalCategory.ONCHAIN
    assert signal.value > 0
    assert "holders=9117" in signal.summary
    await provider.close()


@pytest.mark.asyncio
async def test_get_onchain_missing_data():
    provider = CMCProvider(api_key="test-key")
    mock_data = {"coinMarketCapCryptoTotalHolderData": {}}

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_data
        signal = await provider.get_onchain("BNB", cmc_id=1839)

    assert signal.category == SignalCategory.ONCHAIN
    assert signal.value == 0.0
    assert "no holder breakdown" in signal.summary
    await provider.close()


@pytest.mark.asyncio
async def test_composite_signal():
    rules = RulesConfig()
    agg = SignalAggregator(rules)
    provider = CMCProvider(api_key="test-key", aggregator=agg)

    with patch.object(provider, "get_all_signals", new_callable=AsyncMock) as mock_all:
        from genesis.core.models import Signal

        mock_all.return_value = [
            Signal(category=SignalCategory.TECHNICAL, symbol="BNB", value=0.5),
            Signal(category=SignalCategory.SENTIMENT, symbol="BNB", value=0.4),
            Signal(category=SignalCategory.ONCHAIN, symbol="BNB", value=0.3),
            Signal(category=SignalCategory.DERIVATIVES, symbol="BNB", value=0.1),
            Signal(category=SignalCategory.NEWS, symbol="BNB", value=0.2),
        ]
        composite = await provider.get_composite_signal("BNB", 1839)

    assert composite.symbol == "BNB"
    assert 0 <= composite.conviction <= 1
    await provider.close()


@pytest.mark.asyncio
async def test_x402_mode_only_without_api_key():
    twak = MagicMock(spec=TWAKProvider)
    x402_client = AsyncMock()
    x402_client.call_tool = AsyncMock(return_value=[{"symbol": "BNB", "price": 600.0, "percent_change_24h": 1.0}])
    x402_client.health_check = AsyncMock(return_value={"payment_mode": "x402", "tool_count": 12})
    x402_client.close = AsyncMock()

    provider = CMCProvider(api_key="", x402_enabled=True, twak=twak, x402_mode="only")
    provider._x402_mcp = x402_client

    assert provider.x402_mode == "only"
    health = await provider.health_check()
    assert health["payment_mode"] == "x402"

    with patch.object(provider, "_safe_mcp", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = [{"symbol": "BNB", "price": 600.0, "percent_change_24h": 1.0}]
        signal = await provider.get_quotes("BNB", cmc_id=1839)

    assert signal.symbol == "BNB"
    await provider.close()


@pytest.mark.asyncio
async def test_x402_fallback_on_api_failure():
    twak = MagicMock(spec=TWAKProvider)
    api_mcp = AsyncMock()
    api_mcp.call_tool = AsyncMock(side_effect=RuntimeError("auth failed"))
    x402_mcp = AsyncMock()
    x402_mcp.call_tool = AsyncMock(return_value={"rsi": {"rsi14": "55"}})

    provider = CMCProvider(api_key="test-key", x402_enabled=True, twak=twak, x402_mode="fallback")
    provider._mcp = api_mcp
    provider._x402_mcp = x402_mcp

    result = await provider._call_mcp("get_crypto_technical_analysis", {"id": "1839"})
    assert result["rsi"]["rsi14"] == "55"
    x402_mcp.call_tool.assert_called_once()
    await provider.close()


@pytest.mark.asyncio
async def test_x402_mcp_client_call_tool():
    twak = MagicMock(spec=TWAKProvider)
    client = X402MCPClient(
        twak=twak,
        wallet_password="test-password",
        prefer_network="base",
        max_payment="10000",
    )
    paid_result = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"content": [{"type": "text", "text": '{"price": 580}'}]},
    }

    async def fake_post(url, json=None, headers=None):
        req = httpx.Request("POST", url, headers=headers or {})
        if headers and headers.get("PAYMENT-SIGNATURE"):
            return httpx.Response(200, json=paid_result, request=req)
        return httpx.Response(402, json={"error": "pay"}, headers={"payment-required": "e30="}, request=req)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=fake_post)
    client._http = mock_http

    mock_x402 = AsyncMock()
    mock_x402.handle_402_response = AsyncMock(return_value=({"PAYMENT-SIGNATURE": "sig"}, None))
    client._x402_http = mock_x402

    result = await client.call_tool("get_crypto_quotes_latest", {"id": "1839"})
    assert result["price"] == 580
    assert mock_http.post.await_count == 2
    await client.close()