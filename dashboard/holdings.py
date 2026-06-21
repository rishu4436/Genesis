"""Build wallet holdings view for the Genesis dashboard."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from genesis.core.models import PortfolioSnapshot, TokenConfig
from genesis.core.wallet_tokens import is_gas_or_stable
from genesis.data.price_resolver import STABLE_USD, PriceResolver
from genesis.execution.twak_provider import TWAKProvider

# Hide wallet dust from holdings UI (sub-cent balances)
DUST_VALUE_USD = 0.01


def is_negligible_position(
    amount: float,
    *,
    value_usd: float | None = None,
    price: float | None = None,
) -> bool:
    """True when a balance is zero or economically irrelevant."""
    if amount <= 0:
        return True
    est_value = value_usd if value_usd is not None else amount * (price or 0.0)
    return 0 < est_value < DUST_VALUE_USD


def entry_prices_for_holdings(
    entry_prices: dict[str, dict],
    holdings: list[dict[str, Any]],
) -> dict[str, dict]:
    """Keep cost-basis only for assets still held (sold positions drop off)."""
    held = {h["symbol"].upper() for h in holdings}
    return {
        key: value
        for key, value in entry_prices.items()
        if key.upper() in held
    }


def _pnl_fields(
    amount: float,
    entry_price: float | None,
    current_price: float | None,
    is_quote: bool,
) -> tuple[float | None, float | None, float | None]:
    """Return (pnl_pct, pnl_usd, cost_basis_usd) for a holding."""
    if is_quote or not entry_price or not current_price or amount <= 0:
        return None, None, None
    cost_basis = entry_price * amount
    value_usd = current_price * amount
    pnl_usd = value_usd - cost_basis
    pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price else None
    return pnl_pct, pnl_usd, cost_basis


def _apply_price_to_holding(
    h: dict[str, Any],
    price: float,
    updated_at: str,
    source: str = "twak",
) -> None:
    h["current_price"] = price
    h["price_source"] = source
    h["value_usd"] = round(h["amount"] * price, 4)
    pnl_pct, pnl_usd, cost_basis = _pnl_fields(
        h["amount"],
        h.get("entry_price"),
        price,
        h.get("is_quote", False),
    )
    h["pnl_pct"] = pnl_pct
    h["pnl_usd"] = round(pnl_usd, 4) if pnl_usd is not None else None
    h["cost_basis_usd"] = cost_basis
    h["price_updated_at"] = updated_at


def _stable_price(symbol: str) -> float | None:
    return STABLE_USD.get(symbol.upper())


def merge_holdings(
    portfolio: PortfolioSnapshot | None,
    entry_prices: dict[str, dict],
) -> list[dict[str, Any]]:
    """Combine TWAK/DB balances with entry prices from trade history."""
    if not portfolio:
        return []

    holdings: list[dict[str, Any]] = []
    for pos in portfolio.positions:
        if pos.amount <= 0:
            continue

        symbol = pos.symbol
        sym_upper = symbol.upper()
        is_quote = is_gas_or_stable(symbol)

        current_price = pos.current_price
        stable = _stable_price(sym_upper)
        if stable is not None:
            current_price = stable
        elif (not current_price or current_price <= 0) and pos.amount > 0:
            # Portfolio TWAK json often has usd_value on positions
            pass

        value_usd = pos.amount * (current_price or 0.0)
        if is_negligible_position(pos.amount, value_usd=value_usd, price=current_price):
            continue

        entry = entry_prices.get(symbol) or entry_prices.get(sym_upper)
        entry_price = entry.get("entry_price") if entry else None
        pnl_pct, pnl_usd, cost_basis = _pnl_fields(
            pos.amount, entry_price, current_price, is_quote
        )

        holdings.append(
            {
                "symbol": symbol,
                "amount": pos.amount,
                "value_usd": value_usd,
                "current_price": current_price,
                "entry_price": entry_price,
                "cost_basis_usd": cost_basis,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "is_quote": is_quote,
                "live": True,
            }
        )

    holdings.sort(key=lambda h: h["value_usd"], reverse=True)
    return holdings


def summarize_holdings(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate live portfolio and unrealized PnL from holdings rows."""
    total_value = sum(h.get("value_usd") or 0 for h in holdings)
    trading = [h for h in holdings if not h.get("is_quote")]
    unrealized_pnl = sum(
        h["pnl_usd"] for h in trading if h.get("pnl_usd") is not None
    )
    cost_basis = sum(
        h["cost_basis_usd"] for h in trading if h.get("cost_basis_usd") is not None
    )
    trading_value = sum(h.get("value_usd") or 0 for h in trading)
    return {
        "total_value_usd": round(total_value, 2),
        "trading_value_usd": round(trading_value, 2),
        "unrealized_pnl_usd": round(unrealized_pnl, 2),
        "cost_basis_usd": round(cost_basis, 2),
        "positions_with_pnl": sum(1 for h in trading if h.get("pnl_usd") is not None),
    }


async def refresh_live_prices(
    resolver: PriceResolver,
    holdings: list[dict[str, Any]],
    token_map: dict[str, TokenConfig],
) -> list[dict[str, Any]]:
    """Refresh prices via CMC-first resolver (stables fixed at $1)."""
    updated_at = datetime.now(timezone.utc).isoformat()
    to_resolve: list[TokenConfig] = []
    row_by_sym: dict[str, dict[str, Any]] = {}

    for h in holdings:
        sym = h["symbol"].upper()
        stable = _stable_price(sym)
        if stable is not None:
            _apply_price_to_holding(h, stable, updated_at, "stable")
            continue

        existing = h.get("current_price") or 0
        if sym == "BNB" and existing > 0:
            _apply_price_to_holding(h, existing, updated_at, "portfolio")
            continue

        if h.get("is_quote"):
            if existing > 0:
                _apply_price_to_holding(h, existing, updated_at, "portfolio")
            continue

        token = token_map.get(sym)
        if token:
            to_resolve.append(token)
            row_by_sym[sym] = h

    if to_resolve:
        prices = await resolver.usd_prices_batch(to_resolve)
        for sym, row in row_by_sym.items():
            entry = prices.get(sym)
            if entry and entry[0] > 0:
                _apply_price_to_holding(row, entry[0], updated_at, entry[1])
            elif row.get("current_price") and row["current_price"] > 0:
                _apply_price_to_holding(row, row["current_price"], updated_at, "portfolio")

    holdings.sort(key=lambda x: x["value_usd"], reverse=True)
    return holdings


def build_price_resolver(env: Any, twak: TWAKProvider) -> PriceResolver:
    """Construct CMC-first price resolver from env settings."""
    from genesis.core.config import EnvSettings
    from genesis.data.cmc_provider import CMCProvider

    if not isinstance(env, EnvSettings):
        from genesis.core.config import get_env_settings
        env = get_env_settings()

    cmc = CMCProvider(
        mcp_url=env.cmc_mcp_url,
        api_key=env.cmc_api_key,
        x402_enabled=env.cmc_x402_enabled,
        twak=twak,
        x402_mode=env.cmc_x402_mode,
        x402_max_payment=env.cmc_x402_max_payment,
        x402_prefer_network=env.cmc_x402_prefer_network,
    )
    return PriceResolver(twak, cmc)


async def fetch_live_holdings(
    twak: TWAKProvider,
    portfolio: PortfolioSnapshot,
    entry_prices: dict[str, dict],
    allowed_tokens: list[TokenConfig],
    resolver: PriceResolver | None = None,
) -> dict[str, Any]:
    """Portfolio holdings with refreshed prices and PnL summary."""
    if resolver is None:
        from genesis.core.config import get_env_settings
        resolver = build_price_resolver(get_env_settings(), twak)

    token_map = {t.symbol.upper(): t for t in allowed_tokens}
    holdings = merge_holdings(portfolio, entry_prices)
    holdings = await refresh_live_prices(resolver, holdings, token_map)
    holdings = [h for h in holdings if not is_negligible_position(
        h["amount"], value_usd=h.get("value_usd"), price=h.get("current_price")
    )]
    summary = summarize_holdings(holdings)
    summary["updated_at"] = updated_at if (updated_at := next(
        (h.get("price_updated_at") for h in holdings if h.get("price_updated_at")), None
    )) else None
    summary["available_usd"] = round(portfolio.available_usd, 2)
    summary["source"] = "live"
    return {"holdings": holdings, "summary": summary, "portfolio": portfolio}


async def fetch_wallet_portfolio(
    twak: TWAKProvider,
    rules: Any,
    *,
    traded_symbols: list[str] | None = None,
    resolver: PriceResolver | None = None,
) -> tuple[PortfolioSnapshot, PriceResolver]:
    """Live TWAK portfolio with on-chain supplement for recently traded tokens."""
    from genesis.core.config import get_env_settings

    env = get_env_settings()
    if resolver is None:
        resolver = build_price_resolver(env, twak)

    supplement = twak.resolve_supplement_tokens(
        traded_symbols or [],
        rules.allowed_tokens,
    )
    portfolio = await twak.get_portfolio(
        rules.execution.default_quote,
        supplement_tokens=supplement,
        price_resolver=resolver,
    )
    return portfolio, resolver


def _format_time_short(ts: str | None) -> str:
    if not ts:
        return "—"
    return str(ts)[:16].replace("T", " ")


def enrich_audit_row(audit: dict) -> dict:
    """Add display helpers for decision feed rows."""
    row = dict(audit)
    decision = audit.get("decision") or {}
    action = str(decision.get("action") or "hold").lower()
    reason = str(decision.get("reason") or "")
    row["action"] = action
    row["asset"] = decision.get("asset") or "—"
    row["confidence"] = decision.get("confidence")
    row["reason"] = reason
    row["reason_short"] = (reason[:96] + "…") if len(reason) > 96 else reason
    row["time_short"] = _format_time_short(audit.get("timestamp"))
    row["cycle_id"] = audit.get("cycle_id")
    return row


def enrich_trade_row(trade: dict) -> dict:
    """Add display helpers for trade tables."""
    row = dict(trade)
    symbol = trade.get("symbol", "")
    side = str(trade.get("side", "")).upper()
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        row["asset"] = quote if side == "BUY" else base
    else:
        row["asset"] = symbol
    if not row.get("price") and row.get("amount_token") and trade.get("amount_usd"):
        row["price"] = float(trade["amount_usd"]) / float(trade["amount_token"])
    tx_hash = trade.get("tx_hash")
    row["tx_hash"] = tx_hash
    row["bscscan_url"] = f"https://bscscan.com/tx/{tx_hash}" if tx_hash else None
    row["time_short"] = _format_time_short(trade.get("timestamp"))
    row["status"] = str(trade.get("status") or "").lower()
    row["side_lower"] = side.lower()
    return row


def wallet_context_from_env(env: Any) -> dict[str, Any]:
    """Instant wallet/ERC-8004 block from env — no TWAK calls."""
    from dashboard.wallet_info import INTEGRATION_LINKS
    from dashboard.wallet_store import wallet_display_context

    display = wallet_display_context(env)
    return {
        "twak_installed": None,
        "wallet_address": display["wallet_address"],
        "wallet_mode": env.twak_wallet_mode,
        "twak_network": env.twak_network,
        "agent_endpoint": env.genesis_agent_endpoint,
        "erc8004_agent_id": env.competition_agent_id or None,
        "erc8004_registered": bool(env.competition_agent_id),
        "erc8004_detail": None,
        "competition": None,
        "bscscan_wallet_url": display["bscscan_wallet_url"],
        "links": dict(INTEGRATION_LINKS),
        "deferred": not bool(display["wallet_address"]),
    }