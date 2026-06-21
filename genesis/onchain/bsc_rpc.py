"""BSC JSON-RPC helpers for on-chain ERC-20 balance reads."""

from __future__ import annotations

from typing import Any

import httpx

BALANCE_OF_SELECTOR = "0x70a08231"
DECIMALS_SELECTOR = "0x313ce567"
DEFAULT_BSC_RPC = "https://bsc-dataseed.binance.org/"


def encode_address_param(address: str) -> str:
    """ABI-encode an address argument (32-byte word, no 0x prefix on payload)."""
    clean = address.lower().removeprefix("0x")
    if len(clean) != 40:
        raise ValueError(f"Invalid address: {address}")
    return "0" * 24 + clean


def decode_uint256(hex_value: str) -> int:
    """Decode a 32-byte uint256 from eth_call result."""
    if not hex_value or hex_value in {"0x", "0x0"}:
        return 0
    return int(hex_value, 16)


def format_token_amount(raw: int, decimals: int) -> float:
    """Convert raw token units to human-readable float."""
    if raw <= 0:
        return 0.0
    return raw / (10**decimals)


async def _eth_call(rpc_url: str, to: str, data: str) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(rpc_url, json=payload)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        if "error" in body:
            raise RuntimeError(body["error"])
        result = body.get("result", "0x0")
        return str(result)


async def erc20_decimals(rpc_url: str, token_address: str) -> int:
    """Read ERC-20 decimals(); defaults to 18 if the call fails."""
    try:
        raw = await _eth_call(rpc_url, token_address, DECIMALS_SELECTOR)
        value = decode_uint256(raw)
        return value if 0 < value <= 36 else 18
    except Exception:
        return 18


async def erc20_balance_of(
    rpc_url: str,
    token_address: str,
    wallet_address: str,
    decimals: int | None = None,
) -> float:
    """Return wallet ERC-20 balance in token units (not raw wei)."""
    data = BALANCE_OF_SELECTOR + encode_address_param(wallet_address)
    raw_hex = await _eth_call(rpc_url, token_address, data)
    raw_amount = decode_uint256(raw_hex)
    if raw_amount <= 0:
        return 0.0
    token_decimals = decimals if decimals is not None else await erc20_decimals(rpc_url, token_address)
    return format_token_amount(raw_amount, token_decimals)