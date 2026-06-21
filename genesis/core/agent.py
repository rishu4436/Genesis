"""Main Genesis autonomous trading agent loop."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from genesis.core.config import EnvSettings, RulesConfig, demo_mode_active
from genesis.core.database import Database
from genesis.core.scan_tokens import tokens_to_scan
from genesis.core.models import (
    Action,
    AuditRecord,
    Decision,
    TokenConfig,
    TradeStatus,
)
from genesis.data.cmc_provider import CMCProvider
from genesis.data.market_context import apply_market_context
from genesis.data.price_resolver import PriceResolver
from genesis.data.signal_aggregator import SignalAggregator
from genesis.decision.risk_manager import RiskManager
from genesis.decision.adaptive_mode import count_consecutive_idle_swap_cycles
from genesis.decision.dca_dip import (
    compute_dca_state_after_buy,
    dca_dip_active,
    decide_dca_dip,
)
from genesis.decision.strategy_engine import StrategyEngine
from genesis.decision.trade_sizing import (
    perps_margin_usd,
    perps_notional_usd,
)
from genesis.execution.liquidate import format_swap_error
from genesis.execution.pancake_perps import MIN_NOTIONAL_USD
from genesis.execution.perps_executor import PerpsExecutor
from genesis.execution.swap_preflight import find_swappable_buy_asset
from genesis.execution.twak_provider import TWAKProvider
from genesis.onchain.bnb_identity import BNBIdentityManager


class GenesisAgent:
    """
    Autonomous self-custody AI trading agent.

    Loop: CMC signals → aggregate → LLM decision → risk validation → TWAK execution → audit
    """

    def __init__(
        self,
        env: EnvSettings,
        rules: RulesConfig,
        simulate: bool = False,
    ) -> None:
        self.env = env
        self.rules = rules
        self.simulate = simulate

        # Providers (dependency injection points)
        self.twak = TWAKProvider.from_env(env)
        self.cmc = CMCProvider(
            mcp_url=env.cmc_mcp_url,
            api_key=env.cmc_api_key,
            x402_enabled=env.cmc_x402_enabled,
            twak=self.twak,
            x402_mode=env.cmc_x402_mode,
            x402_max_payment=env.cmc_x402_max_payment,
            x402_prefer_network=env.cmc_x402_prefer_network,
        )
        self.price_resolver = PriceResolver(self.twak, self.cmc)
        self.aggregator = SignalAggregator(rules)
        self.risk_manager = RiskManager(rules)
        self.strategy = StrategyEngine(env, rules, self.risk_manager)
        self.perps = PerpsExecutor(
            self.twak,
            rules,
            env.twak_network,
            wallet_password=env.twak_wallet_password or env.wallet_password,
        )
        self.identity = BNBIdentityManager(env, twak=self.twak)
        self.db = Database(env.genesis_db_path)

        self._running = False
        self._cycle_count = 0
        self._progress_cb: Callable[[str, dict[str, Any]], None] | None = None

    async def initialize(self) -> None:
        """Initialize database and verify providers."""
        await self.db.initialize()

        if not self.simulate:
            if not await self.twak.verify_installation():
                logger.warning(
                    "TWAK CLI not found. Install: "
                    "curl -fsSL https://raw.githubusercontent.com/trustwallet/tw-agent-skills/main/install.sh | bash"
                )

        logger.info(
            f"Genesis agent initialized (network={self.env.genesis_network}, "
            f"simulate={self.simulate})"
        )

    async def run_loop(self, max_cycles: int | None = None) -> None:
        """
        Main autonomous trading loop.

        Runs indefinitely or for max_cycles, sleeping between iterations.
        """
        self._running = True
        interval = self.rules.loop.interval_seconds

        logger.info(f"Starting agent loop (interval={interval}s, simulate={self.simulate})")

        while self._running:
            if max_cycles and self._cycle_count >= max_cycles:
                break

            try:
                await self.run_cycle()
            except Exception as e:
                logger.error(f"Cycle error: {e}")

            self._cycle_count += 1
            if self._running:
                logger.debug(f"Sleeping {interval}s until next cycle...")
                await asyncio.sleep(interval)

        logger.info(f"Agent loop stopped after {self._cycle_count} cycles")

    async def run_cycle(self) -> AuditRecord:
        """Execute a single decision cycle."""
        cycle_id = str(uuid.uuid4())[:8]
        start = datetime.now(timezone.utc)
        logger.info(f"=== Cycle {cycle_id} (#{self._cycle_count + 1}) ===")

        audit = AuditRecord(cycle_id=cycle_id)

        def _emit(event: str, payload: dict[str, Any]) -> None:
            if self._progress_cb:
                self._progress_cb(event, payload)

        # 1. Fetch portfolio
        _emit("phase", {"phase": "portfolio", "cycle_id": cycle_id})
        try:
            if self.simulate:
                from genesis.core.models import PortfolioSnapshot

                portfolio = PortfolioSnapshot(
                    total_value_usd=1000.0,
                    available_usd=800.0,
                )
            else:
                traded = await self.db.get_traded_asset_symbols()
                supplement = self.twak.resolve_supplement_tokens(
                    traded,
                    self.rules.allowed_tokens,
                )
                portfolio = await self.twak.get_portfolio(
                    self.rules.execution.default_quote,
                    supplement_tokens=supplement,
                    price_resolver=self.price_resolver,
                )
            self.risk_manager.update_peak(portfolio)
            audit.portfolio = portfolio
            await self.db.save_portfolio_snapshot(portfolio)
        except Exception as e:
            logger.error(f"Portfolio fetch failed: {e}")
            portfolio = None

        if self.risk_manager.is_halted:
            logger.warning(f"Trading halted: {self.risk_manager.halt_reason}")
            audit.duration_ms = self._elapsed_ms(start)
            await self.db.save_audit(audit)
            _emit("complete", {"cycle_id": cycle_id})
            return audit

        # 2. Market-wide CMC context (macro + market-cap TA)
        _emit("phase", {"phase": "market_context", "cycle_id": cycle_id})
        market_context = None
        try:
            market_context = await self.cmc.fetch_market_context()
            audit.market_context = market_context
            if market_context.blocks_buys:
                self.risk_manager.set_buy_block(market_context.block_reason)
            else:
                self.risk_manager.clear_buy_block()
        except Exception as e:
            logger.warning(f"Market context fetch failed: {e}")
            self.risk_manager.clear_buy_block()

        # 3. Fetch CMC signals for tradable tokens (skip stablecoins)
        all_signals: list = []
        if market_context:
            all_signals.extend(market_context.signals)
        demo = demo_mode_active(self.env, self.rules)
        scan_list = tokens_to_scan(self.rules, demo=demo)
        skipped_stables = len(self.rules.allowed_tokens) - len(
            [t for t in self.rules.allowed_tokens if not demo or t in scan_list]
        )
        if demo:
            logger.info(
                f"Demo mode: scanning {len(scan_list)} tokens "
                f"(concurrency={self.rules.loop.demo.concurrency})"
            )
        elif skipped_stables:
            logger.debug(f"Skipping stablecoins from full allowlist CMC scan")

        concurrency = self.rules.loop.demo.concurrency if demo else 1
        composites, token_signals = await self._scan_tokens_parallel(
            scan_list,
            cycle_id=cycle_id,
            concurrency=max(1, concurrency),
            _emit=_emit,
        )
        for signals in token_signals:
            all_signals.extend(signals)

        audit.signals = all_signals

        if not composites:
            logger.warning("No signals available — HOLD")
            audit.duration_ms = self._elapsed_ms(start)
            await self.db.save_audit(audit)
            _emit("complete", {"cycle_id": cycle_id})
            return audit

        if market_context:
            composites = apply_market_context(composites, market_context, self.rules)

        audit.composites = composites
        _emit(
            "phase",
            {
                "phase": "deciding",
                "cycle_id": cycle_id,
                "scanned": len(composites),
                "total": len(scan_list),
            },
        )

        # 4. Decision (LLM or rule-based per LLM_ENABLED)
        recent_audits = await self.db.get_recent_audits(50)
        idle_swap_cycles = count_consecutive_idle_swap_cycles(recent_audits)

        from genesis.core.models import PortfolioSnapshot

        portfolio_for_decision = portfolio or PortfolioSnapshot(
            total_value_usd=0.0, available_usd=0.0
        )

        if dca_dip_active(self.rules):
            dca_states = await self.db.get_dca_states()
            decision = decide_dca_dip(
                composites,
                portfolio_for_decision,
                self.rules,
                dca_states,
            )
        else:
            use_rules_only = demo and self.rules.loop.demo.rule_based_only
            if portfolio:
                if use_rules_only:
                    decision = self.strategy.decide_rule_based(
                        composites,
                        portfolio,
                        idle_swap_cycles=idle_swap_cycles,
                    )
                else:
                    decision = await self.strategy.decide(
                        composites,
                        portfolio,
                        idle_swap_cycles=idle_swap_cycles,
                    )
            else:
                decision = self.strategy.decide_rule_based(
                    composites,
                    portfolio_for_decision,
                    idle_swap_cycles=idle_swap_cycles,
                )
            if portfolio:
                decision = self.strategy.attach_exit_metadata(decision, composites, portfolio)
        audit.decision = decision
        audit.composite = next(
            (c for c in composites if c.symbol.upper() == decision.asset.upper()),
            composites[0] if composites else None,
        )
        await self.db.save_decision(decision)

        if decision.action == Action.HOLD:
            logger.info(f"HOLD — {decision.reason}")
            audit.duration_ms = self._elapsed_ms(start)
            await self.db.save_audit(audit)
            _emit("complete", {"cycle_id": cycle_id})
            return audit

        # 5. Risk validation (HARD enforcement)
        if portfolio:
            validation = self.risk_manager.validate(decision, portfolio)
        else:
            from genesis.core.models import RiskValidation

            validation = RiskValidation(approved=False, reason="No portfolio data")
        audit.risk_validation = validation

        if not validation.approved:
            logger.warning(f"Risk REJECTED: {validation.reason}")
            audit.duration_ms = self._elapsed_ms(start)
            await self.db.save_audit(audit)
            _emit("complete", {"cycle_id": cycle_id})
            return audit

        _emit("phase", {"phase": "executing", "cycle_id": cycle_id})

        # 6. Execute trade
        spot_size_usd = validation.adjusted_size_usd or decision.size_usd or 0
        size_usd = spot_size_usd
        quote = self.rules.execution.default_quote
        full_token_map = {t.symbol: t for t in self.rules.allowed_tokens}
        execution_asset = decision.asset
        quote_token = full_token_map.get(quote) or next(
            (t for t in self.rules.allowed_tokens if t.symbol.upper() == quote.upper()),
            None,
        )

        if (
            not self.simulate
            and decision.action == Action.BUY
            and portfolio is not None
            and audit.composites
        ):
            swappable, route_error = await find_swappable_buy_asset(
                self.twak,
                decision=decision,
                composites=audit.composites,
                rules=self.rules,
                quote=quote,
                size_usd=spot_size_usd,
                slippage_bps=self.rules.risk.max_slippage_bps,
                allowed_tokens=self.rules.allowed_tokens,
            )
            if not swappable:
                logger.warning(f"BUY blocked — no TWAK route: {route_error}")
                audit.duration_ms = self._elapsed_ms(start)
                await self.db.save_audit(audit)
                _emit("complete", {"cycle_id": cycle_id})
                return audit
            if swappable.upper() != decision.asset.upper():
                original_asset = decision.asset
                execution_asset = swappable
                decision = decision.model_copy(
                    update={
                        "asset": swappable,
                        "reason": (
                            f"{decision.reason} "
                            f"(rerouted: {original_asset} has no TWAK liquidity)"
                        ),
                    }
                )
                audit.decision = decision

        asset_token = full_token_map.get(execution_asset)
        try:
            if self.simulate:
                trade = await self.twak.simulate_swap(
                    quote if decision.action == Action.BUY else execution_asset,
                    execution_asset if decision.action == Action.BUY else quote,
                    size_usd,
                    from_address=quote_token.address if quote_token and decision.action == Action.BUY else (
                        asset_token.address if asset_token else None
                    ),
                    to_address=asset_token.address if asset_token and decision.action == Action.BUY else (
                        quote_token.address if quote_token else None
                    ),
                    slippage_bps=self.rules.risk.max_slippage_bps,
                )
            elif (
                self.rules.perps.enabled
                and decision.action in (Action.BUY, Action.SELL)
                and self.perps.supports_symbol(execution_asset)
                and portfolio is not None
            ):
                perps_size = perps_notional_usd(portfolio, self.rules)
                margin = perps_margin_usd(portfolio, self.rules)
                if perps_size >= MIN_NOTIONAL_USD and margin > 0:
                    size_usd = perps_size
                    trade = await self.perps.open_position(
                        execution_asset,
                        decision.action,
                        size_usd,
                        leverage=self.rules.perps.max_leverage,
                    )
                else:
                    logger.info(
                        f"Perps skipped for {execution_asset}: "
                        f"notional ${perps_size:.2f} < venue min ${MIN_NOTIONAL_USD:.0f} "
                        f"— using spot ${spot_size_usd:.2f}"
                    )
                    trade = await self.twak.execute_trade(
                        decision.action,
                        execution_asset,
                        spot_size_usd,
                        quote,
                        self.rules.risk.max_slippage_bps,
                        asset_address=asset_token.address if asset_token else None,
                        quote_address=quote_token.address if quote_token else None,
                    )
            else:
                trade = await self.twak.execute_trade(
                    decision.action,
                    execution_asset,
                    size_usd,
                    quote,
                    self.rules.risk.max_slippage_bps,
                    asset_address=asset_token.address if asset_token else None,
                    quote_address=quote_token.address if quote_token else None,
                )

            audit.trade = trade
            await self.db.save_trade(trade)
            self.risk_manager.record_trade(trade)

            if trade.tx_hash:
                logger.info(f"Trade executed: {trade.tx_hash}")
            elif trade.simulated:
                logger.info(f"Simulated trade: {trade.symbol} ${trade.amount_usd:.2f}")

            await self._sync_dca_state_after_trade(decision, audit.trade)

        except Exception as e:
            logger.error(f"Execution failed: {format_swap_error(e)}")
            from genesis.core.models import Trade

            audit.trade = Trade(
                symbol=execution_asset,
                side=decision.action,
                amount_usd=size_usd,
                status=TradeStatus.FAILED,
                error=str(e),
            )

        audit.duration_ms = self._elapsed_ms(start)
        await self.db.save_audit(audit)
        _emit("complete", {"cycle_id": cycle_id})
        logger.info(f"Cycle {cycle_id} complete ({audit.duration_ms}ms)")
        return audit

    async def _scan_tokens_parallel(
        self,
        scan_list: list[TokenConfig],
        *,
        cycle_id: str,
        concurrency: int,
        _emit: Callable[[str, dict[str, Any]], None],
    ) -> tuple[list, list[list]]:
        """Fetch CMC signals for many tokens — parallel in demo, sequential otherwise."""
        from genesis.core.models import CompositeSignal

        scan_total = len(scan_list)
        composites: list[CompositeSignal] = []
        all_token_signals: list[list] = []
        scanned_count = 0
        lock = asyncio.Lock()

        _emit(
            "scan_start",
            {"cycle_id": cycle_id, "total": scan_total, "scanned": 0, "current_symbol": None},
        )

        async def _scan_one(token: TokenConfig) -> None:
            nonlocal scanned_count
            symbol = token.symbol
            async with lock:
                _emit(
                    "scanning",
                    {
                        "cycle_id": cycle_id,
                        "current_symbol": symbol,
                        "scanned": scanned_count,
                        "total": scan_total,
                    },
                )
            try:
                signals = await self.cmc.get_all_signals(symbol, token.cmc_id)
                composite = self.aggregator.aggregate(symbol, signals)
                composite_payload = composite.model_dump(mode="json")
                composite_payload["_signals"] = [
                    s.model_dump(mode="json") for s in signals
                ]
                async with lock:
                    composites.append(composite)
                    all_token_signals.append(signals)
                    scanned_count += 1
                    _emit(
                        "composite",
                        {
                            "cycle_id": cycle_id,
                            "current_symbol": symbol,
                            "scanned": scanned_count,
                            "total": scan_total,
                            "composite": composite_payload,
                        },
                    )
            except Exception as e:
                logger.warning(f"Signal fetch failed for {symbol}: {e}")
                async with lock:
                    scanned_count += 1

        if concurrency <= 1:
            for token in scan_list:
                await _scan_one(token)
        else:
            sem = asyncio.Semaphore(concurrency)

            async def _bounded(token: TokenConfig) -> None:
                async with sem:
                    await _scan_one(token)

            await asyncio.gather(*[_bounded(t) for t in scan_list])

        return composites, all_token_signals

    async def _sync_dca_state_after_trade(self, decision: Decision, trade: Any) -> None:
        """Persist DCA ladder after confirmed buys; clear on take-profit sells."""
        if decision.strategy_mode != "dca_dip" or trade is None:
            return
        status = getattr(trade, "status", None)
        status_val = status.value if hasattr(status, "value") else str(status or "")
        if status_val.lower() not in {"confirmed", "submitted", "simulated"}:
            return
        if decision.action == Action.SELL and decision.exit_trigger == "dca_take_profit":
            await self.db.clear_dca_state(decision.asset)
            logger.info(f"DCA ladder closed for {decision.asset} (take-profit)")
            return
        if decision.action != Action.BUY:
            return
        price = getattr(trade, "price", None) or decision.current_price_usd
        if not price or float(price) <= 0:
            return
        states = await self.db.get_dca_states()
        sym = decision.asset.upper()
        updated = compute_dca_state_after_buy(
            states.get(sym),
            symbol=decision.asset,
            buy_price=float(price),
            buy_usd=float(getattr(trade, "amount_usd", 0) or decision.size_usd or 0),
            change_24h_pct=decision.change_24h_pct,
            dca_step=decision.dca_step or 1,
        )
        await self.db.upsert_dca_state(updated)
        logger.info(
            f"DCA ladder updated {decision.asset}: "
            f"step={updated.buy_count} avg=${updated.avg_entry_price_usd:.4f}"
        )

    def stop(self) -> None:
        """Signal the agent loop to stop."""
        self._running = False
        logger.info("Stop signal received")

    async def shutdown(self) -> None:
        """Clean up resources."""
        await self.cmc.close()
        logger.info("Agent shutdown complete")

    @staticmethod
    def _elapsed_ms(start: datetime) -> int:
        return int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

    @staticmethod
    def _format_error(exc: Exception) -> str:
        """Unwrap tenacity RetryError so TWAK messages surface in logs."""
        return format_swap_error(exc)