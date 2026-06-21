"""Hackathon competition registration helpers."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from genesis.core.config import EnvSettings, get_rules
from genesis.execution.twak_provider import TWAKProvider
from genesis.strategy_skill.builder import build_strategy_spec


class CompetitionRegistrar:
    """
    Helpers for hackathon competition registration.

    Supports:
    - TWAK `compete register` CLI command
    - Manual registration metadata export
    """

    HACKATHON_URL = "https://dorahacks.io/hackathon/bnbhack-twt-cmc"

    TRACKS = {
        1: {
            "name": "Autonomous Trading Agents",
            "prize_usd": 24000,
            "description": "Live self-custody agent: CMC signals → decisions → TWAK execution on BSC",
        },
        2: {
            "name": "Strategy Skills",
            "prize_usd": 6000,
            "description": "CMC Skill with backtestable strategy spec (momentum + sentiment fusion)",
        },
    }

    HACKATHON_INFO = {
        "name": "BNB Hack: AI Trading Agent Edition — CoinMarketCap × Trust Wallet",
        "tracks": TRACKS,
        "track": 1,
        "track_name": "Autonomous Trading Agents on BSC",
        "url": HACKATHON_URL,
    }

    def __init__(self, env: EnvSettings, twak: TWAKProvider | None = None) -> None:
        self.env = env
        self.twak = twak

    async def register_via_twak(self, agent_id: str = "") -> dict[str, Any]:
        """
        Register for competition via TWAK CLI.

        Command: twak compete register (uses configured agent wallet on BSC)
        """
        if not self.twak:
            raise ValueError("TWAK provider required for CLI registration")

        result = await self.twak.compete_register()
        if agent_id:
            result["erc8004_agent_id"] = agent_id

        logger.info(f"Competition registration submitted: {result}")
        return result

    def build_registration_payload(self, agent_id: str) -> dict[str, Any]:
        """Build registration metadata for manual submission."""
        rules = get_rules()
        return {
            "hackathon": self.HACKATHON_INFO,
            "tracks_submitted": [1, 2],
            "track_2_strategy_skill": {
                "name": "genesis-momentum-sentiment",
                "spec": build_strategy_spec(rules),
            },
            "agent": {
                "name": self.env.genesis_agent_name,
                "description": self.env.genesis_agent_description,
                "agent_id": agent_id,
                "erc8004": True,
                "network": self.env.bnb_agent_network,
                "endpoint": self.env.genesis_agent_endpoint,
            },
            "integrations": {
                "cmc_agent_hub": {
                    "mcp_url": self.env.cmc_mcp_url,
                    "tools_used": [
                        "get_quotes",
                        "get_technicals",
                        "get_sentiment",
                        "get_onchain",
                        "get_derivatives",
                        "get_news",
                    ],
                    "x402_enabled": self.env.cmc_x402_enabled,
                },
                "twak": {
                    "wallet_mode": self.env.twak_wallet_mode,
                    "features": [
                        "autonomous_wallet",
                        "local_signing",
                        "pancakeswap_swap",
                        "pancakeswap_perps",
                        "x402_payments",
                    ],
                },
                "bnb_agent_sdk": {
                    "erc8004_registered": bool(agent_id),
                    "agent_id": agent_id,
                    "wallet_backend": "twak",
                    "sdk_adapter": "TWAKWalletProvider",
                },
            },
            "strategy": {
                "name": "conservative_momentum_sentiment",
                "type": "multi_signal_fusion",
                "risk_first": True,
            },
        }

    def export_registration(self, agent_id: str, output_path: str) -> str:
        """Export registration payload to JSON file."""
        payload = self.build_registration_payload(agent_id)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info(f"Registration payload exported to {output_path}")
        return output_path