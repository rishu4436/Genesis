"""Trust Wallet Agent Kit (TWAK) async CLI wrapper — real @trustwallet/cli API."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
from typing import Any

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from genesis.core.models import Action, PortfolioSnapshot, Position, TokenConfig, Trade, TradeStatus
from genesis.onchain.bsc_rpc import DEFAULT_BSC_RPC, erc20_balance_of
from genesis.utils import safe_json_loads, truncate_address

# Genesis network → TWAK --chain key (see: twak chains --json)
BSC_USDT_ADDRESS = "0x55d398326f99059fF775485246999027B3197955"

NETWORK_TO_CHAIN: dict[str, str] = {
    "bsc-mainnet": "bsc",
    "bsc-testnet": "bsctestnet",
    "ethereum-mainnet": "ethereum",
}

_SENSITIVE_CLI_FLAGS = frozenset({"--password", "-p"})
_SENSITIVE_ENV_EXPORT = re.compile(
    r"(TWAK_WALLET_PASSWORD=)(?:'[^']*'|\"[^\"]*\"|[^\s;]+)"
)


def _safe_cmd_repr(cmd: list[str]) -> str:
    """Redact wallet passwords before logging subprocess commands."""
    redacted: list[str] = []
    skip_next = False
    for part in cmd:
        if skip_next:
            redacted.append("***")
            skip_next = False
            continue
        if part in _SENSITIVE_CLI_FLAGS:
            redacted.append(part)
            skip_next = True
            continue
        if "TWAK_WALLET_PASSWORD=" in part:
            part = _SENSITIVE_ENV_EXPORT.sub(r"\1***", part)
        redacted.append(part)
    return " ".join(redacted)


class TWAKError(Exception):
    """TWAK CLI execution error."""

    def __init__(self, message: str, stderr: str = "", returncode: int = -1):
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


class TWAKProvider:
    """
    Async wrapper around the official TWAK CLI (`npm i -g @trustwallet/cli`).

    Docs: https://github.com/trustwallet/tw-agent-skills (skills/wallet/references/)
    """

    def __init__(
        self,
        cli_path: str = "twak",
        network: str = "bsc-mainnet",
        wallet_mode: str = "autonomous",
        chain: str | None = None,
        wallet_password: str = "",
        use_wsl: bool = False,
        wsl_init: str = "source ~/.nvm/nvm.sh 2>/dev/null || true",
        bsc_rpc_url: str = DEFAULT_BSC_RPC,
    ) -> None:
        self.cli_path = cli_path
        self.network = network
        self.wallet_mode = wallet_mode
        self.chain = chain or NETWORK_TO_CHAIN.get(network, "bsc")
        self.wallet_password = wallet_password or os.getenv("TWAK_WALLET_PASSWORD", "")
        self.use_wsl = use_wsl
        self.wsl_init = wsl_init
        self.bsc_rpc_url = bsc_rpc_url or DEFAULT_BSC_RPC
        self._wallet_address: str | None = None

    @classmethod
    def from_env(cls, env: Any) -> "TWAKProvider":
        """Construct from EnvSettings."""
        return cls(
            cli_path=env.twak_cli_path,
            network=env.twak_network,
            wallet_mode=env.twak_wallet_mode,
            chain=getattr(env, "twak_chain", None) or NETWORK_TO_CHAIN.get(env.twak_network, "bsc"),
            wallet_password=getattr(env, "twak_wallet_password", "") or env.wallet_password,
            use_wsl=getattr(env, "twak_use_wsl", False),
            bsc_rpc_url=getattr(env, "bsc_rpc_url", DEFAULT_BSC_RPC),
        )

    async def verify_installation(self) -> bool:
        """Check TWAK CLI is installed and accessible."""
        if self.use_wsl:
            try:
                await self._run("auth", "status", json_output=True)
                return True
            except TWAKError:
                return False
        return shutil.which(self.cli_path) is not None

    def _build_command(self, *args: str, json_output: bool = True) -> list[str]:
        """Build subprocess command, optionally wrapping via WSL."""
        twak_args: list[str] = [self.cli_path, *args]
        if json_output:
            twak_args.append("--json")

        if self.use_wsl:
            inner = " ".join(shlex.quote(a) for a in twak_args)
            exports = ""
            if self.wallet_password:
                exports += f"export TWAK_WALLET_PASSWORD={shlex.quote(self.wallet_password)}; "
            script = f"{exports}{self.wsl_init}; {inner}"
            return ["wsl", "bash", "-lc", script]

        return twak_args

    def _subprocess_env(self) -> dict[str, str]:
        """Pass wallet password via env (never CLI args)."""
        env = os.environ.copy()
        if self.wallet_password:
            env["TWAK_WALLET_PASSWORD"] = self.wallet_password
        return env

    async def _run(
        self,
        *args: str,
        json_output: bool = True,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Execute TWAK CLI command and parse JSON output (single attempt)."""
        cmd = self._build_command(*args, json_output=json_output)
        logger.debug(f"TWAK: {_safe_cmd_repr(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._subprocess_env(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            stdout_text = stdout.decode().strip()
            stderr_text = stderr.decode().strip()

            if json_output and stdout_text:
                parsed = safe_json_loads(stdout_text)
                if isinstance(parsed, dict):
                    if "error" in parsed:
                        raise TWAKError(
                            parsed.get("error", "TWAK error"),
                            stderr=stderr_text,
                            returncode=proc.returncode or 1,
                        )
                    if proc.returncode != 0:
                        raise TWAKError(
                            f"TWAK command failed: {' '.join(args)}",
                            stderr=stderr_text,
                            returncode=proc.returncode or -1,
                        )
                    return parsed
                if isinstance(parsed, list):
                    return {"data": parsed}

            if proc.returncode != 0:
                raise TWAKError(
                    f"TWAK command failed: {' '.join(args)}",
                    stderr=stderr_text or stdout_text,
                    returncode=proc.returncode or -1,
                )

            return {"raw": stdout_text, "stderr": stderr_text}

        except asyncio.TimeoutError:
            raise TWAKError(f"TWAK command timed out: {' '.join(args)}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _run_retry(
        self,
        *args: str,
        json_output: bool = True,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Execute TWAK CLI with retries (live swaps / wallet ops)."""
        return await self._run(*args, json_output=json_output, timeout=timeout)

    async def setup_autonomous_wallet(self) -> dict[str, Any]:
        """Create TWAK HD wallet (skip if already exists)."""
        status = await self._run("wallet", "status")
        if status.get("agentWallet") == "configured":
            logger.info("TWAK wallet already configured")
            addr = await self.get_wallet_address()
            return {"existing": True, "address": addr}

        if not self.wallet_password:
            raise TWAKError(
                "TWAK_WALLET_PASSWORD or WALLET_PASSWORD required for wallet create"
            )

        result = await self._run("wallet", "create", "--password", self.wallet_password)
        await self._run("wallet", "keychain", "save", "--password", self.wallet_password)
        await self._run("wallet", "register")
        self._wallet_address = await self.get_wallet_address()
        logger.info(f"TWAK wallet ready: {truncate_address(self._wallet_address or 'unknown')}")
        return result

    async def get_wallet_address(self) -> str:
        """Get BSC wallet address."""
        if self._wallet_address:
            return self._wallet_address
        result = await self._run("wallet", "address", "--chain", self.chain)
        self._wallet_address = result.get("address", "")
        return self._wallet_address

    async def get_balance(self, token: str = "BNB") -> float:
        """Get native or token balance on configured chain."""
        result = await self._run("wallet", "balance", "--chain", self.chain)
        if token.upper() in (result.get("symbol", ""), "BNB", "ETH"):
            return float(result.get("available", result.get("total", 0)))
        for t in result.get("tokens", []):
            if t.get("symbol", "").upper() == token.upper():
                return float(t.get("balance", 0))
        return 0.0

    async def get_price(self, pair: str) -> float:
        """Get token price in USD (pair like BNB/USDT → BNB)."""
        token = pair.split("/")[0].strip()
        result = await self._run("price", token, "--chain", self.chain)
        return float(result.get("priceUsd", 0))

    async def get_price_for_token(self, symbol: str, address: str = "") -> float:
        """Resolve USD price by symbol, falling back to contract address."""
        try:
            return await self.get_price(symbol)
        except TWAKError:
            if address:
                result = await self._run("price", address, "--chain", self.chain)
                return float(result.get("priceUsd", 0))
            raise

    @staticmethod
    def resolve_supplement_tokens(
        symbols: list[str],
        allowed_tokens: list[TokenConfig],
    ) -> list[TokenConfig]:
        """Map traded symbols to allowlist TokenConfig entries for on-chain reads."""
        by_symbol = {t.symbol.upper(): t for t in allowed_tokens}
        seen: set[str] = set()
        resolved: list[TokenConfig] = []
        for symbol in symbols:
            key = symbol.upper()
            if key in seen:
                continue
            token = by_symbol.get(key)
            if token:
                resolved.append(token)
                seen.add(key)
        return resolved

    @staticmethod
    def _parse_portfolio_holdings(result: Any) -> list[dict[str, Any]]:
        """Normalize TWAK portfolio payloads (flat data[] or legacy chain summary)."""
        holdings: list[dict[str, Any]] = []

        if isinstance(result, list):
            entries = result
        elif isinstance(result, dict):
            entries = result.get("data") or result.get("chains") or [result]
        else:
            return holdings

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            # New format: one row per asset in data[]
            if entry.get("usdValue") is not None or entry.get("balance") is not None:
                symbol = str(entry.get("symbol", ""))
                amount = float(entry.get("balance", entry.get("available", entry.get("total", 0))) or 0)
                usd_value = float(entry.get("usdValue", entry.get("totalUsd", 0)) or 0)
                if amount > 0 or usd_value > 0:
                    holdings.append(
                        {
                            "symbol": symbol,
                            "balance": amount,
                            "usd_value": usd_value or amount,
                        }
                    )
                continue

            # Legacy format: chain summary + nested tokens[]
            chain_usd = float(entry.get("totalUsd") or 0)
            symbol = str(entry.get("symbol", ""))
            amount = float(entry.get("available", entry.get("total", 0)) or 0)
            if amount > 0 or chain_usd > 0:
                holdings.append(
                    {
                        "symbol": symbol,
                        "balance": amount,
                        "usd_value": chain_usd,
                    }
                )
            for tok in entry.get("tokens", []):
                if not isinstance(tok, dict):
                    continue
                bal = float(tok.get("balance", 0) or 0)
                if bal <= 0:
                    continue
                holdings.append(
                    {
                        "symbol": str(tok.get("symbol", "")),
                        "balance": bal,
                        "usd_value": bal,
                    }
                )

        return holdings

    async def _supplement_onchain_positions(
        self,
        positions: list[Position],
        total_usd: float,
        supplement_tokens: list[TokenConfig],
        quote_token: str,
        price_resolver: Any | None = None,
    ) -> tuple[list[Position], float]:
        """Add positions TWAK omits by reading ERC-20 balances on BSC."""
        if not supplement_tokens or self.chain != "bsc":
            return positions, total_usd

        wallet = await self.get_wallet_address()
        if not wallet:
            return positions, total_usd

        existing = {p.symbol.upper(): p for p in positions}
        quote_upper = quote_token.upper()

        for token in supplement_tokens:
            sym = token.symbol.upper()
            if sym == quote_upper:
                continue

            current = existing.get(sym)

            try:
                balance = await erc20_balance_of(
                    self.bsc_rpc_url,
                    token.address,
                    wallet,
                )
            except Exception as e:
                logger.warning(f"On-chain balance fetch failed for {token.symbol}: {e}")
                continue

            if balance <= 0:
                if current:
                    total_usd -= current.amount * (current.current_price or 0.0)
                    positions = [p for p in positions if p.symbol.upper() != sym]
                    existing.pop(sym, None)
                    logger.debug(f"On-chain supplement: removed stale {token.symbol} (zero balance)")
                continue

            price = 0.0
            source = "twak"
            if price_resolver is not None:
                try:
                    price, source = await price_resolver.usd_price_for_token(token)
                except Exception as e:
                    logger.warning(f"Price resolver failed for {token.symbol}: {e}")
            else:
                try:
                    price = await self.get_price_for_token(token.symbol, token.address)
                except Exception as e:
                    logger.warning(f"Price fetch failed for {token.symbol}: {e}")

            usd_value = balance * price
            if current:
                total_usd -= current.amount * (current.current_price or 0.0)
                positions = [p for p in positions if p.symbol.upper() != sym]

            positions.append(
                Position(
                    symbol=token.symbol,
                    amount=balance,
                    entry_price=0.0,
                    current_price=price,
                )
            )
            total_usd += usd_value
            existing[sym] = positions[-1]
            logger.debug(
                f"On-chain supplement: {token.symbol} balance={balance:.4f} "
                f"price=${price:.8f} ({source})"
            )

        return positions, total_usd

    async def get_portfolio(
        self,
        quote_token: str = "USDT",
        supplement_tokens: list[TokenConfig] | None = None,
        price_resolver: Any | None = None,
    ) -> PortfolioSnapshot:
        """Fetch BSC portfolio via TWAK, optionally supplementing traded tokens on-chain."""
        result = await self._run("wallet", "portfolio", "--chains", self.chain)
        holdings = self._parse_portfolio_holdings(result)

        positions: list[Position] = []
        total_usd = 0.0
        quote_available = 0.0
        stable_symbols = {"USDT", "USDC"}

        for holding in holdings:
            symbol = holding["symbol"]
            amount = holding["balance"]
            usd_value = holding["usd_value"]
            total_usd += usd_value

            sym_upper = symbol.upper()
            if sym_upper in stable_symbols:
                quote_available += usd_value or amount
            elif sym_upper == quote_token.upper():
                quote_available += usd_value or amount

            if amount > 0:
                positions.append(
                    Position(
                        symbol=symbol,
                        amount=amount,
                        entry_price=0.0,
                        current_price=usd_value / amount if amount else 0.0,
                    )
                )

        if supplement_tokens:
            positions, total_usd = await self._supplement_onchain_positions(
                positions,
                total_usd,
                supplement_tokens,
                quote_token,
                price_resolver=price_resolver,
            )

        if quote_available <= 0:
            quote_available = await self.get_balance(quote_token)

        return PortfolioSnapshot(
            total_value_usd=total_usd,
            available_usd=quote_available,
            positions=positions,
        )

    def _bps_to_slippage_pct(self, slippage_bps: int) -> str:
        """TWAK uses slippage percent, Genesis rules use basis points."""
        return str(max(0.01, slippage_bps / 100.0))

    @staticmethod
    def _parse_swap_leg(value: Any) -> tuple[float, str]:
        """Parse TWAK swap leg like '2844.96 TAG' or '3.00 USDT'."""
        if value is None:
            return 0.0, ""
        text = str(value).strip()
        if not text:
            return 0.0, ""
        parts = text.rsplit(maxsplit=1)
        if len(parts) != 2:
            return 0.0, text
        try:
            return float(parts[0].replace(",", "")), parts[1]
        except ValueError:
            return 0.0, parts[1]

    def _swap_trade_metrics(
        self,
        result: dict[str, Any],
        *,
        from_token: str,
        to_token: str,
        amount_usd: float,
        buying_asset: bool,
    ) -> tuple[float | None, float | None]:
        """Derive token amount and entry/exit price from TWAK swap JSON."""
        amount_in, symbol_in = self._parse_swap_leg(result.get("input"))
        amount_out, symbol_out = self._parse_swap_leg(result.get("output"))

        if buying_asset:
            amount_token = amount_out or float(result.get("outputAmount", 0) or 0)
            spent_usd = amount_usd or amount_in
        else:
            amount_token = amount_in or float(result.get("inputAmount", 0) or 0)
            spent_usd = amount_out or amount_usd

        if amount_token and spent_usd:
            return amount_token, spent_usd / amount_token

        # Fallback: numeric fields some TWAK versions return
        if buying_asset:
            qty = float(result.get("toAmount", result.get("amountOut", 0)) or 0)
            usd = float(result.get("amountUsd", result.get("usdValue", amount_usd)) or 0)
        else:
            qty = float(result.get("fromAmount", result.get("amountIn", 0)) or 0)
            usd = float(result.get("amountUsd", result.get("usdValue", amount_usd)) or 0)
        if qty and usd:
            return qty, usd / qty
        return None, None

    @staticmethod
    def _is_quote_token(ref: str) -> bool:
        return ref.upper() in {"USDT", "USDC"} or ref.lower() == BSC_USDT_ADDRESS.lower()

    async def swap(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        slippage_bps: int = 50,
        amount_is_usd: bool = False,
        from_address: str | None = None,
        to_address: str | None = None,
        trade_side: Action | None = None,
    ) -> Trade:
        """Execute spot swap on BSC via TWAK routing (incl. PancakeSwap)."""
        slippage = self._bps_to_slippage_pct(slippage_bps)
        from_ref = from_address or from_token
        to_ref = to_address or to_token

        if amount_is_usd:
            result = await self._run_retry(
                "swap",
                from_ref,
                to_ref,
                "--chain",
                self.chain,
                "--usd",
                str(amount),
                "--slippage",
                slippage,
            )
        else:
            result = await self._run_retry(
                "swap",
                str(amount),
                from_ref,
                to_ref,
                "--chain",
                self.chain,
                "--slippage",
                slippage,
            )

        return self._build_trade_from_swap_result(
            result,
            from_token=from_token,
            to_token=to_token,
            amount=amount,
            slippage_bps=slippage_bps,
            amount_is_usd=amount_is_usd,
            trade_side=trade_side,
        )

    async def quote_swap(
        self,
        from_token: str,
        to_token: str,
        amount_usd: float,
        slippage_bps: int = 50,
        from_address: str | None = None,
        to_address: str | None = None,
    ) -> dict[str, Any]:
        """TWAK quote-only — returns route JSON or raises TWAKError (no retries)."""
        slippage = self._bps_to_slippage_pct(slippage_bps)
        from_ref = from_address or from_token
        to_ref = to_address or to_token
        return await self._run(
            "swap",
            from_ref,
            to_ref,
            "--chain",
            self.chain,
            "--usd",
            str(amount_usd),
            "--slippage",
            slippage,
            "--quote-only",
        )

    def _build_trade_from_swap_result(
        self,
        result: dict[str, Any],
        *,
        from_token: str,
        to_token: str,
        amount: float,
        slippage_bps: int,
        amount_is_usd: bool,
        trade_side: Action | None,
    ) -> Trade:
        tx_hash = result.get("hash", result.get("tx_hash", result.get("transactionHash")))
        from_is_quote = self._is_quote_token(from_token)
        buying_asset = from_is_quote and not self._is_quote_token(to_token)
        side = trade_side or (Action.BUY if buying_asset else Action.SELL)
        spent_usd = amount if amount_is_usd else 0.0
        amount_token, price = self._swap_trade_metrics(
            result,
            from_token=from_token,
            to_token=to_token,
            amount_usd=spent_usd,
            buying_asset=side == Action.BUY,
        )
        if not spent_usd and amount_token and price:
            spent_usd = amount_token * price

        return Trade(
            symbol=f"{from_token}/{to_token}",
            side=side,
            amount_usd=spent_usd,
            amount_token=amount_token,
            price=price,
            tx_hash=tx_hash,
            slippage_bps=slippage_bps,
            status=TradeStatus.CONFIRMED if tx_hash else TradeStatus.SUBMITTED,
        )

    async def execute_trade(
        self,
        decision_action: Action,
        asset: str,
        amount_usd: float,
        quote_token: str = "USDT",
        slippage_bps: int = 50,
        asset_address: str | None = None,
        quote_address: str | None = None,
    ) -> Trade:
        """Execute trade based on decision action."""
        quote_ref = quote_address or (
            BSC_USDT_ADDRESS if quote_token.upper() == "USDT" and self.chain == "bsc" else quote_token
        )
        asset_ref = asset_address or asset

        if decision_action == Action.BUY:
            return await self.swap(
                quote_token,
                asset,
                amount_usd,
                slippage_bps,
                amount_is_usd=True,
                from_address=quote_ref,
                to_address=asset_ref,
                trade_side=Action.BUY,
            )
        if decision_action == Action.SELL:
            return await self.swap(
                asset,
                quote_token,
                amount_usd,
                slippage_bps,
                amount_is_usd=True,
                from_address=asset_ref,
                to_address=quote_ref,
                trade_side=Action.SELL,
            )
        raise ValueError("Cannot execute HOLD action")

    async def x402_request(
        self,
        url: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        max_payment: str = "10000000000000000",
        prefer_network: str = "bsc",
        auto_approve: bool = True,
    ) -> dict[str, Any]:
        """
        HTTP request to an x402-gated endpoint via TWAK.

        TWAK handles 402 Payment Required, signs authorization, and retries.
        Default max_payment=10000 is 0.01 USDC (6 decimals).
        """
        args = [
            "x402",
            "request",
            url,
            "--method",
            method.upper(),
            "--max-payment",
            max_payment,
            "--prefer-network",
            prefer_network,
        ]
        if body is not None:
            args.extend(["--body", json.dumps(body, separators=(",", ":"))])
        if auto_approve:
            args.append("--yes")

        result = await self._run(*args, timeout=120.0)

        if "jsonrpc" in result:
            return result

        raw = result.get("raw", "")
        if raw:
            parsed = self._parse_x402_stdout(raw)
            if parsed:
                return parsed

        return result

    @staticmethod
    def _parse_x402_stdout(stdout: str) -> dict[str, Any] | None:
        """Extract JSON-RPC body from twak x402 request stdout."""
        lines = stdout.strip().splitlines()
        json_start = None
        for i, line in enumerate(lines):
            if line.strip().startswith("{"):
                json_start = i
                break
        if json_start is None:
            return None
        blob = "\n".join(lines[json_start:])
        parsed = safe_json_loads(blob)
        return parsed if isinstance(parsed, dict) else None

    def _chain_flag(self) -> list[str]:
        return ["--chain", self.chain]

    async def erc8004_register(
        self,
        uri: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Mint ERC-8004 agent identity via TWAK (same wallet as swaps/x402)."""
        args = ["erc8004", "register", "--uri", uri, *self._chain_flag()]
        for key, value in (metadata or {}).items():
            args.extend(["--metadata", f"{key}={value}"])
        result = await self._run(*args, timeout=180.0)
        logger.info(
            f"ERC-8004 registered via TWAK: agentId={result.get('agentId', '?')}"
        )
        return result

    async def erc8004_show(self, agent_id: str | int) -> dict[str, Any]:
        """Fetch on-chain ERC-8004 identity state."""
        return await self._run("erc8004", "show", str(agent_id), *self._chain_flag())

    async def erc8004_set_uri(self, agent_id: str | int, uri: str) -> dict[str, Any]:
        """Update agentURI on an existing ERC-8004 identity."""
        return await self._run(
            "erc8004",
            "set-uri",
            str(agent_id),
            "--uri",
            uri,
            *self._chain_flag(),
        )

    async def erc8004_set_metadata(
        self,
        agent_id: str | int,
        key: str,
        value: str,
    ) -> dict[str, Any]:
        """Set metadata on ERC-8004 identity."""
        return await self._run(
            "erc8004",
            "set-metadata",
            str(agent_id),
            "--key",
            key,
            "--value",
            value,
            *self._chain_flag(),
        )

    async def compete_register(self) -> dict[str, Any]:
        """Register agent wallet for BNB HACK live trading competition."""
        result = await self._run("compete", "register", timeout=180.0)
        logger.info("Competition registration submitted via TWAK")
        return result

    async def compete_status(self) -> dict[str, Any]:
        """Check hackathon competition registration status."""
        return await self._run("compete", "status")

    async def execute_x402_payment(
        self,
        recipient: str,
        amount: str,
        resource: str = "",
    ) -> dict[str, Any]:
        """Execute standalone x402 micropayment via TWAK."""
        args = ["x402", "pay", "--recipient", recipient, "--amount", amount]
        if resource:
            args.extend(["--resource", resource])
        result = await self._run(*args)
        logger.info(f"x402 payment sent to {truncate_address(recipient)}")
        return result

    async def simulate_swap(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        *,
        from_address: str | None = None,
        to_address: str | None = None,
        slippage_bps: int = 50,
    ) -> Trade:
        """Quote-only swap for backtest mode."""
        result = await self.quote_swap(
            from_token,
            to_token,
            amount,
            slippage_bps=slippage_bps,
            from_address=from_address,
            to_address=to_address,
        )
        return Trade(
            symbol=f"{from_token}/{to_token}",
            side=Action.BUY,
            amount_usd=amount,
            price=float(result.get("priceImpact", 0) or 0),
            status=TradeStatus.SIMULATED,
            simulated=True,
        )