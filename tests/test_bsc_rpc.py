"""Tests for BSC on-chain RPC helpers."""

from __future__ import annotations

import pytest

from genesis.onchain.bsc_rpc import (
    decode_uint256,
    encode_address_param,
    erc20_balance_of,
    format_token_amount,
)


def test_encode_address_param():
    wallet = "0x000000000000000000000000000000000000dEaD"
    encoded = encode_address_param(wallet)
    assert encoded == "000000000000000000000000000000000000000000000000000000000000dead"


def test_decode_uint256():
    assert decode_uint256("0x0") == 0
    assert decode_uint256("0x") == 0
    assert decode_uint256("0x1") == 1
    # 7944.2 TAG with 18 decimals (approx raw)
    raw = int(7944.2 * 10**18)
    assert decode_uint256(hex(raw)) == raw


def test_format_token_amount():
    raw = int(7944.2 * 10**18)
    assert format_token_amount(raw, 18) == pytest.approx(7944.2, rel=1e-9)
    assert format_token_amount(0, 18) == 0.0


@pytest.mark.asyncio
async def test_erc20_balance_of_parses_rpc_response(monkeypatch):
    token = "0x208bf3e7da9639f1eaefa2de78c23396b0682025"
    wallet = "0x000000000000000000000000000000000000dEaD"
    raw = int(7944.2 * 10**18)

    async def fake_eth_call(rpc_url: str, to: str, data: str) -> str:
        if data.startswith("0x70a08231"):
            return hex(raw)
        if data == "0x313ce567":
            return hex(18)
        raise AssertionError(f"unexpected call: {data}")

    monkeypatch.setattr("genesis.onchain.bsc_rpc._eth_call", fake_eth_call)

    balance = await erc20_balance_of("https://rpc.test", token, wallet)
    assert balance == pytest.approx(7944.2, rel=1e-9)