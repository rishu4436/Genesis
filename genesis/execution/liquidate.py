"""Sell-all helper — liquidate wallet tokens to USDT (keeps BNB + stables)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from genesis.core.models import Action, PortfolioSnapshot, TokenConfig, Trade
from genesis.core.wallet_tokens import is_gas_or_stable
from genesis.execution.twak_provider import BSC_USDT_ADDRESS, TWAKProvider
from genesis.onchain.bsc_rpc import erc20_balance_of

if TYPE_CHECKING:
    from genesis.core.database import Database
    from genesis.core.models import RulesConfig

# Small buffer so swaps do not revert on rounding / price drift
SELL_AMOUNT_BUFFER = 0.995

# Below this USD value, DEX routers reject swaps — skip instead of failing
DEFAULT_DUST_THRESHOLD_USD = 0.01


def is_unsellable_dust(value_usd: float, dust_threshold: float = DEFAULT_DUST_THRESHOLD_USD) -> bool:
    """True when a position is too small for PancakeSwap/TWAK minimum notional."""
    return 0 < value_usd < dust_threshold


def format_swap_error(exc: BaseException) -> str:
    """Unwrap RetryError / TWAKError into a readable message."""
    root = exc
    try:
        from tenacity import RetryError

        if isinstance(exc, RetryError) and exc.last_attempt is not None:
            inner = exc.last_attempt.exception()
            if inner is not None:
                root = inner
    except ImportError:
        pass

    from genesis.execution.twak_provider import TWAKError

    if isinstance(root, TWAKError):
        msg = str(root)
        if root.stderr:
            detail = root.stderr.strip().splitlines()[-1][:180]
            if detail and detail not in msg:
                msg = f"{msg} — {detail}"
        return msg
    return str(root)


@dataclass
class SellTarget:
    """Token position queued for liquidation to USDT."""

    symbol: str
    amount: float
    address: str
    value_usd: float


def format_sell_amount(balance: float) -> float:
    """Round down sell size with a safety buffer to avoid insufficient-balance reverts."""
    if balance <= 0:
        return 0.0
    buffered = balance * SELL_AMOUNT_BUFFER
    if buffered >= 1000:
        return float(int(buffered))
    if buffered >= 1:
        return float(int(buffered * 100) / 100)
    if buffered >= 0.01:
        return float(int(buffered * 10000) / 10000)
    return round(buffered, 8)


def collect_sell_targets(
    portfolio: PortfolioSnapshot,
    token_map: dict[str, TokenConfig],
    *,
    min_usd: float = 0.0,
    on_chain_balances: dict[str, float] | None = None,
) -> list[SellTarget]:
    """Build sell list from portfolio + optional on-chain balances (excludes BNB/stables)."""
    on_chain_balances = on_chain_balances or {}
    merged: dict[str, tuple[float, float, float | None]] = {}

    for pos in portfolio.positions:
        key = pos.symbol.upper()
        if is_gas_or_stable(key) or pos.amount <= 0:
            continue
        price = pos.current_price
        merged[key] = (pos.amount, pos.amount * (price or 0.0), price)

    for key, balance in on_chain_balances.items():
        sym = key.upper()
        if is_gas_or_stable(sym) or balance <= 0:
            continue
        current = merged.get(sym)
        if current is None or balance > current[0]:
            price = current[2] if current else None
            est_usd = balance * (price or 0.0) if price else (current[1] if current else 0.0)
            merged[sym] = (balance, est_usd, price)

    targets: list[SellTarget] = []
    for key, (amount, value_usd, _price) in merged.items():
        if value_usd < min_usd and min_usd > 0:
            continue

        token = token_map.get(key)
        if not token:
            logger.warning(f"Skipping {key}: no contract address in allowlist")
            continue

        sell_amount = format_sell_amount(amount)
        if sell_amount <= 0:
            continue

        targets.append(
            SellTarget(
                symbol=token.symbol,
                amount=sell_amount,
                address=token.address,
                value_usd=value_usd,
            )
        )

    targets.sort(key=lambda t: (t.value_usd, t.amount), reverse=True)
    return targets


async def _fetch_on_chain_balances(
    twak: TWAKProvider,
    tokens: list[TokenConfig],
    wallet: str,
) -> dict[str, float]:
    """Read ERC-20 balances for candidate sell tokens."""
    if twak.chain != "bsc" or not wallet:
        return {}

    balances: dict[str, float] = {}
    for token in tokens:
        key = token.symbol.upper()
        if is_gas_or_stable(key):
            continue
        try:
            balance = await erc20_balance_of(twak.bsc_rpc_url, token.address, wallet)
            if balance > 0:
                balances[key] = balance
        except Exception as e:
            logger.warning(f"On-chain balance fetch failed for {token.symbol}: {e}")
    return balances


async def _enrich_target_values(
    twak: TWAKProvider,
    targets: list[SellTarget],
) -> None:
    """Fill missing USD estimates so dust tokens are still attempted."""
    for target in targets:
        if target.value_usd > 0:
            continue
        try:
            price = await twak.get_price_for_token(target.symbol, target.address)
            target.value_usd = target.amount * price
        except Exception as e:
            logger.debug(f"Price unavailable for {target.symbol}: {e}")


async def _on_chain_balance(
    twak: TWAKProvider,
    target: SellTarget,
    wallet: str,
) -> float:
    """Re-read ERC-20 balance before selling."""
    if twak.chain != "bsc" or not wallet:
        return target.amount
    try:
        return await erc20_balance_of(twak.bsc_rpc_url, target.address, wallet)
    except Exception as e:
        logger.warning(f"On-chain refresh failed for {target.symbol}: {e}")
        return target.amount


async def _execute_sell_with_retries(
    twak: TWAKProvider,
    target: SellTarget,
    *,
    quote: str,
    quote_address: str,
    slippage_bps: int,
    wallet: str,
) -> Trade:
    """Try buffered, full-balance, and USD-notional sells until one succeeds."""
    from genesis.core.models import TradeStatus

    on_chain = await _on_chain_balance(twak, target, wallet)
    if on_chain <= 0:
        raise ValueError(f"No on-chain balance for {target.symbol}")

    if target.value_usd <= 0 and on_chain > 0:
        try:
            price = await twak.get_price_for_token(target.symbol, target.address)
            target.value_usd = on_chain * price
        except Exception:
            pass

    attempts: list[tuple[str, float, bool]] = [
        ("buffered", format_sell_amount(on_chain), False),
        ("full-balance", on_chain, False),
    ]
    if target.value_usd >= 0.01:
        attempts.append(("usd-notional", round(target.value_usd, 2), True))

    last_error = ""
    for label, amount, use_usd in attempts:
        if amount <= 0:
            continue
        logger.info(
            f"Selling {target.symbol} via {label}: "
            f"{'$' + str(amount) if use_usd else str(amount) + ' tokens'} → {quote}"
        )
        try:
            return await twak.swap(
                target.symbol,
                quote,
                amount,
                slippage_bps=slippage_bps,
                amount_is_usd=use_usd,
                from_address=target.address,
                to_address=quote_address,
                trade_side=Action.SELL,
            )
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Sell {target.symbol} ({label}) failed: {e}")

    raise RuntimeError(last_error or f"All sell strategies failed for {target.symbol}")


async def sell_all_to_usdt(
    twak: TWAKProvider,
    rules: RulesConfig,
    *,
    db: Database | None = None,
    min_usd: float = 0.0,
    dust_threshold_usd: float = DEFAULT_DUST_THRESHOLD_USD,
    slippage_bps: int | None = None,
    dry_run: bool = False,
    supplement_symbols: list[str] | None = None,
) -> tuple[list[SellTarget], list[Trade]]:
    """
    Sell every held token except BNB and stablecoins into USDT.

    Uses token-amount swaps (not --usd) to avoid slippage-induced reverts.
    """
    quote = rules.execution.default_quote
    slippage = slippage_bps if slippage_bps is not None else rules.risk.max_slippage_bps
    token_map = {t.symbol.upper(): t for t in rules.allowed_tokens}

    if supplement_symbols is None and db is not None:
        supplement_symbols = await db.get_traded_asset_symbols()
    supplement = twak.resolve_supplement_tokens(
        supplement_symbols or [],
        rules.allowed_tokens,
    )

    portfolio = await twak.get_portfolio(quote, supplement_tokens=supplement)
    wallet = await twak.get_wallet_address()

    scan_tokens = list({t.symbol.upper(): t for t in rules.allowed_tokens}.values())
    if supplement:
        for token in supplement:
            scan_tokens.append(token)
    on_chain_balances = await _fetch_on_chain_balances(twak, scan_tokens, wallet)

    targets = collect_sell_targets(
        portfolio,
        token_map,
        min_usd=min_usd,
        on_chain_balances=on_chain_balances,
    )
    await _enrich_target_values(twak, targets)

    if dry_run or not targets:
        return targets, []

    quote_token = token_map.get(quote.upper())
    quote_address = quote_token.address if quote_token else BSC_USDT_ADDRESS

    trades: list[Trade] = []
    for target in targets:
        from genesis.core.models import TradeStatus

        remaining = await _on_chain_balance(twak, target, wallet)
        if remaining > 0 and target.value_usd <= 0:
            try:
                price = await twak.get_price_for_token(target.symbol, target.address)
                target.value_usd = remaining * price
            except Exception:
                pass

        if is_unsellable_dust(target.value_usd, dust_threshold_usd):
            msg = (
                f"Below ${dust_threshold_usd:.2f} DEX minimum "
                f"(~${target.value_usd:.6f} — harmless dust, wallet is effectively clear)"
            )
            logger.info(f"Skipping {target.symbol}: {msg}")
            skipped = Trade(
                symbol=f"{target.symbol}/{quote}",
                side=Action.SELL,
                amount_usd=target.value_usd,
                amount_token=remaining,
                status=TradeStatus.SKIPPED,
                error=msg,
            )
            trades.append(skipped)
            continue

        try:
            trade = await _execute_sell_with_retries(
                twak,
                target,
                quote=quote,
                quote_address=quote_address,
                slippage_bps=slippage,
                wallet=wallet,
            )
            trades.append(trade)
            if db is not None:
                await db.save_trade(trade)
        except Exception as e:
            err = format_swap_error(e)
            logger.error(f"Sell failed for {target.symbol}: {err}")
            remaining = await _on_chain_balance(twak, target, wallet)
            if remaining > 0 and is_unsellable_dust(target.value_usd, dust_threshold_usd):
                msg = (
                    f"Below ${dust_threshold_usd:.2f} DEX minimum "
                    f"(~${target.value_usd:.6f} dust remains — safe to ignore)"
                )
                logger.info(f"{target.symbol}: {msg}")
                trades.append(
                    Trade(
                        symbol=f"{target.symbol}/{quote}",
                        side=Action.SELL,
                        amount_usd=target.value_usd,
                        amount_token=remaining,
                        status=TradeStatus.SKIPPED,
                        error=msg,
                    )
                )
                continue

            failed = Trade(
                symbol=f"{target.symbol}/{quote}",
                side=Action.SELL,
                amount_usd=target.value_usd,
                amount_token=remaining,
                status=TradeStatus.FAILED,
                error=err,
            )
            trades.append(failed)
            if db is not None:
                await db.save_trade(failed)
            if remaining > 0:
                logger.warning(
                    f"{target.symbol} dust remains: {remaining:.8f} "
                    f"(~${target.value_usd:.4f})"
                )

    return targets, trades