"""Tests for dashboard top CMC market strip."""

from dashboard.market_top import TOP_CMC_WATCHLIST, parse_top_cmc_quotes
from dashboard.holdings import enrich_audit_row, enrich_trade_row


def test_parse_top_cmc_quotes_mcp_table():
    data = {
        "headers": ["id", "name", "symbol", "slug", "price", "rank", "percent_change_24h"],
        "rows": [
            ["1", "Bitcoin", "BTC", "bitcoin", 65000.5, 1, 1.2],
            ["1027", "Ethereum", "ETH", "ethereum", 3500.0, 2, -0.5],
            ["825", "Tether USDt", "USDT", "tether", 1.0, 3, 0.01],
            ["1839", "BNB", "BNB", "bnb", 580.0, 4, 2.1],
            ["52", "XRP", "XRP", "xrp", 1.14, 6, -1.5],
        ],
    }
    coins = parse_top_cmc_quotes(data)
    assert len(coins) == 5
    btc = next(c for c in coins if c["display"] == "BTC")
    assert btc["price_usd"] == 65000.5
    assert btc["cmc_rank"] == 1
    assert btc["change_24h_pct"] == 1.2
    bnb = next(c for c in coins if c["display"] == "BNB")
    assert bnb["price_usd"] == 580.0


def test_parse_top_cmc_quotes_batch():
    data = {
        "data": {
            "1": {
                "id": 1,
                "symbol": "BTC",
                "cmc_rank": 1,
                "quote": {"USD": {"price": 65000.5, "percent_change_24h": 1.2}},
            },
            "1027": {
                "id": 1027,
                "symbol": "ETH",
                "cmc_rank": 2,
                "quote": {"USD": {"price": 3500.0, "percent_change_24h": -0.5}},
            },
        }
    }
    coins = parse_top_cmc_quotes(data, watchlist=TOP_CMC_WATCHLIST[:2])
    assert len(coins) == 2
    btc = next(c for c in coins if c["display"] == "BTC")
    assert btc["price_usd"] == 65000.5
    assert btc["cmc_rank"] == 1
    assert btc["change_24h_pct"] == 1.2


def test_enrich_audit_row():
    row = enrich_audit_row(
        {
            "cycle_id": "42",
            "timestamp": "2026-06-20T12:00:00Z",
            "decision": {
                "action": "HOLD",
                "asset": "BNB",
                "confidence": 0.72,
                "reason": "Waiting for stronger signal",
            },
        }
    )
    assert row["action"] == "hold"
    assert row["asset"] == "BNB"
    assert row["confidence"] == 0.72
    assert "Waiting" in row["reason_short"]


def test_enrich_trade_row_bscscan():
    row = enrich_trade_row(
        {
            "symbol": "USDT/TAG",
            "side": "SELL",
            "amount_usd": 5.5,
            "tx_hash": "0xabc123",
            "timestamp": "2026-06-20T11:00:00Z",
            "status": "confirmed",
        }
    )
    assert row["asset"] == "USDT"
    assert "0xabc123" in row["bscscan_url"]
    assert row["side_lower"] == "sell"