"""PancakeSwap Perps executor — ApolloX Diamond on BSC via TWAK wallet signing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from genesis.core.models import Action, RulesConfig, Trade, TradeStatus
from genesis.execution.pancake_perps import (
    MIN_NOTIONAL_USD,
    PANCAKE_PERPS_DIAMOND,
    build_open_params,
    close_perps_position,
    fetch_index_price,
    open_perps_position,
    resolve_perps_market,
)
from genesis.execution.twak_provider import TWAKProvider

if TYPE_CHECKING:
    pass


class PerpsExecutor:
    """
    PancakeSwap Perps (ApolloX Diamond) on BSC.

    Uses the TWAK HD wallet for signing (same key as spot swaps / x402).
    Positions open via ``openMarketTrade``; keeper settlement is async on-chain.
    """

    def __init__(
        self,
        twak: TWAKProvider,
        rules: RulesConfig,
        network: str = "bsc-mainnet",
        wallet_password: str = "",
    ) -> None:
        self.twak = twak
        self.rules = rules
        self.network = network
        self.wallet_password = wallet_password or twak.wallet_password
        self.diamond = PANCAKE_PERPS_DIAMOND

    @property
    def is_enabled(self) -> bool:
        return self.rules.perps.enabled

    def eligible_perps_symbols(self) -> set[str]:
        """Symbols allowed for perps: perps.allowed_symbols ∩ rules allowlist ∩ venue markets."""
        configured = {s.upper() for s in self.rules.perps.allowed_symbols}
        allowlisted = {t.symbol.upper() for t in self.rules.allowed_tokens}
        return configured & allowlisted

    def supports_symbol(self, symbol: str) -> bool:
        key = symbol.upper()
        if key not in self.eligible_perps_symbols():
            return False
        try:
            resolve_perps_market(key)
            return True
        except ValueError:
            return False

    def _account(self) -> Any:
        from genesis.onchain.twak_evm_signer import get_twak_eth_account

        if not self.wallet_password:
            raise ValueError("TWAK_WALLET_PASSWORD required for perps signing")
        return get_twak_eth_account(self.twak, self.wallet_password)

    async def open_position(
        self,
        symbol: str,
        side: Action,
        size_usd: float,
        leverage: int | None = None,
        *,
        dry_run: bool = False,
    ) -> Trade:
        """Open a leveraged perpetual position on PancakeSwap Perps."""
        if not self.is_enabled:
            raise RuntimeError("Perps trading is disabled in rules.yaml")

        if side not in (Action.BUY, Action.SELL):
            raise ValueError(f"Perps open requires BUY (long) or SELL (short), got {side}")

        if not self.supports_symbol(symbol):
            eligible = ", ".join(sorted(self.eligible_perps_symbols()))
            raise ValueError(
                f"{symbol} is not eligible for perps. "
                f"Allowed (config ∩ allowlist): {eligible}. "
                f"Venue must list an ApolloX market (TWT has no perps market yet)."
            )

        max_lev = self.rules.perps.max_leverage
        lev = min(max(leverage or 1, 1), max_lev)
        slippage_bps = getattr(self.rules.perps, "default_slippage_bps", None) or self.rules.risk.max_slippage_bps
        collateral = getattr(self.rules.perps, "collateral_token", "USDT") or "USDT"

        _, market = resolve_perps_market(symbol)
        index_price = await fetch_index_price(market)
        params = build_open_params(
            symbol,
            notional_usd=size_usd,
            leverage=lev,
            is_long=side == Action.BUY,
            index_price=index_price,
            slippage_bps=slippage_bps,
            collateral_symbol=collateral,
        )

        logger.info(
            f"Perps open: {side.value} {symbol} ${size_usd:.2f} notional @ {lev}x "
            f"(index=${index_price:.4f}, market={market})"
        )

        if dry_run:
            return Trade(
                symbol=symbol,
                side=side,
                amount_usd=size_usd,
                price=index_price,
                status=TradeStatus.SIMULATED,
                simulated=True,
                execution_type="perps",
                leverage=lev,
                error="dry-run — no transaction submitted",
            )

        account = self._account()
        tx_hash = await open_perps_position(self.twak.bsc_rpc_url, account, params)

        return Trade(
            symbol=symbol,
            side=side,
            amount_usd=size_usd,
            price=index_price,
            status=TradeStatus.EXECUTED,
            tx_hash=tx_hash,
            execution_type="perps",
            leverage=lev,
        )

    async def close_position(
        self,
        trade_hash: str,
        symbol: str = "",
        *,
        dry_run: bool = False,
    ) -> Trade:
        """Close an existing perps position by on-chain tradeHash."""
        if not self.is_enabled:
            raise RuntimeError("Perps trading is disabled")

        if not trade_hash or not trade_hash.startswith("0x"):
            raise ValueError("trade_hash must be a 0x-prefixed bytes32")

        logger.info(f"Perps close: {symbol or 'position'} hash={trade_hash[:18]}…")

        if dry_run:
            return Trade(
                symbol=symbol or "PERP",
                side=Action.SELL,
                amount_usd=0,
                status=TradeStatus.SIMULATED,
                simulated=True,
                execution_type="perps",
                position_id=trade_hash,
                error="dry-run — no transaction submitted",
            )

        account = self._account()
        tx_hash = await close_perps_position(self.twak.bsc_rpc_url, account, trade_hash)

        return Trade(
            symbol=symbol or "PERP",
            side=Action.SELL,
            amount_usd=0,
            status=TradeStatus.EXECUTED,
            tx_hash=tx_hash,
            execution_type="perps",
            position_id=trade_hash,
        )