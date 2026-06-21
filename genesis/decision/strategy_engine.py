"""LLM-powered strategy engine with instructor structured outputs."""

from __future__ import annotations

from typing import Any

import instructor
from anthropic import AsyncAnthropic
from loguru import logger
from openai import AsyncOpenAI

from genesis.core.config import EnvSettings
from genesis.core.models import Action, CompositeSignal, Decision, PortfolioSnapshot, RulesConfig
from genesis.decision.adaptive_mode import (
    buy_params_for_idle,
    is_aggressive_mode,
    pick_force_buy_candidate,
)
from genesis.decision.candidate_selection import is_buy_eligible, pick_best_buy_candidate
from genesis.decision.llm_prompts import build_system_prompt, build_user_prompt
from genesis.decision.risk_manager import RiskManager
from genesis.decision.trade_sizing import spot_trade_size_usd

# Quote / stable assets — not valid BUY targets (USDT→USD1 etc.)
NON_TRADABLE_SYMBOLS = frozenset(
    {
        "USDT", "USDC", "DAI", "TUSD", "USDD", "USD1", "USDe", "U", "FDUSD", "FRAX",
        "BUSD", "USDP", "LUSD", "SUSD", "GUSD", "EURI", "XUSD", "DUSD", "FRXUSD",
        "USDf", "USDF", "lisUSD", "XAUt", "BTCB", "ETH",
    }
)


class StrategyEngine:
    """
    Combines signal analysis with LLM structured decision making.

    Uses instructor + Pydantic for reliable JSON decisions.
    Supports Grok (xAI), OpenAI, and Anthropic providers.
    """

    def __init__(
        self,
        env: EnvSettings,
        rules: RulesConfig,
        risk_manager: RiskManager,
    ) -> None:
        self.env = env
        self.rules = rules
        self.risk_manager = risk_manager
        self._client = None
        if env.llm_enabled:
            try:
                self._client = self._create_instructor_client()
            except ValueError as e:
                logger.warning(f"LLM client unavailable, using rule-based decisions: {e}")

    def _create_instructor_client(self) -> Any:
        """Create instructor-patched async client for configured provider."""
        provider = self.env.llm_provider
        api_key = self.env.get_llm_api_key()

        if provider == "grok":
            base_url = "https://api.x.ai/v1"
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            return instructor.from_openai(client, mode=instructor.Mode.JSON)
        elif provider == "openai":
            client = AsyncOpenAI(api_key=api_key)
            return instructor.from_openai(client, mode=instructor.Mode.JSON)
        elif provider == "anthropic":
            client = AsyncAnthropic(api_key=api_key)
            return instructor.from_anthropic(client, mode=instructor.Mode.JSON)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

    async def decide(
        self,
        composites: list[CompositeSignal],
        portfolio: PortfolioSnapshot,
        *,
        idle_swap_cycles: int = 0,
    ) -> Decision:
        """Generate trading decision from signals and portfolio state."""
        if not self.env.llm_enabled or self._client is None:
            return self.decide_rule_based(composites, portfolio, idle_swap_cycles=idle_swap_cycles)

        system_prompt = build_system_prompt(self.rules)

        composite_data = [c.model_dump() for c in composites]
        portfolio_data = portfolio.model_dump()
        risk_state = {
            "halted": self.risk_manager.is_halted,
            "halt_reason": self.risk_manager.halt_reason,
            "drawdown_pct": portfolio.drawdown_pct,
            "max_drawdown_pct": self.rules.risk.max_drawdown_pct,
        }

        user_prompt = build_user_prompt(composite_data, portfolio_data, risk_state)

        try:
            decision: Decision = await self._client.chat.completions.create(
                model=self.env.llm_model,
                response_model=Decision,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_retries=2,
            )
            logger.info(
                f"LLM decision: {decision.action.value} {decision.asset} "
                f"(confidence={decision.confidence:.2f})"
            )
            return decision

        except Exception as e:
            logger.error(f"LLM decision failed, using rule-based fallback: {e}")
            return self.decide_rule_based(
                composites, portfolio, idle_swap_cycles=idle_swap_cycles
            )

    def decide_rule_based(
        self,
        composites: list[CompositeSignal],
        portfolio: PortfolioSnapshot,
        *,
        idle_swap_cycles: int = 0,
    ) -> Decision:
        """Deterministic signal-driven decisions when LLM is disabled or unavailable."""
        if not composites:
            return Decision(
                action=Action.HOLD,
                asset="BNB",
                reason="Rule-based: no composite signals",
                confidence=0.0,
                risk_notes="Rule engine",
            )

        thresholds = self.rules.signals
        aggressive = is_aggressive_mode(self.rules, idle_swap_cycles)
        buy_gate = buy_params_for_idle(self.rules, idle_swap_cycles)
        held = {p.symbol.upper(): p for p in portfolio.positions}

        if aggressive:
            logger.info(
                f"Adaptive aggression active: {idle_swap_cycles} consecutive cycles "
                f"without swap — buy_conviction_min={buy_gate.buy_conviction_min:.2f}"
            )

        for composite in composites:
            sym = composite.symbol.upper()
            if sym in held and composite.conviction <= thresholds.sell_conviction_max:
                position_value = getattr(held[sym], "value_usd", None) or self._trade_size_usd(portfolio)
                return Decision(
                    action=Action.SELL,
                    asset=composite.symbol,
                    size_usd=min(self._trade_size_usd(portfolio), position_value),
                    reason=(
                        f"Rule-based SELL: {composite.symbol} "
                        f"conviction={composite.conviction:.2f}"
                    ),
                    confidence=max(1.0 - composite.conviction, self.rules.risk.min_confidence),
                    risk_notes="Rule engine",
                    signals_used=list(composite.components.keys()),
                )

        buy_candidates = [
            c
            for c in composites
            if is_buy_eligible(
                c,
                self.rules,
                non_tradable=NON_TRADABLE_SYMBOLS,
                params=buy_gate,
            )
        ]
        if not buy_candidates and aggressive and self.rules.loop.adaptive_aggression.force_best_candidate:
            forced = pick_force_buy_candidate(
                composites,
                buy_gate,
                non_tradable=NON_TRADABLE_SYMBOLS,
            )
            if forced:
                buy_candidates = [forced]

        if buy_candidates:
            best = pick_best_buy_candidate(buy_candidates)
            rank = best.features.get("cmc_rank")
            mcap = best.features.get("market_cap_usd")
            from genesis.decision.candidate_selection import buy_alignment_score

            aligned = buy_alignment_score(
                best,
                bullish_component_min=buy_gate.bullish_component_min,
            )
            cap_note = ""
            if mcap and float(mcap) > 0:
                cap_note = f", mcap=${float(mcap):,.0f}"
            elif rank:
                cap_note = f", rank=#{rank}"
            mode_note = (
                f" (adaptive aggression after {idle_swap_cycles} idle swap cycles)"
                if aggressive
                else ""
            )
            return Decision(
                action=Action.BUY,
                asset=best.symbol,
                size_usd=self._trade_size_usd(portfolio),
                reason=(
                    f"Rule-based BUY: {best.symbol} "
                    f"conviction={best.conviction:.2f} ({best.direction})"
                    f"{cap_note}, aligned={aligned}/4 "
                    f"— largest cap among multi-signal bullish candidates"
                    f"{mode_note}"
                ),
                confidence=max(best.conviction, self.rules.risk.min_confidence),
                risk_notes="Rule engine",
                signals_used=list(best.components.keys()),
            )

        best = max(composites, key=lambda c: c.conviction)
        return Decision(
            action=Action.HOLD,
            asset=best.symbol,
            reason=(
                f"Rule-based HOLD: best {best.symbol} "
                f"conviction={best.conviction:.2f} ({best.direction})"
            ),
            confidence=0.5,
            risk_notes="Rule engine",
            signals_used=list(best.components.keys()),
        )

    def rule_based_fallback(
        self,
        composite: CompositeSignal,
        portfolio: PortfolioSnapshot | None = None,
    ) -> Decision:
        """Single-token rule evaluation (compat wrapper)."""
        portfolio = portfolio or PortfolioSnapshot(total_value_usd=1000.0, available_usd=800.0)
        return self.decide_rule_based([composite], portfolio)

    def _trade_size_usd(self, portfolio: PortfolioSnapshot) -> float:
        """Spot sizing: spot_stable_pct% of USDT + USDC balance."""
        return spot_trade_size_usd(portfolio, self.rules)