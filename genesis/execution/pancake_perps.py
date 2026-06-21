"""PancakeSwap Perps (ApolloX Diamond) on BSC — calldata, pricing, and execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger
from web3 import Web3

from genesis.execution.twak_provider import BSC_USDT_ADDRESS

# ApolloX Diamond proxy (PancakeSwap Perps broker id = 2)
PANCAKE_PERPS_DIAMOND = Web3.to_checksum_address(
    "0x1b6f2d3844c6ae7d56ceb3c3643b9060ba28feb0"
)
PANCAKESWAP_BROKER_ID = 2
BSC_CHAIN_ID = 56
MIN_NOTIONAL_USD = 200.0

# pairBase addresses on BSC — must match ApolloX/PancakeSwap Perps markets
PAIR_BASE_BY_SYMBOL: dict[str, str] = {
    "BTC": "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
    "BTCB": "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
    "ETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "BNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "ASTER": "0x000Ae314E2A2172a039B26378814C252734f556A",
    "XRP": "0x1D2F0da169ceB9fC7B3144628dB156f3F6c60aBE",
    "TRX": "0xCE7de646e7208a4Ef112cb6ed5038FA6cC6b12e3",
    "UNI": "0xBf5140A22578168FD562DCcF235E5D43A02ce9B1",
}

MARKET_SYMBOL_BY_ASSET: dict[str, str] = {
    "BTC": "BTCUSD",
    "BTCB": "BTCUSD",
    "ETH": "ETHUSD",
    "BNB": "BNBUSD",
    "WBNB": "BNBUSD",
    "ASTER": "ASTERUSD",
    "XRP": "XRPUSD",
    "TRX": "TRXUSD",
    "UNI": "UNIUSD",
}

# Config allowlist names (TWT has no ApolloX perps market yet)
PERPS_CONFIG_SYMBOLS = frozenset(
    {"TWT", "UNI", "ETH", "ASTER", "XRP", "TRX", "BNB", "BTC", "BTCB"}
)

COLLATERAL_BY_SYMBOL: dict[str, str] = {
    "USDT": BSC_USDT_ADDRESS,
    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
}

OPEN_DATA_INPUT_TYPE = (
    "tuple(address,bool,address,uint256,uint256,uint256,uint256,uint256,uint256)"
)

OPEN_MARKET_TRADE_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "pairBase", "type": "address"},
                    {"name": "isLong", "type": "bool"},
                    {"name": "tokenIn", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "qty", "type": "uint256"},
                    {"name": "price", "type": "uint256"},
                    {"name": "stopLoss", "type": "uint256"},
                    {"name": "takeProfit", "type": "uint256"},
                    {"name": "broker", "type": "uint256"},
                ],
                "name": "data",
                "type": "tuple",
            }
        ],
        "name": "openMarketTrade",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

CLOSE_TRADE_ABI = [
    {
        "inputs": [{"name": "tradeHash", "type": "bytes32"}],
        "name": "closeTrade",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

ERC20_APPROVE_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

APOLLOX_PRICE_URL = "https://fapi.apollox.finance/fapi/v1/premiumIndex"


@dataclass
class PerpsOpenParams:
    """Resolved parameters for openMarketTrade."""

    pair_base: str
    market_symbol: str
    is_long: bool
    token_in: str
    amount_in_wei: int
    qty: int
    acceptable_price: int
    leverage: int
    index_price: float
    notional_usd: float


def resolve_perps_market(symbol: str) -> tuple[str, str]:
    """Map Genesis asset symbol to (pairBase, ApolloX market symbol)."""
    key = symbol.upper()
    pair_base = PAIR_BASE_BY_SYMBOL.get(key)
    market = MARKET_SYMBOL_BY_ASSET.get(key)
    if not pair_base or not market:
        raise ValueError(
            f"No PancakeSwap Perps market for {symbol}. "
            f"Supported: {', '.join(sorted(set(MARKET_SYMBOL_BY_ASSET)))}"
        )
    try:
        pair_checksum = Web3.to_checksum_address(pair_base)
    except ValueError as exc:
        raise ValueError(f"Invalid pairBase for {symbol}: {pair_base}") from exc
    return pair_checksum, market


def compute_qty(notional_usd: float, price_1e8: int) -> int:
    """Compute on-wire qty (1e10 scale) from USD notional and index price (1e8)."""
    if price_1e8 <= 0 or notional_usd <= 0:
        raise ValueError("notional and price must be positive")
    return int(notional_usd * 1e18 / price_1e8)


def compute_acceptable_price(index_price: float, is_long: bool, slippage_bps: int) -> int:
    """Worst acceptable fill price (1e8) for market open."""
    slip = slippage_bps / 10_000
    bound = index_price * (1 + slip) if is_long else index_price * (1 - slip)
    return int(bound * 1e8)


async def fetch_index_price(market_symbol: str) -> float:
    """Fetch ApolloX index/mark price for a perps market (e.g. BNBUSD)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(APOLLOX_PRICE_URL)
        response.raise_for_status()
        rows: list[dict[str, Any]] = response.json()

    for row in rows:
        if str(row.get("symbol", "")).upper() == market_symbol.upper():
            for key in ("indexPrice", "markPrice"):
                raw = row.get(key)
                if raw is not None:
                    return float(raw)

    raise RuntimeError(f"ApolloX price not found for {market_symbol}")


def build_open_params(
    symbol: str,
    *,
    notional_usd: float,
    leverage: int,
    is_long: bool,
    index_price: float,
    slippage_bps: int,
    collateral_symbol: str = "USDT",
) -> PerpsOpenParams:
    """Build validated openMarketTrade inputs."""
    if notional_usd < MIN_NOTIONAL_USD:
        raise ValueError(
            f"PancakeSwap Perps min notional is ${MIN_NOTIONAL_USD:.0f}; "
            f"requested ${notional_usd:.2f}"
        )

    pair_base, market = resolve_perps_market(symbol)
    collateral = COLLATERAL_BY_SYMBOL.get(collateral_symbol.upper())
    if not collateral:
        raise ValueError(f"Unsupported perps collateral: {collateral_symbol}")

    lev = max(1, leverage)
    margin_usd = notional_usd / lev
    amount_in_wei = int(margin_usd * 1e18)  # USDT/USDC 18 decimals on BSC
    price_1e8 = int(index_price * 1e8)
    qty = compute_qty(notional_usd, price_1e8)
    acceptable = compute_acceptable_price(index_price, is_long, slippage_bps)

    return PerpsOpenParams(
        pair_base=pair_base,
        market_symbol=market,
        is_long=is_long,
        token_in=Web3.to_checksum_address(collateral),
        amount_in_wei=amount_in_wei,
        qty=qty,
        acceptable_price=acceptable,
        leverage=lev,
        index_price=index_price,
        notional_usd=notional_usd,
    )


def _w3(rpc_url: str) -> Web3:
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))


def _build_open_data(params: PerpsOpenParams) -> tuple[Any, ...]:
    return (
        params.pair_base,
        params.is_long,
        params.token_in,
        params.amount_in_wei,
        params.qty,
        params.acceptable_price,
        0,
        0,
        PANCAKESWAP_BROKER_ID,
    )


def build_approve_tx(
    rpc_url: str,
    token_address: str,
    owner: str,
    spender: str,
    amount_wei: int,
) -> dict[str, Any]:
    """Build unsigned ERC-20 approve transaction."""
    w3 = _w3(rpc_url)
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_APPROVE_ABI,
    )
    return token.functions.approve(
        Web3.to_checksum_address(spender),
        amount_wei,
    ).build_transaction(
        {
            "from": Web3.to_checksum_address(owner),
            "nonce": w3.eth.get_transaction_count(owner),
            "gas": 80_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": BSC_CHAIN_ID,
        }
    )


def build_open_market_trade_tx(
    rpc_url: str,
    wallet: str,
    params: PerpsOpenParams,
) -> dict[str, Any]:
    """Build unsigned openMarketTrade transaction."""
    w3 = _w3(rpc_url)
    contract = w3.eth.contract(address=PANCAKE_PERPS_DIAMOND, abi=OPEN_MARKET_TRADE_ABI)
    open_data = _build_open_data(params)
    return contract.functions.openMarketTrade(open_data).build_transaction(
        {
            "from": Web3.to_checksum_address(wallet),
            "nonce": w3.eth.get_transaction_count(wallet),
            "gas": 600_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": BSC_CHAIN_ID,
        }
    )


def build_close_trade_tx(
    rpc_url: str,
    wallet: str,
    trade_hash: str,
) -> dict[str, Any]:
    """Build unsigned closeTrade(bytes32) transaction."""
    w3 = _w3(rpc_url)
    contract = w3.eth.contract(address=PANCAKE_PERPS_DIAMOND, abi=CLOSE_TRADE_ABI)
    hash_bytes = Web3.to_bytes(hexstr=trade_hash)
    return contract.functions.closeTrade(hash_bytes).build_transaction(
        {
            "from": Web3.to_checksum_address(wallet),
            "nonce": w3.eth.get_transaction_count(wallet),
            "gas": 400_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": BSC_CHAIN_ID,
        }
    )


def sign_and_send_tx(rpc_url: str, account: Any, tx: dict[str, Any]) -> str:
    """Sign and broadcast a transaction; return tx hash hex."""
    w3 = _w3(rpc_url)
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.get("status") != 1:
        raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
    return tx_hash.hex()


async def ensure_collateral_allowance(
    rpc_url: str,
    account: Any,
    token_address: str,
    spender: str,
    amount_wei: int,
) -> str | None:
    """Approve collateral if current allowance is insufficient."""
    w3 = _w3(rpc_url)
    owner = account.address
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_APPROVE_ABI
        + [
            {
                "inputs": [
                    {"name": "owner", "type": "address"},
                    {"name": "spender", "type": "address"},
                ],
                "name": "allowance",
                "outputs": [{"type": "uint256"}],
                "stateMutability": "view",
                "type": "function",
            }
        ],
    )

    def _check_and_approve() -> str | None:
        current = token.functions.allowance(owner, Web3.to_checksum_address(spender)).call()
        if current >= amount_wei:
            return None
        approve_tx = build_approve_tx(rpc_url, token_address, owner, spender, amount_wei)
        approve_tx["nonce"] = w3.eth.get_transaction_count(owner)
        return sign_and_send_tx(rpc_url, account, approve_tx)

    return await asyncio.to_thread(_check_and_approve)


async def open_perps_position(
    rpc_url: str,
    account: Any,
    params: PerpsOpenParams,
) -> str:
    """Approve collateral (if needed) and submit openMarketTrade."""
    approve_hash = await ensure_collateral_allowance(
        rpc_url,
        account,
        params.token_in,
        PANCAKE_PERPS_DIAMOND,
        params.amount_in_wei,
    )
    if approve_hash:
        logger.info(f"Perps collateral approved: {approve_hash}")

    def _open() -> str:
        tx = build_open_market_trade_tx(rpc_url, account.address, params)
        w3 = _w3(rpc_url)
        tx["nonce"] = w3.eth.get_transaction_count(account.address)
        return sign_and_send_tx(rpc_url, account, tx)

    return await asyncio.to_thread(_open)


async def close_perps_position(rpc_url: str, account: Any, trade_hash: str) -> str:
    """Submit closeTrade for an existing perps position."""

    def _close() -> str:
        tx = build_close_trade_tx(rpc_url, account.address, trade_hash)
        w3 = _w3(rpc_url)
        tx["nonce"] = w3.eth.get_transaction_count(account.address)
        return sign_and_send_tx(rpc_url, account, tx)

    return await asyncio.to_thread(_close)