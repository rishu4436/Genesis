"""FastAPI monitoring dashboard for Genesis agent."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from dashboard.agent_logic import build_logic_view, fetch_cycle_feed, fetch_latest_logic
from dashboard.agent_runner import get_agent_runner
from dashboard.wallet_info import INTEGRATION_LINKS, get_wallet_integration
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from genesis.core.config import demo_mode_active, get_env_settings, get_rules
from genesis.core.database import Database
from genesis.core.models import PortfolioSnapshot
from genesis.execution.twak_provider import TWAKProvider

from dashboard.holdings import (
    build_price_resolver,
    enrich_audit_row,
    enrich_trade_row,
    entry_prices_for_holdings,
    fetch_live_holdings,
    fetch_wallet_portfolio,
    merge_holdings,
    summarize_holdings,
    wallet_context_from_env,
)

from dashboard.price_cache import cache_clear, cache_get, cache_set

app = FastAPI(
    title="Genesis Dashboard",
    description="Monitoring dashboard for Genesis autonomous trading agent",
    version="0.1.0",
)

DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_LOGO_SVG_PATH = STATIC_DIR / "img" / "genesis-logo.svg"


def _load_logo_svg() -> str:
    return _LOGO_SVG_PATH.read_text(encoding="utf-8")


def _genesis_logo_svg(size: int = 32) -> str:
    """Animated Genesis logo for favicon (inline SVG)."""
    svg = _load_logo_svg()
    if 'width="' not in svg.split(">", 1)[0]:
        svg = svg.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
    else:
        svg = re.sub(r'width="[^"]*"', f'width="{size}"', svg, count=1)
        svg = re.sub(r'height="[^"]*"', f'height="{size}"', svg, count=1)
    return svg


def _genesis_logo_markup(size: int = 48, uid: str = "gen") -> Markup:
    """Render logo macro server-side for templates without duplicate IDs."""
    macro_fn = templates.env.from_string(
        "{% from 'macros/genesis_logo.html' import render %}{{ render(size, uid) }}"
    )
    return Markup(macro_fn.render(size=size, uid=uid))


templates.env.globals["genesis_logo"] = _genesis_logo_markup
templates.env.globals["asset_version"] = "20260621a"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

WALLET_API_TIMEOUT_SEC = 25.0
HOLDINGS_FETCH_TIMEOUT_SEC = 45.0
HOLDINGS_CACHE_TTL_SEC = 45.0
_holdings_fetch_lock = asyncio.Lock()


def _wallet_context_fast(env: Any) -> dict[str, Any]:
    """Wallet block for SSR — env/file address instantly; TWAK enriches in background."""
    cached = cache_get(f"wallet:{env.twak_network}:False")
    if cached is not None and cached.get("wallet_address"):
        return cached
    return wallet_context_from_env(env)


@app.on_event("startup")
async def _warm_dashboard_caches() -> None:
    """Prefetch wallet info so /app does not sit on 'Loading wallet…'."""

    async def _warm() -> None:
        with suppress(Exception):
            await asyncio.wait_for(
                get_wallet_integration(full=False),
                timeout=WALLET_API_TIMEOUT_SEC,
            )

    asyncio.create_task(_warm())


def _is_proxied_request(request: Request) -> bool:
    """Detect Cloudflare tunnel or reverse-proxy traffic (not direct localhost)."""
    if request.headers.get("cf-connecting-ip"):
        return True
    if request.headers.get("x-forwarded-for"):
        return True
    if request.headers.get("x-real-ip"):
        return True
    return False


def _is_local_request(request: Request | None) -> bool:
    if request is None or request.client is None:
        return False
    if _is_proxied_request(request):
        return False
    host = request.client.host or ""
    return host in {"127.0.0.1", "::1", "localhost"}


def _controls_enabled(request: Request | None = None) -> bool:
    """Env flag, or automatic allow when dashboard is opened on localhost."""
    if get_env_settings().dashboard_controls_enabled:
        return True
    return _is_local_request(request)


def _require_controls(request: Request) -> None:
    if not _controls_enabled(request):
        raise HTTPException(
            status_code=403,
            detail="Agent controls are disabled on this public endpoint. View-only mode.",
        )


def _get_db() -> Database:
    env = get_env_settings()
    return Database(env.genesis_db_path)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Genesis logo favicon."""
    return Response(content=_genesis_logo_svg(32), media_type="image/svg+xml")


def _landing_context() -> dict[str, Any]:
    """Shared context for the public landing page."""
    env = get_env_settings()
    rules = get_rules()
    endpoint = (env.genesis_agent_endpoint or "").rstrip("/")
    return {
        "agent_name": env.genesis_agent_name,
        "network": env.genesis_network,
        "strategy": rules.strategy.name,
        "agent_id": env.competition_agent_id,
        "agent_endpoint": endpoint,
        "llm_enabled": env.llm_enabled,
        "llm_provider": env.llm_provider,
        "llm_model": env.llm_model,
        "x402_enabled": env.cmc_x402_enabled,
        "x402_network": env.cmc_x402_prefer_network,
        "allowed_token_count": len(rules.allowed_tokens),
        "loop_interval": rules.loop.interval_seconds,
        "signal_change_pct": int(rules.loop.signal_change_threshold * 100),
        "risk": rules.risk.model_dump(),
        "perps_enabled": rules.perps.enabled,
        "perps_max_leverage": rules.perps.max_leverage,
        "public_view_only": not env.dashboard_controls_enabled,
        "hackathon_tracks": env.hackathon_tracks,
    }


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    """Public landing page — features, how it works, launch CTA."""
    return templates.TemplateResponse(
        request,
        "landing.html",
        _landing_context(),
    )


async def _dashboard_context() -> dict[str, Any]:
    """Build template context for the live dashboard."""
    env = get_env_settings()
    rules = get_rules()
    db = _get_db()
    await db.initialize()

    audits = await db.get_recent_audits(15)
    latest_audit = audits[0] if audits else None
    agent_logic = build_logic_view(latest_audit, rules, llm_enabled=env.llm_enabled)
    decisions = [enrich_audit_row(a) for a in audits[:8]]
    trades = [enrich_trade_row(t) for t in await db.get_recent_trades(15)]
    live = await _load_live_holdings(use_cache=True, persist=False)
    holdings = live["holdings"]
    pnl_summary = live["summary"]
    portfolio = live.get("portfolio")
    if portfolio is not None:
        portfolio = portfolio.model_dump()
    else:
        portfolio = await db.get_latest_portfolio()
    wallet = _wallet_context_fast(env)
    agent_status = get_agent_runner().status()

    return {
        "agent_name": env.genesis_agent_name,
        "network": env.genesis_network,
        "strategy": rules.strategy.name,
        "agent_id": env.competition_agent_id,
        "llm_enabled": env.llm_enabled,
        "x402_enabled": env.cmc_x402_enabled,
        "x402_network": env.cmc_x402_prefer_network,
        "perps_enabled": rules.perps.enabled,
        "perps_margin_pct": rules.perps.margin_stable_pct,
        "perps_max_leverage": rules.perps.max_leverage,
        "hackathon_tracks": env.hackathon_tracks,
        "loop_interval": rules.loop.interval_seconds,
        "portfolio": portfolio,
        "holdings": holdings,
        "pnl_summary": pnl_summary,
        "audits": audits,
        "decisions": decisions,
        "trades": trades,
        "risk": rules.risk.model_dump(),
        "wallet": wallet,
        "agent_status": agent_status,
        "agent_logic": agent_logic,
        "links": INTEGRATION_LINKS,
        "holdings_loading": False,
        "holdings_source": pnl_summary.get("source", "live"),
    }


@app.get("/app", response_class=HTMLResponse)
async def dashboard_app(request: Request) -> HTMLResponse:
    """Live monitoring dashboard."""
    ctx = await _dashboard_context()
    ctx["controls_enabled"] = _controls_enabled(request)
    ctx["active_track"] = 1
    return templates.TemplateResponse(
        request,
        "index.html",
        ctx,
    )


@app.get("/app/strategy", response_class=HTMLResponse)
async def strategy_generator_app(request: Request) -> HTMLResponse:
    """Track 2: Strategy Skill Generator UI."""
    env = get_env_settings()
    rules = get_rules()
    return templates.TemplateResponse(
        request,
        "track2.html",
        {
            "agent_name": env.genesis_agent_name,
            "network": env.genesis_network,
            "strategy": rules.strategy.name,
            "x402_enabled": env.cmc_x402_enabled,
            "hackathon_tracks": env.hackathon_tracks,
            "active_track": 2,
            "default_asset": "BNB",
            "allowed_token_count": len(rules.allowed_tokens),
        },
    )


@app.get("/api/status")
async def api_status(request: Request) -> dict[str, Any]:
    """Agent status endpoint."""
    env = get_env_settings()
    rules = get_rules()
    runner = get_agent_runner()
    return {
        "agent": env.genesis_agent_name,
        "version": "0.1.0",
        "network": env.genesis_network,
        "strategy": rules.strategy.name,
        "llm_provider": env.llm_provider,
        "agent_id": env.competition_agent_id,
        "controls_enabled": _controls_enabled(request),
        "demo_mode": demo_mode_active(env, rules),
        **runner.status(),
    }


@app.get("/erc8183/status")
async def erc8183_status() -> dict[str, Any]:
    """ERC-8183-compatible agent status (referenced in ERC-8004 agent URI)."""
    env = get_env_settings()
    rules = get_rules()
    runner = get_agent_runner()
    status = runner.status()
    return {
        "name": env.genesis_agent_name,
        "description": env.genesis_agent_description,
        "version": "0.1.0",
        "network": env.genesis_network,
        "strategy": rules.strategy.name,
        "agent_id": env.competition_agent_id,
        "endpoint": env.genesis_agent_endpoint,
        "status": status.get("state", "unknown"),
        "llm_enabled": env.llm_enabled,
        "x402_enabled": env.cmc_x402_enabled,
        "hackathon_tracks": env.hackathon_tracks,
        "capabilities": [
            "spot_trading",
            "perps_trading",
            "signal_fusion",
            "risk_management",
            "audit_trail",
            "strategy_skill",
        ],
        "links": {
            "dashboard": f"{env.genesis_agent_endpoint.rstrip('/')}/app",
            "api_status": f"{env.genesis_agent_endpoint.rstrip('/')}/api/status",
            "audit_export": f"{env.genesis_agent_endpoint.rstrip('/')}/api/audit/export",
            "strategy_skill": f"{env.genesis_agent_endpoint.rstrip('/')}/api/strategy-skill",
        },
    }


@app.get("/api/strategy-skill")
async def api_strategy_skill() -> dict[str, Any]:
    """Track 2: backtestable strategy spec derived from rules.yaml."""
    from genesis.strategy_skill.builder import build_strategy_spec

    rules = get_rules()
    return build_strategy_spec(rules)


@app.post("/api/strategy-skill/generate")
async def api_strategy_skill_generate(body: dict[str, Any]) -> dict[str, Any]:
    """Track 2: generate strategy JSON from user-supplied market conditions."""
    from genesis.strategy_skill.backtest import backtest_from_audits
    from genesis.strategy_skill.generator import generate_strategy
    from genesis.strategy_skill.models import GenerateStrategyResponse, StrategyConditions

    rules = get_rules()
    conditions = StrategyConditions.model_validate(body)
    strategy = generate_strategy(conditions, rules)

    backtest_preview = None
    if conditions.backtest_limit > 0:
        db = _get_db()
        await db.initialize()
        audits = await db.get_recent_audits(conditions.backtest_limit)
        backtest_preview = backtest_from_audits(
            audits, rules, idle_swap_cycles=conditions.idle_cycles
        )

    return GenerateStrategyResponse(
        strategy=strategy,
        conditions=conditions,
        backtest_preview=backtest_preview,
    ).model_dump()


@app.get("/api/strategy-skill/download")
async def api_strategy_skill_download(
    primary_asset: str = Query("BNB"),
    risk_profile: str = Query("conservative"),
    market_regime: str = Query("bullish"),
) -> FileResponse:
    """Download generated strategy spec as JSON file."""
    import tempfile

    from genesis.strategy_skill.builder import build_strategy_spec
    from genesis.strategy_skill.models import StrategyConditions

    rules = get_rules()
    conditions = StrategyConditions(
        primary_asset=primary_asset,
        risk_profile=risk_profile,  # type: ignore[arg-type]
        market_regime=market_regime,  # type: ignore[arg-type]
    )
    spec = build_strategy_spec(rules, conditions)

    tmp = Path(tempfile.gettempdir()) / f"genesis-strategy-{primary_asset.lower()}.json"
    tmp.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return FileResponse(
        path=str(tmp),
        filename=f"genesis-strategy-{primary_asset.lower()}.json",
        media_type="application/json",
    )


@app.get("/api/strategy-skill/backtest")
async def api_strategy_skill_backtest(
    limit: int = Query(50, ge=1, le=200),
    idle_cycles: int = Query(0, ge=0, le=100),
) -> dict[str, Any]:
    """Track 2: replay audit history through strategy entry/exit gates."""
    from genesis.strategy_skill.backtest import backtest_from_audits

    rules = get_rules()
    db = _get_db()
    await db.initialize()
    audits = await db.get_recent_audits(limit)
    return backtest_from_audits(audits, rules, idle_swap_cycles=idle_cycles)


@app.post("/api/agent/start")
async def api_agent_start(request: Request) -> dict[str, Any]:
    """Start the autonomous trading loop (dashboard-managed)."""
    _require_controls(request)
    return await get_agent_runner().start_loop()


@app.post("/api/agent/stop")
async def api_agent_stop(request: Request) -> dict[str, Any]:
    """Stop the autonomous trading loop."""
    _require_controls(request)
    return await get_agent_runner().stop_loop()


@app.post("/api/agent/cycle")
async def api_agent_cycle(request: Request) -> dict[str, Any]:
    """Run a single decision + trade cycle."""
    _require_controls(request)
    return await get_agent_runner().run_one_cycle()


@app.get("/api/cycle/feed")
async def api_cycle_feed() -> dict[str, Any]:
    """Live per-token signal feed during cycles; latest audit when idle."""
    env = get_env_settings()
    rules = get_rules()
    return await fetch_cycle_feed(rules, llm_enabled=env.llm_enabled)


@app.get("/api/wallet")
async def api_wallet(full: bool = True) -> dict[str, Any]:
    """TWAK wallet and ERC-8004 integration details."""
    try:
        return await asyncio.wait_for(
            get_wallet_integration(full=full),
            timeout=WALLET_API_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        env = get_env_settings()
        fallback = _wallet_context_fast(env)
        if not fallback.get("wallet_address"):
            fallback["wallet_error"] = "TWAK wallet lookup timed out — check WSL/TWAK"
            fallback["deferred"] = True
        else:
            fallback["wallet_error"] = "Competition status slow — address from config"
        return fallback


@app.get("/api/portfolio")
async def api_portfolio() -> dict[str, Any]:
    """Latest portfolio snapshot."""
    db = _get_db()
    await db.initialize()
    portfolio = await db.get_latest_portfolio()
    return portfolio or {"message": "No portfolio data yet"}


@app.get("/api/agent-logic")
async def api_agent_logic() -> dict[str, Any]:
    """Agent reasoning: fused signals, conviction, and latest decision."""
    env = get_env_settings()
    rules = get_rules()
    return await fetch_latest_logic(rules, llm_enabled=env.llm_enabled)


@app.get("/api/decisions")
async def api_decisions(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    """Recent decisions from audit trail."""
    db = _get_db()
    await db.initialize()
    audits = await db.get_recent_audits(limit)
    return [enrich_audit_row(a) for a in audits]


@app.get("/api/trades")
async def api_trades(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    """Recent trades."""
    db = _get_db()
    await db.initialize()
    trades = await db.get_recent_trades(limit)
    return [enrich_trade_row(t) for t in trades]


async def _holdings_db_fallback(
    db: Database,
    all_entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Last good portfolio snapshot when live TWAK fetch is empty or fails."""
    portfolio_row = await db.get_latest_portfolio()
    if not portfolio_row:
        return None
    snap = PortfolioSnapshot.model_validate(portfolio_row)
    if snap.total_value_usd <= 0 and not snap.positions:
        return None
    relevant_entries = entry_prices_for_holdings(
        all_entries,
        [{"symbol": p.symbol} for p in snap.positions],
    )
    holdings = merge_holdings(snap, relevant_entries)
    summary = summarize_holdings(holdings)
    summary["source"] = "db_fallback"
    summary["available_usd"] = round(snap.available_usd, 2)
    return {"holdings": holdings, "summary": summary, "portfolio": snap}


def _portfolio_is_empty(portfolio: PortfolioSnapshot) -> bool:
    return portfolio.total_value_usd <= 0 and not portfolio.positions


async def _load_live_holdings(
    *,
    use_cache: bool = True,
    persist: bool = False,
) -> dict[str, Any]:
    """Fetch portfolio with live TWAK + on-chain balances (optional DB sync)."""
    if use_cache:
        cached = cache_get("holdings:live")
        if cached is not None:
            summary = cached.get("summary") or {}
            if summary.get("total_value_usd", 0) > 0:
                return cached

    async with _holdings_fetch_lock:
        if use_cache:
            cached = cache_get("holdings:live")
            if cached is not None:
                summary = cached.get("summary") or {}
                if summary.get("total_value_usd", 0) > 0:
                    return cached

        return await _fetch_live_holdings_uncached(persist=persist)


async def _fetch_live_holdings_uncached(*, persist: bool = False) -> dict[str, Any]:
    """TWAK portfolio fetch — serialized so parallel polls do not stampede WSL."""
    env = get_env_settings()
    rules = get_rules()
    db = _get_db()
    await db.initialize()
    all_entries = await db.get_entry_prices()
    resolver = None
    try:
        twak = TWAKProvider.from_env(env)
        traded = await db.get_traded_asset_symbols()
        portfolio, resolver = await asyncio.wait_for(
            fetch_wallet_portfolio(twak, rules, traded_symbols=traded),
            timeout=HOLDINGS_FETCH_TIMEOUT_SEC,
        )
        if _portfolio_is_empty(portfolio):
            logger.warning("TWAK returned empty portfolio — trying DB fallback")
            fallback = await _holdings_db_fallback(db, all_entries)
            if fallback:
                return fallback

        relevant_entries = entry_prices_for_holdings(
            all_entries,
            [{"symbol": p.symbol} for p in portfolio.positions],
        )
        try:
            result = await asyncio.wait_for(
                fetch_live_holdings(
                    twak,
                    portfolio,
                    relevant_entries,
                    rules.allowed_tokens,
                    resolver=resolver,
                ),
                timeout=HOLDINGS_FETCH_TIMEOUT_SEC,
            )
        except Exception as price_exc:
            logger.warning("Live price refresh failed, using TWAK portfolio prices: %s", price_exc)
            holdings = merge_holdings(portfolio, relevant_entries)
            summary = summarize_holdings(holdings)
            summary["source"] = "live_twak"
            summary["available_usd"] = round(portfolio.available_usd, 2)
            result = {"holdings": holdings, "summary": summary, "portfolio": portfolio}
        if result["summary"].get("total_value_usd", 0) <= 0:
            fallback = await _holdings_db_fallback(db, all_entries)
            if fallback:
                return fallback

        if persist and not _portfolio_is_empty(portfolio):
            await db.save_portfolio_snapshot(portfolio)
            cache_clear("holdings:live")
        if result["summary"].get("total_value_usd", 0) > 0:
            cache_set("holdings:live", result, HOLDINGS_CACHE_TTL_SEC)
        return result
    except asyncio.TimeoutError:
        logger.warning(
            "Live holdings fetch timed out after %.0fs (TWAK/WSL slow) — using DB fallback",
            HOLDINGS_FETCH_TIMEOUT_SEC,
        )
        fallback = await _holdings_db_fallback(db, all_entries)
        if fallback:
            return fallback
        return {
            "holdings": [],
            "summary": {"source": "unavailable", "total_value_usd": 0.0},
            "portfolio": None,
        }
    except Exception as exc:
        logger.warning("Live holdings fetch failed, using DB fallback: %s", exc)
        fallback = await _holdings_db_fallback(db, all_entries)
        if fallback:
            return fallback
        return {
            "holdings": [],
            "summary": {"source": "unavailable", "total_value_usd": 0.0},
            "portfolio": None,
        }
    finally:
        if resolver is not None:
            await resolver.cmc.close()


@app.get("/api/holdings")
async def api_holdings() -> list[dict]:
    """Live wallet balances with entry prices from trade history."""
    data = await _load_live_holdings()
    return data["holdings"]


@app.get("/api/holdings/live")
async def api_holdings_live() -> dict[str, Any]:
    """Live prices, values, and unrealized PnL — poll from dashboard JS."""
    data = await _load_live_holdings()
    port = data.get("portfolio")
    if isinstance(port, PortfolioSnapshot):
        data = {**data, "portfolio": port.model_dump()}
    return data


@app.get("/api/rules")
async def api_rules() -> dict[str, Any]:
    """Current strategy rules."""
    rules = get_rules()
    return rules.model_dump()


@app.get("/api/audit/export")
async def api_audit_export() -> JSONResponse:
    """Export full audit trail."""
    db = _get_db()
    await db.initialize()
    output = Path("data/audit_export.json")
    count = await db.export_audit_trail(str(output))
    with open(output, encoding="utf-8") as f:
        data = json.load(f)
    return JSONResponse(content={"count": count, "records": data})