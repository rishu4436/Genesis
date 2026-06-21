"""Genesis CLI — Typer app with rich output."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from genesis import __app_name__, __version__
from genesis.core.agent import GenesisAgent
from genesis.core.config import (
    EnvSettings,
    PROJECT_ROOT,
    clear_config_cache,
    get_env_settings,
    get_rules,
    load_rules,
    update_env_file,
)
from genesis.core.database import Database
from genesis.core.logger import setup_logger
from genesis.execution.liquidate import sell_all_to_usdt
from genesis.execution.perps_executor import PerpsExecutor
from genesis.execution.twak_provider import TWAKProvider
from genesis.core.models import Action
from genesis.onchain.bnb_identity import BNBIdentityManager
from genesis.onchain.competition import CompetitionRegistrar

app = typer.Typer(
    name="genesis",
    help="Genesis — Self-Custody Autonomous AI Trading Agent for BSC",
    add_completion=False,
)
skill_app = typer.Typer(help="Track 2: CMC Strategy Skill export and backtest")
app.add_typer(skill_app, name="strategy-skill")
console = Console()


def _run_async(coro):
    """Run async coroutine from sync CLI."""
    return asyncio.run(coro)


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Genesis autonomous trading agent CLI."""
    setup_logger(level="DEBUG" if verbose else "INFO")


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
) -> None:
    """Initialize Genesis project (create data dirs, validate config)."""
    from genesis.core.config import PROJECT_ROOT

    console.print(Panel(f"[bold green]{__app_name__} v{__version__}[/] — Initialization"))

    dirs = ["data", "data/logs", "config"]
    for d in dirs:
        path = PROJECT_ROOT / d
        path.mkdir(parents=True, exist_ok=True)
        console.print(f"  [green]✓[/] {d}/")

    env_example = PROJECT_ROOT / ".env.example"
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists() and env_example.exists():
        import shutil

        shutil.copy(env_example, env_file)
        console.print("  [green]✓[/] Created .env from .env.example")
    elif env_file.exists():
        console.print("  [yellow]![/] .env already exists")

    try:
        rules = load_rules()
        console.print(f"  [green]✓[/] rules.yaml loaded ({rules.strategy.name})")
    except Exception as e:
        console.print(f"  [red]✗[/] rules.yaml error: {e}")

    console.print("\n[bold]Next steps:[/]")
    console.print("  1. Edit .env with your API keys")
    console.print("  2. genesis setup-wallet")
    console.print("  3. genesis register-competition")
    console.print("  4. genesis run  (live mainnet — ensure wallet funded)")


@app.command("setup-wallet")
def setup_wallet() -> None:
    """Set up TWAK autonomous agent wallet (local signing)."""
    env = get_env_settings()
    twak = TWAKProvider.from_env(env)

    async def _setup():
        if not await twak.verify_installation():
            console.print("[red]TWAK CLI not found.[/] Install first:")
            console.print(
                "  curl -fsSL https://raw.githubusercontent.com/trustwallet/"
                "tw-agent-skills/main/install.sh | bash"
            )
            raise typer.Exit(1)

        result = await twak.setup_autonomous_wallet()
        address = await twak.get_wallet_address()
        console.print(Panel(
            f"[green]Autonomous wallet ready[/]\n"
            f"Address: [bold]{address}[/]\n"
            f"Mode: {env.twak_wallet_mode}\n"
            f"Network: {env.twak_network}",
            title="TWAK Wallet",
        ))
        return result

    _run_async(_setup())


@skill_app.command("generate")
def strategy_skill_generate(
    asset: str = typer.Option("BNB", "--asset", "-a", help="Primary asset symbol"),
    timeframe: str = typer.Option("5m", "--timeframe", "-t"),
    risk_profile: str = typer.Option("conservative", "--risk", "-r"),
    regime: str = typer.Option("bullish", "--regime"),
    take_profit: float = typer.Option(12.0, "--tp", help="Take profit %"),
    stop_loss: float = typer.Option(6.0, "--sl", help="Stop loss %"),
    backtest: bool = typer.Option(True, "--backtest/--no-backtest"),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """Generate Track 2 strategy JSON from market conditions."""
    from genesis.strategy_skill.generator import generate_strategy
    from genesis.strategy_skill.models import StrategyConditions

    rules = get_rules()
    conditions = StrategyConditions(
        primary_asset=asset,
        timeframe=timeframe,
        risk_profile=risk_profile,  # type: ignore[arg-type]
        market_regime=regime,  # type: ignore[arg-type]
        take_profit_pct=take_profit,
        stop_loss_pct=stop_loss,
        backtest_limit=limit if backtest else 0,
    )
    spec = generate_strategy(conditions, rules)
    console.print_json(data=spec)

    if backtest:
        from genesis.strategy_skill.backtest import backtest_from_audits

        async def _run():
            env = get_env_settings()
            db = Database(env.genesis_db_path)
            await db.initialize()
            return await db.get_recent_audits(limit)

        audits = _run_async(_run())
        result = backtest_from_audits(audits, rules)
        console.print(
            Panel(
                f"Backtest: {result['buy_signals']} buy / {result['sell_signals']} sell / "
                f"{result['hold_signals']} hold ({result['signals_evaluated']} signals)",
                title="Audit Replay",
                style="cyan",
            )
        )


@skill_app.command("export")
def strategy_skill_export() -> None:
    """Export Track 2 SKILL.md + strategy_spec.json from rules.yaml."""
    from genesis.strategy_skill.builder import export_strategy_skill

    rules = get_rules()
    paths = export_strategy_skill(rules)
    console.print(Panel("[bold]Track 2 Strategy Skill exported[/]", style="green"))
    console.print(f"  SKILL.md:  [cyan]{paths['skill_md']}[/]")
    console.print(f"  Spec JSON: [cyan]{paths['spec_json']}[/]")
    console.print("\nSubmit the [bold]skills/genesis-momentum-sentiment[/] folder for Track 2.")


@skill_app.command("backtest")
def strategy_skill_backtest(
    limit: int = typer.Option(50, "--limit", "-n", help="Recent audits to replay"),
    idle_cycles: int = typer.Option(0, "--idle", help="Simulate adaptive aggression after N idle swaps"),
) -> None:
    """Backtest strategy gates against stored audit composites."""
    from genesis.strategy_skill.backtest import backtest_from_audits

    rules = get_rules()

    async def _run():
        env = get_env_settings()
        db = Database(env.genesis_db_path)
        await db.initialize()
        return await db.get_recent_audits(limit)

    audits = _run_async(_run())
    result = backtest_from_audits(audits, rules, idle_swap_cycles=idle_cycles)

    table = Table(title="Strategy Skill Backtest (audit replay)")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key in (
        "audits_processed",
        "signals_evaluated",
        "buy_signals",
        "sell_signals",
        "hold_signals",
        "buy_pct",
        "sell_pct",
        "hold_pct",
    ):
        table.add_row(key, str(result.get(key, "—")))
    console.print(table)
    if result.get("top_symbols"):
        console.print("\n[bold]Most active symbols:[/]")
        for row in result["top_symbols"][:5]:
            console.print(
                f"  {row['symbol']}: buy={row['buy']} sell={row['sell']} hold={row['hold']}"
            )


@app.command("register-competition")
def register_competition(
    skip_onchain: bool = typer.Option(False, "--skip-onchain", help="Skip ERC-8004 registration"),
) -> None:
    """Register Genesis for hackathon (ERC-8004 + competition)."""
    env = get_env_settings()

    async def _register():
        agent_id = env.competition_agent_id

        twak = TWAKProvider.from_env(env)

        if not skip_onchain:
            identity = BNBIdentityManager(env, twak=twak)
            try:
                result = await identity.register_agent()
                agent_id = str(
                    result.get("agentId")
                    or result.get("agent_id")
                    or agent_id
                )
                identity.save_agent_id_to_env(agent_id)
                wallet = await twak.get_wallet_address()
                console.print(
                    f"[green]✓[/] ERC-8004 registered via TWAK: agentId={agent_id}\n"
                    f"    Wallet: {wallet}"
                )
            except Exception as e:
                console.print(f"[yellow]![/] ERC-8004 registration failed: {e}")
                if not agent_id:
                    console.print("[red]No agent ID available. Set COMPETITION_AGENT_ID in .env[/]")
                    raise typer.Exit(1)

        registrar = CompetitionRegistrar(env, twak)

        try:
            result = await registrar.register_via_twak(agent_id)
            console.print(f"[green]✓[/] Competition registration: {result}")
        except Exception as e:
            console.print(f"[yellow]![/] TWAK compete register failed: {e}")
            output = Path("data/registration.json")
            registrar.export_registration(agent_id, str(output))
            console.print(f"[blue]→[/] Registration payload exported to {output}")

    _run_async(_register())


@app.command()
def run(
    simulate: bool = typer.Option(False, "--simulate", "-s", help="Simulated execution (no on-chain txs)"),
    cycles: Optional[int] = typer.Option(None, "--cycles", "-c", help="Max cycles (default: infinite)"),
    interval: Optional[int] = typer.Option(None, "--interval", "-i", help="Override loop interval (seconds)"),
) -> None:
    """Start the autonomous trading agent loop."""
    env = get_env_settings()
    rules = get_rules()

    if interval:
        rules.loop.interval_seconds = interval

    console.print(Panel(
        f"[bold]{__app_name__}[/] Autonomous Agent\n"
        f"Network: {env.genesis_network}\n"
        f"Strategy: {rules.strategy.name}\n"
        f"Interval: {rules.loop.interval_seconds}s\n"
        f"Mode: {'SIMULATE' if simulate else 'LIVE'}",
        title="Starting Agent",
    ))

    agent = GenesisAgent(env, rules, simulate=simulate)

    async def _run():
        await agent.initialize()
        try:
            await agent.run_loop(max_cycles=cycles)
        except KeyboardInterrupt:
            agent.stop()
        finally:
            await agent.shutdown()

    _run_async(_run())


@app.command()
def status() -> None:
    """Show agent status and configuration."""
    env = get_env_settings()
    rules = get_rules()

    table = Table(title="Genesis Status")
    table.add_column("Setting", style="cyan")
    table.add_column("Value")

    table.add_row("Version", __version__)
    table.add_row("Network", env.genesis_network)
    table.add_row("Strategy", rules.strategy.name)
    llm_status = (
        f"{env.llm_provider} ({env.llm_model})"
        if env.llm_enabled
        else "disabled — rule-based signals"
    )
    table.add_row("LLM", llm_status)
    table.add_row("Loop Interval", f"{rules.loop.interval_seconds}s")
    table.add_row("Max Drawdown", f"{rules.risk.max_drawdown_pct}%")
    table.add_row("Risk/Trade", f"{rules.risk.max_portfolio_risk_per_trade_pct}%")
    table.add_row("Allowed Tokens", ", ".join(t.symbol for t in rules.allowed_tokens))
    table.add_row("TWAK Mode", env.twak_wallet_mode)
    table.add_row("BNB SDK Wallet", "TWAK (TWAKWalletProvider)")
    table.add_row("CMC MCP", env.cmc_mcp_url)
    table.add_row(
        "CMC x402",
        f"{'enabled' if env.cmc_x402_enabled else 'disabled'} ({env.cmc_x402_mode})",
    )
    table.add_row("Agent ID", env.competition_agent_id or "Not registered")
    twak_pw = env.twak_wallet_password or env.wallet_password
    perps_exec = PerpsExecutor(
        TWAKProvider.from_env(env),
        rules,
        env.twak_network,
        wallet_password=twak_pw,
    )
    perps_eligible = sorted(
        s for s in perps_exec.eligible_perps_symbols() if perps_exec.supports_symbol(s)
    )
    table.add_row(
        "Spot size",
        f"${rules.risk.min_swap_usd:.0f}–{rules.risk.spot_stable_pct:.0f}% of USDT/USDC",
    )
    table.add_row(
        "Perps",
        f"{'enabled' if rules.perps.enabled else 'disabled'} · "
        f"{rules.perps.margin_stable_pct:.0f}% margin × {rules.perps.max_leverage}x max",
    )
    if rules.perps.enabled:
        table.add_row("Perps symbols", ", ".join(perps_eligible) or "none")

    console.print(table)

    async def _compete():
        twak = TWAKProvider.from_env(env)
        if await twak.verify_installation():
            try:
                comp = await twak.compete_status()
                if comp.get("registered"):
                    console.print(
                        f"[green]Compete:[/] registered · open={comp.get('open')} · "
                        f"deadline={str(comp.get('deadline', ''))[:10]}"
                    )
                else:
                    console.print("[yellow]Compete:[/] not registered — run register-competition")
            except Exception as e:
                console.print(f"[yellow]Compete status:[/] {e}")

    _run_async(_compete())


@app.command()
def portfolio() -> None:
    """Show current portfolio from TWAK."""
    env = get_env_settings()
    rules = get_rules()
    twak = TWAKProvider.from_env(env)
    db = Database(env.genesis_db_path)

    async def _portfolio():
        await db.initialize()
        try:
            traded = await db.get_traded_asset_symbols()
            supplement = twak.resolve_supplement_tokens(traded, rules.allowed_tokens)
            snap = await twak.get_portfolio(
                rules.execution.default_quote,
                supplement_tokens=supplement,
            )
        except Exception as e:
            console.print(f"[red]Portfolio fetch failed: {e}[/]")
            raise typer.Exit(1)

        table = Table(title="Portfolio")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")

        table.add_row("Total Value", f"${snap.total_value_usd:,.2f}")
        table.add_row("Available", f"${snap.available_usd:,.2f}")
        table.add_row("Daily PnL", f"${snap.daily_pnl_usd:+,.2f}")
        table.add_row("Cumulative PnL", f"${snap.cumulative_pnl_usd:+,.2f}")
        table.add_row("Drawdown", f"{snap.drawdown_pct:.2f}%")
        console.print(table)

        if snap.positions:
            pos_table = Table(title="Positions")
            pos_table.add_column("Symbol")
            pos_table.add_column("Amount", justify="right")
            pos_table.add_column("Entry", justify="right")
            pos_table.add_column("PnL", justify="right")
            for p in snap.positions:
                pos_table.add_row(
                    p.symbol,
                    f"{p.amount:.4f}",
                    f"${p.entry_price:.2f}",
                    f"${p.unrealized_pnl_usd:+,.2f}",
                )
            console.print(pos_table)

    _run_async(_portfolio())


@app.command("perps-open")
def perps_open(
    symbol: str = typer.Argument(
        ...,
        help="Perps symbol (BNB, ETH, BTCB, UNI, ASTER, XRP, TRX) — must be in allowlist",
    ),
    size: float = typer.Option(250.0, "--size", "-s", help="Position notional in USD (min $200)"),
    leverage: int = typer.Option(2, "--leverage", "-l", help="Leverage multiplier"),
    side: str = typer.Option("long", "--side", help="long or short"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build params without submitting tx"),
) -> None:
    """Open a PancakeSwap Perps position (ApolloX Diamond on BSC)."""
    env = get_env_settings()
    rules = get_rules()
    twak = TWAKProvider.from_env(env)
    executor = PerpsExecutor(
        twak,
        rules,
        env.twak_network,
        wallet_password=env.twak_wallet_password or env.wallet_password,
    )

    action = Action.BUY if side.lower() in ("long", "buy") else Action.SELL

    async def _open():
        if not executor.is_enabled:
            console.print("[red]Perps disabled in rules.yaml[/] — set perps.enabled: true")
            raise typer.Exit(1)
        trade = await executor.open_position(
            symbol.upper(),
            action,
            size,
            leverage=leverage,
            dry_run=dry_run,
        )
        console.print(Panel(
            f"Symbol: [bold]{trade.symbol}[/]\n"
            f"Side: {trade.side.value}\n"
            f"Notional: ${trade.amount_usd:.2f}\n"
            f"Leverage: {trade.leverage}x\n"
            f"Price: ${trade.price:.4f}\n"
            f"Status: {trade.status.value}\n"
            f"TX: {trade.tx_hash or '—'}\n"
            f"Note: {trade.error or 'submitted — keeper settlement is async'}",
            title="PancakeSwap Perps",
        ))

    _run_async(_open())


@app.command("perps-close")
def perps_close(
    trade_hash: str = typer.Argument(..., help="bytes32 tradeHash from open tx logs"),
    symbol: str = typer.Option("", "--symbol", help="Optional symbol label for audit"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without submitting tx"),
) -> None:
    """Close a PancakeSwap Perps position by tradeHash."""
    env = get_env_settings()
    rules = get_rules()
    twak = TWAKProvider.from_env(env)
    executor = PerpsExecutor(
        twak,
        rules,
        env.twak_network,
        wallet_password=env.twak_wallet_password or env.wallet_password,
    )

    async def _close():
        trade = await executor.close_position(trade_hash, symbol=symbol, dry_run=dry_run)
        console.print(Panel(
            f"Position: {trade.position_id}\n"
            f"Status: {trade.status.value}\n"
            f"TX: {trade.tx_hash or '—'}",
            title="Perps Close",
        ))

    _run_async(_close())


@app.command("sell-all")
def sell_all(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview sells without executing"),
    min_usd: float = typer.Option(
        0.0,
        "--min-usd",
        help="Skip positions worth less than this (USD); default 0 sells all dust",
    ),
    dust_threshold: float = typer.Option(
        0.01,
        "--dust-threshold",
        help="Treat balances below this USD value as unsellable DEX dust (default $0.01)",
    ),
    slippage: Optional[int] = typer.Option(
        None,
        "--slippage",
        help="Slippage in basis points (default: rules.yaml max_slippage_bps)",
    ),
) -> None:
    """Sell all held tokens to USDT (keeps BNB for gas and USDT/stables)."""
    env = get_env_settings()
    rules = get_rules()
    twak = TWAKProvider.from_env(env)
    db = Database(env.genesis_db_path)

    async def _sell_all():
        await db.initialize()

        if not await twak.verify_installation():
            console.print("[red]TWAK CLI not found.[/] Run genesis setup-wallet first.")
            raise typer.Exit(1)

        targets, trades = await sell_all_to_usdt(
            twak,
            rules,
            db=db,
            min_usd=min_usd,
            dust_threshold_usd=dust_threshold,
            slippage_bps=slippage,
            dry_run=True,
        )

        if not targets:
            console.print("[yellow]No sellable tokens found (only BNB/USDT/stables or dust).[/]")
            raise typer.Exit(0)

        table = Table(title="Sell All → USDT" + (" (dry run)" if dry_run else ""))
        table.add_column("Symbol", style="cyan")
        table.add_column("Amount", justify="right")
        table.add_column("Est. USD", justify="right")
        for target in targets:
            table.add_row(
                target.symbol,
                f"{target.amount:.6g}",
                f"${target.value_usd:.2f}",
            )
        console.print(table)
        console.print(
            f"\n[dim]Keeps: BNB (gas) + USDT/stables. "
            f"Slippage: {(slippage or rules.risk.max_slippage_bps) / 100:.2f}%[/]"
        )

        if dry_run:
            console.print("\n[yellow]Dry run — no swaps executed.[/] Remove --dry-run to sell.")
            return

        if not yes:
            confirm = typer.confirm(
                f"Sell {len(targets)} token(s) to USDT? This cannot be undone.",
                default=False,
            )
            if not confirm:
                console.print("[yellow]Cancelled.[/]")
                raise typer.Exit(0)

        console.print("\n[bold]Executing sells...[/]")
        targets, trades = await sell_all_to_usdt(
            twak,
            rules,
            db=db,
            min_usd=min_usd,
            dust_threshold_usd=dust_threshold,
            slippage_bps=slippage,
            dry_run=False,
        )

        from genesis.core.models import TradeStatus

        result_table = Table(title="Results")
        result_table.add_column("Symbol")
        result_table.add_column("Status")
        result_table.add_column("Tx / Note")
        for trade in trades:
            status = trade.status.value
            detail = trade.tx_hash or trade.error or "—"
            if trade.tx_hash:
                color = "green"
            elif trade.status == TradeStatus.SKIPPED:
                color = "yellow"
            else:
                color = "red"
            result_table.add_row(
                trade.symbol,
                f"[{color}]{status}[/]",
                detail[:80] if detail else "—",
            )
        console.print(result_table)

        ok = sum(1 for t in trades if t.tx_hash)
        skipped = [t for t in trades if t.status == TradeStatus.SKIPPED]
        failed = [t for t in trades if t.status == TradeStatus.FAILED]
        console.print(f"\n[bold]{ok}/{len(trades)}[/] sells confirmed.")
        if skipped:
            console.print(
                f"[yellow]{len(skipped)} dust balance(s) skipped[/] "
                f"(below ${dust_threshold:.2f} DEX minimum — harmless, safe to ignore)"
            )
        if failed:
            console.print("[red]Some sells failed — see errors above.[/]")
            for trade in failed:
                sym = trade.symbol.split("/")[0]
                amt = trade.amount_token or 0
                console.print(f"  [dim]{sym}: {amt:.8g} remaining — {trade.error}[/]")

        if ok > 0 and not dry_run:
            try:
                from genesis.data.price_resolver import PriceResolver
                from genesis.data.cmc_provider import CMCProvider

                traded = await db.get_traded_asset_symbols()
                supplement = twak.resolve_supplement_tokens(traded, rules.allowed_tokens)
                cmc = CMCProvider(
                    mcp_url=env.cmc_mcp_url,
                    api_key=env.cmc_api_key,
                    x402_enabled=env.cmc_x402_enabled,
                    twak=twak,
                    x402_mode=env.cmc_x402_mode,
                    x402_max_payment=env.cmc_x402_max_payment,
                    x402_prefer_network=env.cmc_x402_prefer_network,
                )
                resolver = PriceResolver(twak, cmc)
                portfolio = await twak.get_portfolio(
                    rules.execution.default_quote,
                    supplement_tokens=supplement,
                    price_resolver=resolver,
                )
                await db.save_portfolio_snapshot(portfolio)
                await cmc.close()
                console.print("[dim]Portfolio snapshot updated after sell-all.[/]")
            except Exception as e:
                console.print(f"[yellow]Portfolio refresh skipped: {e}[/]")

    _run_async(_sell_all())


@app.command()
def backtest(
    cycles: int = typer.Option(10, "--cycles", "-c", help="Number of simulated cycles"),
) -> None:
    """Run backtest mode with simulated execution."""
    console.print(f"[bold]Running backtest ({cycles} cycles)...[/]")
    env = get_env_settings()
    rules = get_rules()
    agent = GenesisAgent(env, rules, simulate=True)

    async def _backtest():
        await agent.initialize()
        await agent.run_loop(max_cycles=cycles)
        await agent.shutdown()

        audits = await agent.db.get_recent_audits(cycles)
        trades = [a for a in audits if a.get("trade")]

        table = Table(title="Backtest Results")
        table.add_column("Cycle")
        table.add_column("Action")
        table.add_column("Asset")
        table.add_column("Confidence")
        table.add_column("Status")

        for audit in audits:
            d = audit.get("decision", {})
            t = audit.get("trade")
            table.add_row(
                audit.get("cycle_id", "?"),
                d.get("action", "—"),
                d.get("asset", "—"),
                f"{d.get('confidence', 0):.2f}",
                t.get("status", "—") if t else "—",
            )

        console.print(table)
        console.print(f"\nTotal cycles: {len(audits)}, Trades: {len(trades)}")

    _run_async(_backtest())


@app.command()
def logs(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent audit records"),
    export: Optional[str] = typer.Option(None, "--export", help="Export full audit trail to JSON"),
) -> None:
    """View recent decision logs and audit trail."""
    env = get_env_settings()
    from genesis.core.database import Database

    db = Database(env.genesis_db_path)

    async def _logs():
        await db.initialize()

        if export:
            count = await db.export_audit_trail(export)
            console.print(f"[green]Exported {count} audit records to {export}[/]")
            return

        audits = await db.get_recent_audits(limit)
        if not audits:
            console.print("[yellow]No audit records found. Run the agent first.[/]")
            return

        for audit in audits:
            d = audit.get("decision", {})
            action = d.get("action", "—")
            color = {"BUY": "green", "SELL": "red"}.get(action, "yellow")
            console.print(
                f"[dim]{audit.get('timestamp', '')}[/] "
                f"[{color}]{action}[/] {d.get('asset', '')} "
                f"(conf={d.get('confidence', 0):.2f}) — {d.get('reason', '')[:80]}"
            )

    _run_async(_logs())


@app.command("cmc-test")
def cmc_test(
    symbol: str = typer.Option("BNB", "--symbol", "-s", help="Token symbol to test"),
    cmc_id: int = typer.Option(1839, "--id", help="CoinMarketCap coin ID"),
    x402: bool = typer.Option(False, "--x402", help="Force x402 pay-per-call mode (no API key)"),
) -> None:
    """Test CMC Agent Hub MCP integration (all 12 tools)."""
    from genesis.data.cmc_provider import CMCProvider
    from genesis.data.signal_aggregator import SignalAggregator

    env = get_env_settings()
    rules = get_rules()

    use_x402 = x402 or env.cmc_x402_enabled
    api_key = "" if (x402 or (use_x402 and env.cmc_x402_mode == "only")) else env.cmc_api_key
    x402_mode = "only" if x402 else env.cmc_x402_mode

    async def _test():
        twak = TWAKProvider.from_env(env)
        provider = CMCProvider(
            mcp_url=env.cmc_mcp_url,
            api_key=api_key,
            x402_enabled=use_x402,
            twak=twak,
            x402_mode=x402_mode,
            x402_max_payment=env.cmc_x402_max_payment,
            x402_prefer_network=env.cmc_x402_prefer_network,
            aggregator=SignalAggregator(rules),
        )

        mode_label = "x402" if use_x402 and not api_key else "API key"
        if use_x402 and api_key:
            mode_label = f"API key + x402 ({x402_mode})"
        console.print(Panel(f"CMC MCP Test — {symbol} (id={cmc_id}) [{mode_label}]"))

        health = await provider.health_check()
        table = Table(title="MCP Health")
        table.add_column("Key", style="cyan")
        table.add_column("Value")
        table.add_row("URL", health.get("url", env.cmc_mcp_url))
        table.add_row("Mode", health.get("payment_mode", "api-key"))
        if health.get("payment_mode") == "x402":
            table.add_row("Auth", "x402 micropayment (no API key)")
            table.add_row("Payment ready", str(health.get("payment_ready", False)))
        else:
            table.add_row("Authenticated", str(health.get("authenticated", False)))
        table.add_row("Tools", str(health.get("tool_count", 0)))
        if health.get("max_payment_atomic"):
            table.add_row("Max payment", f"{health['max_payment_atomic']} atomic USDC")
        console.print(table)

        if health.get("tools"):
            console.print(f"Tools: {', '.join(health['tools'][:6])}...")

        signals = await provider.get_all_signals(symbol, cmc_id)
        sig_table = Table(title=f"Signals for {symbol}")
        sig_table.add_column("Category")
        sig_table.add_column("Value", justify="right")
        sig_table.add_column("Source")
        sig_table.add_column("Summary")

        for s in signals:
            sig_table.add_row(
                s.category.value,
                f"{s.value:+.2f}",
                s.source.split(":")[-1][:30],
                s.summary[:60],
            )
        console.print(sig_table)

        composite = await provider.get_composite_signal(symbol, cmc_id)
        console.print(
            Panel(
                f"Conviction: [bold]{composite.conviction:.2f}[/] ({composite.direction})\n"
                f"{composite.summary}",
                title="Composite Signal",
            )
        )

        narratives = await provider.get_narratives()
        console.print(f"Market narratives: {narratives.summary}")

        market_ctx = await provider.fetch_market_context()
        ctx_table = Table(title="Market Context (4 wired tools)")
        ctx_table.add_column("Tool")
        ctx_table.add_column("Value", justify="right")
        ctx_table.add_column("Summary")
        for signal in market_ctx.signals:
            tool = signal.source.split(":")[-1]
            ctx_table.add_row(tool, f"{signal.value:+.2f}", signal.summary[:70])
        console.print(ctx_table)
        if market_ctx.blocks_buys:
            console.print(f"[yellow]Macro buy block:[/] {market_ctx.block_reason}")
        else:
            console.print(
                f"[dim]Market TA conviction delta: {market_ctx.market_conviction_delta:+.3f}[/]"
            )

        info = await provider.get_crypto_info_signal(symbol, cmc_id)
        discovery = await provider.search_cryptos_signal(symbol, cmc_id)
        console.print(f"Token metadata: {info.summary}")
        console.print(f"Token discovery: {discovery.summary}")

        await provider.close()

    if not env.cmc_api_key and not use_x402:
        console.print("[red]Set CMC_API_KEY or CMC_X402_ENABLED=true in .env[/]")
        raise typer.Exit(1)

    if use_x402 and not api_key:
        console.print(
            "[yellow]x402 mode:[/] ~$0.01 USDC per tool call — fund TWAK wallet with USDC on Base (or BSC)"
        )

    _run_async(_test())


def _port_in_use(host: str, port: int) -> bool:
    """Return True if something is already serving on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        if probe.connect_ex((host, port)) == 0:
            return True

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return False
        except OSError:
            return True


def _listener_pid(port: int) -> int | None:
    """Best-effort PID for the process listening on port (Windows/Linux)."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line.upper():
                    parts = line.split()
                    if parts:
                        return int(parts[-1])
        except (ValueError, OSError):
            return None
        return None

    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = result.stdout.strip().split()
        return int(pids[0]) if pids else None
    except (ValueError, OSError):
        return None


def _cloudflared_path() -> Path:
    return PROJECT_ROOT / "bin" / "cloudflared.exe"


def _ensure_cloudflared() -> Path:
    """Download cloudflared for Windows if missing."""
    exe = _cloudflared_path()
    if exe.exists():
        return exe

    import urllib.request

    exe.parent.mkdir(parents=True, exist_ok=True)
    url = (
        "https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-windows-amd64.exe"
    )
    console.print(f"[blue]→[/] Downloading cloudflared to {exe} …")
    urllib.request.urlretrieve(url, exe)
    return exe


def _parse_tunnel_url(output: str) -> str | None:
    import re

    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", output, re.IGNORECASE)
    return match.group(0) if match else None


def _kill_cloudflared() -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/f", "/im", "cloudflared.exe"], capture_output=True, check=False)
    else:
        subprocess.run(["pkill", "-f", "cloudflared"], capture_output=True, check=False)


def _cloudflared_pid() -> int | None:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/fi", "imagename eq cloudflared.exe", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            if "cloudflared.exe" in line.lower():
                parts = line.split(",")
                if len(parts) >= 2:
                    try:
                        return int(parts[1].strip('"'))
                    except ValueError:
                        continue
        return None

    result = subprocess.run(["pgrep", "-f", "cloudflared"], capture_output=True, text=True, check=False)
    if result.stdout.strip():
        try:
            return int(result.stdout.strip().splitlines()[0])
        except ValueError:
            return None
    return None


def _start_quick_tunnel(port: int, wait_seconds: int = 60) -> tuple[str, int]:
    """Start a detached Cloudflare quick tunnel; return public URL and PID."""
    import time

    exe = _ensure_cloudflared()
    log_path = PROJECT_ROOT / "data" / "tunnel.log"
    url_file = PROJECT_ROOT / "data" / "public_endpoint.txt"
    pid_file = PROJECT_ROOT / "data" / "tunnel.pid"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    _kill_cloudflared()
    time.sleep(1)
    log_path.write_text("", encoding="utf-8")

    origin = f"http://127.0.0.1:{port}"
    if sys.platform == "win32":
        # Keep a visible CMD window open — start /b and RedirectStandardOutput both
        # cause cloudflared to exit when the parent shell session ends.
        subprocess.run(
            (
                f'start "genesis-tunnel" cmd /k "cd /d "{PROJECT_ROOT}" && '
                f'"{exe}" tunnel --url {origin} >> "{log_path}" 2>&1"'
            ),
            shell=True,
            check=True,
        )
    else:
        with open(log_path, "a", encoding="utf-8") as log_file:
            subprocess.Popen(
                [str(exe), "tunnel", "--url", origin],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )

    deadline = time.time() + wait_seconds
    public_url = None
    pid: int | None = None
    while time.time() < deadline:
        collected = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
        public_url = _parse_tunnel_url(collected)
        pid = _cloudflared_pid()
        if public_url and pid:
            break
        if pid is None and collected and "Registered tunnel connection" in collected:
            tail = collected[-500:]
            raise RuntimeError(f"Cloudflare tunnel exited early. Log tail: {tail}")
        time.sleep(0.5)

    if not public_url or not pid:
        _kill_cloudflared()
        tail = log_path.read_text(encoding="utf-8", errors="ignore")[-500:] if log_path.exists() else ""
        raise RuntimeError(
            "Cloudflare tunnel did not return a public URL in time. "
            f"Log tail: {tail}"
        )

    url_file.write_text(public_url + "\n", encoding="utf-8")
    pid_file.write_text(str(pid), encoding="utf-8")
    return public_url, pid


@app.command("deploy-endpoint")
def deploy_endpoint(
    url: Optional[str] = typer.Option(
        None,
        "--url",
        help="Public HTTPS URL (skip tunnel if provided)",
    ),
    tunnel: bool = typer.Option(
        False,
        "--tunnel",
        help="Start Cloudflare quick tunnel to the dashboard",
    ),
    port: int = typer.Option(8080, "--port", help="Local dashboard port for tunnel"),
    skip_onchain: bool = typer.Option(
        False,
        "--skip-onchain",
        help="Only update .env, do not refresh ERC-8004 agentURI",
    ),
) -> None:
    """Expose Genesis publicly and update GENESIS_AGENT_ENDPOINT (+ on-chain URI)."""
    env = get_env_settings()
    public_url = url

    if tunnel:
        if not _port_in_use("127.0.0.1", port):
            console.print(f"[yellow]![/] Dashboard not on port {port} — starting it …")
            if sys.platform == "win32":
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "genesis.cli",
                        "dashboard",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        str(port),
                    ],
                    cwd=str(PROJECT_ROOT),
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
            else:
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "genesis.cli",
                        "dashboard",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        str(port),
                    ],
                    cwd=str(PROJECT_ROOT),
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            import time

            for _ in range(20):
                time.sleep(0.5)
                if _port_in_use("127.0.0.1", port):
                    break
            else:
                console.print(
                    f"[red]Dashboard failed to start on port {port}.[/] "
                    f"Run: [cyan]genesis dashboard --host 0.0.0.0 --port {port}[/]"
                )
                raise typer.Exit(1)
            console.print(f"[green]✓[/] Dashboard listening on 0.0.0.0:{port}")
        console.print(f"[blue]→[/] Starting Cloudflare tunnel to 127.0.0.1:{port} …")
        try:
            public_url, tunnel_pid = _start_quick_tunnel(port)
            console.print(f"[green]✓[/] Tunnel running (PID {tunnel_pid})")
            console.print(f"  Logs: [cyan]{PROJECT_ROOT / 'data' / 'tunnel.log'}[/]")
        except Exception as e:
            console.print(f"[red]Tunnel failed:[/] {e}")
            raise typer.Exit(1) from e

    if not public_url:
        saved = PROJECT_ROOT / "data" / "public_endpoint.txt"
        if saved.exists():
            public_url = saved.read_text(encoding="utf-8").strip()
        if not public_url:
            console.print(
                "[red]No public URL.[/] Use [cyan]--tunnel[/] or [cyan]--url https://your-host[/]"
            )
            raise typer.Exit(1)

    public_url = public_url.rstrip("/")
    update_env_file("GENESIS_AGENT_ENDPOINT", public_url)
    clear_config_cache()
    env = get_env_settings()

    console.print(f"[green]✓[/] GENESIS_AGENT_ENDPOINT → [bold]{public_url}[/]")
    console.print(f"  Dashboard: [cyan]{public_url}/app[/]")
    console.print(f"  ERC-8183:    [cyan]{public_url}/erc8183/status[/]")

    if skip_onchain:
        return

    if not env.competition_agent_id:
        console.print("[yellow]![/] No COMPETITION_AGENT_ID — skipped on-chain URI update")
        return

    async def _update_onchain() -> None:
        twak = TWAKProvider.from_env(env)
        identity = BNBIdentityManager(env, twak=twak)
        try:
            result = await asyncio.wait_for(identity.update_agent_uri(), timeout=120.0)
            tx = result.get("transactionHash") or result.get("hash") or "submitted"
            console.print(f"[green]✓[/] ERC-8004 agentURI updated on-chain (tx={tx})")
        except TimeoutError:
            console.print("[yellow]![/] On-chain URI update timed out (120s)")
            console.print(
                "  .env is updated. Retry when TWAK/WSL is responsive:\n"
                f"  [cyan]genesis deploy-endpoint --url {public_url}[/]"
            )
        except Exception as e:
            console.print(f"[yellow]![/] On-chain URI update failed: {e}")
            console.print(
                "  .env is updated. Retry after TWAK is ready:\n"
                f"  [cyan]genesis deploy-endpoint --url {public_url}[/]"
            )

    _run_async(_update_onchain())


@app.command()
def dashboard(
    host: Optional[str] = typer.Option(None, "--host", help="Dashboard host"),
    port: Optional[int] = typer.Option(None, "--port", help="Dashboard port"),
) -> None:
    """Launch monitoring dashboard."""
    import uvicorn

    env = get_env_settings()
    h = host or env.dashboard_host
    p = port or env.dashboard_port

    if _port_in_use(h, p):
        pid = _listener_pid(p)
        console.print(f"[bold red]Port {p} is already in use on {h}.[/]")
        if pid:
            console.print(f"  Likely a previous dashboard (PID [bold]{pid}[/]).")
            if sys.platform == "win32":
                console.print(f"  Stop it: [cyan]Stop-Process -Id {pid} -Force[/]")
            else:
                console.print(f"  Stop it: [cyan]kill {pid}[/]")
        else:
            console.print("  Another app may be using this port.")
        console.print(f"  Or start on a different port: [cyan]genesis dashboard --port 8081[/]")
        raise typer.Exit(1)

    console.print(f"[bold]Starting Genesis at http://{h}:{p}[/]")
    console.print(f"  Landing page: [cyan]http://{h}:{p}/[/]")
    console.print(f"  Live dashboard: [cyan]http://{h}:{p}/app[/]")
    uvicorn.run("dashboard.main:app", host=h, port=p, reload=False)


if __name__ == "__main__":
    app()