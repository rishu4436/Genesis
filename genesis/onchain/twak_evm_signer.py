"""Eth account signer derived from TWAK wallet for x402 MCP payments."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from eth_account import Account
from loguru import logger

from genesis.onchain.twak_wallet_crypto import decrypt_twak_mnemonic, load_twak_wallet_json

if TYPE_CHECKING:
    from genesis.execution.twak_provider import TWAKProvider


def get_twak_eth_account(twak: TWAKProvider, password: str) -> Account:
    """Derive the TWAK HD wallet account used for EVM x402 authorizations."""
    wallet_path = None if twak.use_wsl else Path.home() / ".twak" / "wallet.json"
    wallet_data = load_twak_wallet_json(wallet_path, use_wsl=twak.use_wsl)
    mnemonic = decrypt_twak_mnemonic(wallet_data, password)
    try:
        Account.enable_unaudited_hdwallet_features()
        account = Account.from_mnemonic(mnemonic)
    finally:
        del mnemonic

    if twak._wallet_address and account.address.lower() != twak._wallet_address.lower():
        logger.warning(
            f"TWAK mnemonic address {account.address} != cached TWAK address {twak._wallet_address}"
        )
    return account


def build_x402_payment_client(
    twak: TWAKProvider,
    *,
    password: str,
    max_payment: str,
    prefer_network: str,
):
    """Create x402 HTTP client with EIP-3009 signing from the TWAK wallet."""
    from x402 import max_amount, prefer_network as x402_prefer_network, x402Client
    from x402.http.x402_http_client import x402HTTPClient
    from x402.mechanisms.evm.exact import ExactEvmScheme

    network_map = {
        "base": "eip155:8453",
        "bsc": "eip155:56",
        "ethereum": "eip155:1",
    }
    caip_network = network_map.get(prefer_network.lower(), prefer_network)

    account = get_twak_eth_account(twak, password)
    scheme = ExactEvmScheme(signer=account)

    client = x402Client()
    client.register("eip155:8453", scheme)
    client.register("eip155:56", scheme)
    client.register_policy(x402_prefer_network(caip_network))
    client.register_policy(max_amount(int(max_payment)))

    return x402HTTPClient(client)