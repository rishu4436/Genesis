"""TWAK wallet and ERC-8004 context for the dashboard."""

from __future__ import annotations

import asyncio
from typing import Any

from genesis.core.config import EnvSettings, get_env_settings
from genesis.execution.twak_provider import TWAKProvider
from genesis.onchain.bnb_identity import BNBIdentityManager

from dashboard.price_cache import cache_get, cache_set
from dashboard.wallet_store import persist_wallet_address, resolve_wallet_address

WALLET_CACHE_TTL = 120.0

INTEGRATION_LINKS = {
    "hackathon": "https://dorahacks.io/hackathon/bnbhack-twt-cmc",
    "twak": "https://github.com/trustwallet/tw-agent-skills",
    "twak_install": "https://raw.githubusercontent.com/trustwallet/tw-agent-skills/main/install.sh",
    "bnbagent": "https://github.com/bnb-chain/bnbagent",
    "erc8004_docs": "https://eips.ethereum.org/EIPS/eip-8004",
    "erc8004_registry": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
    "bscscan": "https://bscscan.com",
    "pancakeswap": "https://pancakeswap.finance",
    "cmc_mcp": "https://coinmarketcap.com/api/mcp/",
}


def erc8004_nft_url(agent_id: str | int | None, *, bscscan: str = INTEGRATION_LINKS["bscscan"]) -> str | None:
    """BscScan NFT link for an ERC-8004 on-chain agent identity."""
    if not agent_id:
        return None
    registry = INTEGRATION_LINKS["erc8004_registry"]
    return f"{bscscan}/nft/{registry}/{agent_id}"


async def get_wallet_integration(
    env: EnvSettings | None = None,
    *,
    full: bool = True,
) -> dict[str, Any]:
    """Fetch TWAK wallet, balance hints, and ERC-8004 registration info."""
    env = env or get_env_settings()
    cache_key = f"wallet:{env.twak_network}:{full}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    twak = TWAKProvider.from_env(env)
    stored_address = resolve_wallet_address(env)
    result: dict[str, Any] = {
        "twak_installed": bool(stored_address),
        "wallet_address": stored_address or None,
        "wallet_mode": env.twak_wallet_mode,
        "twak_network": env.twak_network,
        "agent_endpoint": env.genesis_agent_endpoint,
        "erc8004_agent_id": env.competition_agent_id or None,
        "erc8004_registered": bool(env.competition_agent_id),
        "erc8004_detail": None,
        "competition": None,
        "bscscan_wallet_url": (
            f"{INTEGRATION_LINKS['bscscan']}/address/{stored_address}" if stored_address else None
        ),
        "links": dict(INTEGRATION_LINKS),
    }
    if stored_address:
        result["links"]["wallet_bscscan"] = result["bscscan_wallet_url"]

    async def _address() -> str | None:
        try:
            return await twak.get_wallet_address()
        except Exception as exc:
            result["wallet_error"] = str(exc)
            return None

    async def _compete() -> dict[str, Any]:
        try:
            compete = await twak.compete_status()
            return {
                "registered": compete.get("registered", False),
                "open": compete.get("open", False),
                "participant": compete.get("participant"),
                "deadline": compete.get("deadline"),
                "opens_at": compete.get("opensAt"),
                "seconds_remaining": compete.get("secondsRemaining"),
                "chain": compete.get("chain", env.twak_chain),
            }
        except Exception as exc:
            return {"error": str(exc)}

    if not full:
        address, competition = await asyncio.gather(_address(), _compete())
        if address:
            persist_wallet_address(address)
            result["wallet_address"] = address
            result["bscscan_wallet_url"] = f"{INTEGRATION_LINKS['bscscan']}/address/{address}"
            result["links"]["wallet_bscscan"] = result["bscscan_wallet_url"]
        result["twak_installed"] = bool(address or stored_address)
        result["competition"] = competition
        cache_set(cache_key, result, WALLET_CACHE_TTL)
        return result

    try:
        verified = await twak.verify_installation()
    except Exception:
        verified = False
    result["twak_installed"] = verified or bool(stored_address)

    if not verified and not stored_address:
        return result

    address, competition = await asyncio.gather(_address(), _compete())
    if address:
        persist_wallet_address(address)
        result["wallet_address"] = address
        result["bscscan_wallet_url"] = f"{INTEGRATION_LINKS['bscscan']}/address/{address}"
        result["links"]["wallet_bscscan"] = result["bscscan_wallet_url"]
        result["twak_installed"] = True
    result["competition"] = competition

    try:
        status = await twak._run("wallet", "status")  # noqa: SLF001
        if isinstance(status, dict):
            result["wallet_status"] = status.get("agentWallet") or status.get("status") or status
    except Exception:
        pass

    if env.competition_agent_id:
        try:
            identity = BNBIdentityManager(env, twak=twak)
            detail = await identity.get_agent_info()
            result["erc8004_detail"] = detail
            result["erc8004_registered"] = detail.get("registered", True)
        except Exception as e:
            result["erc8004_detail"] = {"error": str(e)}

    cache_set(cache_key, result, WALLET_CACHE_TTL)
    return result