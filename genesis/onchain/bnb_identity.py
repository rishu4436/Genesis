"""ERC-8004 agent identity — TWAK wallet + bnbagent SDK."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from genesis.core.config import EnvSettings
from genesis.onchain.twak_wallet_provider import TWAKWalletProvider

if TYPE_CHECKING:
    from genesis.execution.twak_provider import TWAKProvider


class BNBIdentityManager:
    """
    Genesis on-chain identity via TWAK + BNB AI Agent SDK.

    Write path (registration): TWAK `erc8004 register` — same HD wallet as trades.
    Read path (agent info): bnbagent ERC8004Agent + TWAKWalletProvider adapter.
    """

    def __init__(
        self,
        env: EnvSettings,
        twak: TWAKProvider | None = None,
    ) -> None:
        self.env = env
        self.twak = twak
        self._agent_id: str | None = env.competition_agent_id or None
        self._sdk: Any = None

    def _require_twak(self) -> TWAKProvider:
        if self.twak is None:
            raise ValueError(
                "TWAK provider required for ERC-8004. "
                "Run setup-wallet first or pass TWAKProvider to BNBIdentityManager."
            )
        return self.twak

    def _build_agent_uri(self) -> str:
        """Generate EIP-8004 agent URI using bnbagent (no signing required)."""
        from bnbagent import AgentEndpoint
        from bnbagent.erc8004.agent_uri import AgentURIGenerator

        endpoints = [
            AgentEndpoint(
                name="Genesis-API",
                endpoint=self.env.genesis_agent_endpoint,
                version="0.1.0",
            ),
            AgentEndpoint(
                name="ERC-8183",
                endpoint=f"{self.env.genesis_agent_endpoint.rstrip('/')}/erc8183/status",
                version="0.1.0",
            ),
        ]
        return AgentURIGenerator.generate_agent_uri(
            name=self.env.genesis_agent_name,
            description=self.env.genesis_agent_description,
            endpoints=endpoints,
        )

    def get_wallet_provider(self) -> TWAKWalletProvider:
        """bnbagent WalletProvider backed by the TWAK autonomous wallet."""
        return TWAKWalletProvider(self._require_twak())

    def get_sdk(self) -> Any:
        """
        bnbagent ERC8004Agent using TWAK as the signing backend.

        Note: contract writes should use TWAKProvider.erc8004_* — the SDK is
        primarily for read/query helpers that share the TWAK wallet address.
        """
        if self._sdk is not None:
            return self._sdk

        try:
            from bnbagent import ERC8004Agent
        except ImportError as e:
            raise ImportError(
                "bnbagent SDK not installed. Run: pip install bnbagent"
            ) from e

        self._sdk = ERC8004Agent(
            wallet_provider=self.get_wallet_provider(),
            network=self.env.bnb_agent_network,
        )
        return self._sdk

    async def register_agent(self) -> dict[str, Any]:
        """
        Register Genesis as ERC-8004 agent on BSC via TWAK.

        Uses the same self-custody TWAK wallet as swaps and x402 payments.
        """
        twak = self._require_twak()
        uri = self._build_agent_uri()
        metadata = {
            "strategy": "conservative_momentum_sentiment",
            "network": self.env.bnb_agent_network,
            "hackathon_track": str(self.env.hackathon_track),
            "hackathon_tracks": self.env.hackathon_tracks,
            "capabilities": "spot_trading,signal_fusion,risk_management,x402,strategy_skill",
            "wallet": "twak",
        }

        result = await twak.erc8004_register(uri=uri, metadata=metadata)

        self._agent_id = str(
            result.get("agentId")
            or result.get("agent_id")
            or result.get("id")
            or ""
        )
        if self._agent_id:
            logger.info(
                f"ERC-8004 agent registered via TWAK: ID={self._agent_id}, "
                f"TX={result.get('transactionHash', result.get('hash', 'N/A'))}"
            )
        return result

    async def update_agent_uri(self) -> dict[str, Any]:
        """Refresh on-chain agentURI after GENESIS_AGENT_ENDPOINT changes."""
        if not self._agent_id:
            raise ValueError("Agent not registered — set COMPETITION_AGENT_ID in .env")

        twak = self._require_twak()
        uri = self._build_agent_uri()
        result = await twak.erc8004_set_uri(self._agent_id, uri)
        logger.info(
            f"ERC-8004 agentURI updated: ID={self._agent_id}, "
            f"endpoint={self.env.genesis_agent_endpoint}"
        )
        return {"agent_id": self._agent_id, "uri": uri, **result}

    async def get_agent_info(self) -> dict[str, Any]:
        """Fetch registered agent info (TWAK show + optional SDK enrich)."""
        if not self._agent_id:
            return {"registered": False, "message": "Agent not yet registered"}

        twak = self._require_twak()
        try:
            onchain = await twak.erc8004_show(self._agent_id)
            info: dict[str, Any] = {
                "registered": True,
                "agent_id": self._agent_id,
                "wallet": await twak.get_wallet_address(),
                "source": "twak",
                **onchain,
            }
            return info
        except Exception as e:
            logger.warning(f"TWAK erc8004 show failed: {e}")
            return {"registered": True, "agent_id": self._agent_id, "error": str(e)}

    @property
    def agent_id(self) -> str | None:
        return self._agent_id

    def save_agent_id_to_env(self, agent_id: str) -> None:
        """Persist agent ID (user should add to .env as COMPETITION_AGENT_ID)."""
        self._agent_id = agent_id
        logger.info(
            f"Agent ID: {agent_id}. Add to .env: COMPETITION_AGENT_ID={agent_id}"
        )