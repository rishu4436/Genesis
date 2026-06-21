"""Hard risk enforcement before any trade execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

from genesis.core.models import (
    Action,
    Decision,
    PortfolioSnapshot,
    RiskValidation,
    RulesConfig,
    Trade,
)
from genesis.core.wallet_tokens import count_trading_positions
from genesis.decision.trade_sizing import (
    max_spot_trade_size_usd,
    resolve_spot_trade_size,
    stable_quote_balance_usd,
)
from genesis.utils import pct_to_fraction


class RiskManager:
    """
    Enforces hard risk rules — NEVER overridden by LLM.

    Checks: drawdown, position sizing, token allowlist, confidence,
    cooldowns, slippage, max positions, stable-balance trade sizing.
    """

    def __init__(self, rules: RulesConfig) -> None:
        self.rules = rules
        self._last_trade_time: dict[str, datetime] = {}
        self._peak_portfolio_value: float = 0.0
        self._halted: bool = False
        self._halt_reason: str = ""
        self._block_buys: bool = False
        self._block_buys_reason: str = ""

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def set_buy_block(self, reason: str) -> None:
        """Temporarily block new BUY orders (e.g. upcoming macro events)."""
        self._block_buys = True
        self._block_buys_reason = reason
        logger.warning(f"Buy block active: {reason}")

    def clear_buy_block(self) -> None:
        """Clear temporary buy block from market context."""
        self._block_buys = False
        self._block_buys_reason = ""

    def update_peak(self, portfolio: PortfolioSnapshot) -> None:
        """Track peak portfolio value for drawdown calculation."""
        if portfolio.total_value_usd > self._peak_portfolio_value:
            self._peak_portfolio_value = portfolio.total_value_usd

        if self._peak_portfolio_value > 0:
            drawdown = (
                (self._peak_portfolio_value - portfolio.total_value_usd)
                / self._peak_portfolio_value
                * 100
            )
            portfolio.drawdown_pct = drawdown
            portfolio.peak_value_usd = self._peak_portfolio_value

            if drawdown >= self.rules.risk.max_drawdown_pct:
                self._halted = True
                self._halt_reason = (
                    f"Max drawdown exceeded: {drawdown:.2f}% >= "
                    f"{self.rules.risk.max_drawdown_pct}%"
                )
                logger.error(self._halt_reason)

    def validate(
        self,
        decision: Decision,
        portfolio: PortfolioSnapshot,
    ) -> RiskValidation:
        """Validate decision against all hard rules. Returns approval or rejection."""
        violations: list[str] = []

        if self._halted:
            return RiskValidation(
                approved=False,
                reason=f"Trading halted: {self._halt_reason}",
                violations=[self._halt_reason],
            )

        if decision.action == Action.HOLD:
            return RiskValidation(approved=True, reason="HOLD — no execution needed")

        allowed_symbols = {t.symbol.upper() for t in self.rules.allowed_tokens}
        if decision.asset.upper() not in allowed_symbols:
            violations.append(f"Token {decision.asset} not in allowlist")

        if decision.confidence < self.rules.risk.min_confidence:
            violations.append(
                f"Confidence {decision.confidence:.2f} < min {self.rules.risk.min_confidence}"
            )

        asset_key = decision.asset.upper()
        last_trade = self._last_trade_time.get(asset_key)
        if last_trade:
            cooldown = timedelta(minutes=self.rules.risk.cooldown_minutes)
            if datetime.now(timezone.utc) - last_trade < cooldown:
                violations.append(f"Cooldown active for {decision.asset}")

        if decision.action == Action.BUY:
            if self._block_buys:
                violations.append(f"Buys blocked: {self._block_buys_reason}")
            open_trades = count_trading_positions(portfolio.positions)
            if open_trades >= self.rules.risk.max_open_positions:
                violations.append(
                    f"Max open positions ({self.rules.risk.max_open_positions}) reached "
                    f"({open_trades} trading, excluding BNB/stables)"
                )

        size_usd = self._resolve_trade_size(decision, portfolio)
        stable_balance = stable_quote_balance_usd(portfolio)

        if size_usd is None or size_usd <= 0:
            min_swap = self.rules.risk.min_swap_usd
            if stable_balance < min_swap:
                violations.append(
                    f"Stables ${stable_balance:.2f} below ${min_swap:.2f} minimum swap"
                )
            else:
                violations.append("Trade size could not be resolved")
        elif decision.action == Action.BUY and size_usd > stable_balance:
            violations.append(
                f"Insufficient USDT/USDC: need ${size_usd:.2f}, "
                f"have ${stable_balance:.2f}"
            )

        if violations:
            return RiskValidation(
                approved=False,
                reason=f"Risk violations: {'; '.join(violations)}",
                violations=violations,
            )

        max_swap = max_spot_trade_size_usd(portfolio, self.rules)
        return RiskValidation(
            approved=True,
            reason=(
                f"Spot size ${size_usd:.2f} "
                f"(min ${self.rules.risk.min_swap_usd:.2f}, "
                f"max ${max_swap:.2f} = {self.rules.risk.spot_stable_pct:.0f}% of "
                f"USDT/USDC ${stable_balance:.2f})"
            ),
            adjusted_size_usd=size_usd,
        )

    def record_trade(self, trade: Trade) -> None:
        """Record trade timestamp for cooldown tracking."""
        self._last_trade_time[trade.symbol.upper()] = datetime.now(timezone.utc)

    def validate_slippage(self, expected_bps: int) -> bool:
        """Check if slippage is within limits."""
        return expected_bps <= self.rules.risk.max_slippage_bps

    def _resolve_trade_size(
        self, decision: Decision, portfolio: PortfolioSnapshot
    ) -> float | None:
        """Resolve spot trade size — min $1, max spot_stable_pct% of USDT/USDC."""
        if decision.size_usd is not None:
            size = resolve_spot_trade_size(portfolio, self.rules, decision.size_usd)
            return size if size > 0 else None
        if decision.size_pct is not None:
            base = stable_quote_balance_usd(portfolio)
            if base > 0:
                requested = round(base * pct_to_fraction(decision.size_pct), 2)
                size = resolve_spot_trade_size(portfolio, self.rules, requested)
                return size if size > 0 else None
            if portfolio.total_value_usd > 0:
                requested = round(
                    portfolio.total_value_usd * pct_to_fraction(decision.size_pct),
                    2,
                )
                size = resolve_spot_trade_size(portfolio, self.rules, requested)
                return size if size > 0 else None
            return None
        size = resolve_spot_trade_size(portfolio, self.rules)
        return size if size > 0 else None