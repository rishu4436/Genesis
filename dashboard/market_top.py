"""Top CMC-ranked coins for dashboard market strip."""

from __future__ import annotations

from typing import Any

from genesis.data.cmc_provider import CMCProvider, _extract_cmc_rank, _parse_pct

# Global top-5 by market cap (BSC allowlist symbols)
TOP_CMC_WATCHLIST: list[dict[str, Any]] = [
    {"symbol": "BTCB", "display": "BTC", "cmc_id": 1},
    {"symbol": "ETH", "display": "ETH", "cmc_id": 1027},
    {"symbol": "USDT", "display": "USDT", "cmc_id": 825},
    {"symbol": "BNB", "display": "BNB", "cmc_id": 1839},
    {"symbol": "XRP", "display": "XRP", "cmc_id": 52},
]


def _safe_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _safe_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def _parse_quote_row(row: dict[str, Any], *, display: str, symbol: str) -> dict[str, Any]:
    """REST API shape: { id, symbol, quote: { USD: { price, percent_change_24h } } }."""
    quote_usd = row.get("quote", {})
    if isinstance(quote_usd, dict):
        quote_usd = quote_usd.get("USD", quote_usd)
    if not isinstance(quote_usd, dict):
        quote_usd = {}

    price = quote_usd.get("price", row.get("price"))
    change_raw = quote_usd.get(
        "percent_change_24h",
        quote_usd.get("percentChange24h", row.get("percent_change_24h")),
    )
    rank = _extract_cmc_rank(row) or _extract_cmc_rank(quote_usd) or _safe_int(row.get("rank"))

    return {
        "symbol": symbol,
        "display": display,
        "cmc_id": row.get("id"),
        "cmc_rank": rank,
        "price_usd": _safe_float(price),
        "change_24h_pct": round(_parse_pct(change_raw), 2) if change_raw is not None else None,
    }


def _parse_mcp_table(data: dict[str, Any], watchlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CMC MCP tabular shape: { headers: [...], rows: [[...], ...] }."""
    headers = data.get("headers")
    rows = data.get("rows")
    if not isinstance(headers, list) or not isinstance(rows, list):
        return []

    col = {str(h): i for i, h in enumerate(headers)}
    id_idx = col.get("id", 0)
    sym_idx = col.get("symbol")
    price_idx = col.get("price")
    rank_idx = col.get("rank") if "rank" in col else col.get("cmc_rank")
    change_idx = col.get("percent_change_24h")

    id_to_meta = {int(w["cmc_id"]): w for w in watchlist}
    sym_to_meta: dict[str, dict[str, Any]] = {}
    for w in watchlist:
        sym_to_meta[w["symbol"].upper()] = w
        sym_to_meta[w["display"].upper()] = w

    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        cid = _safe_int(row[id_idx] if id_idx < len(row) else None)
        if cid is None:
            continue
        meta = id_to_meta.get(cid)
        if meta is None and sym_idx is not None and sym_idx < len(row):
            meta = sym_to_meta.get(str(row[sym_idx]).upper())
        if meta is None:
            continue
        sym = meta["symbol"].upper()
        if sym in seen:
            continue
        seen.add(sym)

        price = _safe_float(row[price_idx]) if price_idx is not None and price_idx < len(row) else None
        rank = _safe_int(row[rank_idx]) if rank_idx is not None and rank_idx < len(row) else None
        change = (
            round(_parse_pct(row[change_idx]), 2)
            if change_idx is not None and change_idx < len(row)
            else None
        )

        parsed.append(
            {
                "symbol": sym,
                "display": meta["display"],
                "cmc_id": cid,
                "cmc_rank": rank,
                "price_usd": price,
                "change_24h_pct": change,
            }
        )

    return parsed


def parse_top_cmc_quotes(data: Any, watchlist: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Parse CMC quotes (MCP table or REST) into sorted top-coin cards."""
    watchlist = watchlist or TOP_CMC_WATCHLIST

    parsed: list[dict[str, Any]] = []
    if isinstance(data, dict) and "headers" in data and "rows" in data:
        parsed = _parse_mcp_table(data, watchlist)

    if not parsed and isinstance(data, dict):
        sym_to_meta = {w["symbol"].upper(): w for w in watchlist}
        id_to_meta = {int(w["cmc_id"]): w for w in watchlist}
        nested = data.get("data", data)
        if isinstance(nested, dict):
            seen: set[str] = set()
            for cid, row in nested.items():
                if not isinstance(row, dict):
                    continue
                try:
                    cid_int = int(cid)
                except (TypeError, ValueError):
                    cid_int = _safe_int(row.get("id")) or 0
                meta = id_to_meta.get(cid_int)
                if not meta:
                    sym_key = str(row.get("symbol", "")).upper()
                    meta = sym_to_meta.get(sym_key)
                if not meta:
                    continue
                sym = meta["symbol"].upper()
                if sym in seen:
                    continue
                seen.add(sym)
                parsed.append(_parse_quote_row(row, display=meta["display"], symbol=sym))

    seen_syms = {c["symbol"].upper() for c in parsed}
    for w in watchlist:
        if w["symbol"].upper() not in seen_syms:
            parsed.append(_empty_coin(w))

    parsed.sort(key=lambda c: (c.get("cmc_rank") or 9999, c.get("display", "")))
    return parsed[:5]


def _empty_coin(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": meta["symbol"],
        "display": meta["display"],
        "cmc_id": meta["cmc_id"],
        "cmc_rank": None,
        "price_usd": None,
        "change_24h_pct": None,
    }


def coins_have_prices(coins: list[dict[str, Any]]) -> bool:
    return any(c.get("price_usd") is not None for c in coins)


async def fetch_top_cmc_coins(cmc: CMCProvider) -> list[dict[str, Any]]:
    """Live top-5 CMC market snapshot."""
    tokens = [(w["symbol"], int(w["cmc_id"])) for w in TOP_CMC_WATCHLIST]
    ids = ",".join(str(cmc_id) for _, cmc_id in tokens)
    data = await cmc._safe_mcp(
        "get_crypto_quotes_latest",
        {"id": ids},
        "cryptocurrency/quotes/latest",
        {"id": ids, "convert": "USD"},
    )
    coins = parse_top_cmc_quotes(data)

    if coins_have_prices(coins):
        return coins

    # REST fallback per coin when MCP table missing
    for coin in coins:
        if coin.get("price_usd") is not None:
            continue
        meta = next((w for w in TOP_CMC_WATCHLIST if w["symbol"] == coin["symbol"]), None)
        if not meta:
            continue
        try:
            price = await cmc.get_usd_price(meta["symbol"], int(meta["cmc_id"]))
            coin["price_usd"] = price
        except Exception:
            pass
        try:
            signal = await cmc.get_quotes(meta["symbol"], int(meta["cmc_id"]))
            raw = signal.raw_data if hasattr(signal, "raw_data") else {}
            if isinstance(raw, dict):
                coin["change_24h_pct"] = round(
                    _parse_pct(raw.get("percent_change_24h", raw.get("percentChange24h", 0))),
                    2,
                )
                coin["cmc_rank"] = coin.get("cmc_rank") or _extract_cmc_rank(raw)
        except Exception:
            pass

    return coins