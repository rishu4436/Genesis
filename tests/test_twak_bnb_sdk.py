"""Tests for TWAK ↔ bnbagent SDK bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from genesis.core.config import EnvSettings
from genesis.execution.twak_provider import TWAKProvider
from genesis.onchain.bnb_identity import BNBIdentityManager
from genesis.onchain.twak_wallet_provider import TWAKWalletProvider


@pytest.fixture
def env() -> EnvSettings:
    return EnvSettings(
        genesis_agent_name="Genesis",
        genesis_agent_description="Test agent",
        genesis_agent_endpoint="http://localhost:8080",
        bnb_agent_network="bsc-mainnet",
        hackathon_track=1,
    )


@pytest.fixture
def twak() -> MagicMock:
    mock = MagicMock(spec=TWAKProvider)
    mock.chain = "bsc"
    mock.get_wallet_address = AsyncMock(return_value="0x000000000000000000000000000000000000dEaD")
    mock.erc8004_register = AsyncMock(
        return_value={"agentId": 42, "transactionHash": "0xabc"}
    )
    mock.erc8004_show = AsyncMock(return_value={"agentURI": "data:application/json,..."})
    mock._run = AsyncMock(
        return_value={
            "signature": "0xsig",
            "digest": "0xdigest",
        }
    )
    return mock


def test_twak_wallet_provider_address(twak: MagicMock):
    provider = TWAKWalletProvider(twak)
    assert provider.address == "0x000000000000000000000000000000000000dEaD"
    twak.get_wallet_address.assert_called_once()


def test_twak_wallet_provider_sign_message(twak: MagicMock):
    provider = TWAKWalletProvider(twak)
    result = provider.sign_message("hello")
    assert result["signature"] == "0xsig"
    twak._run.assert_called_once()


def test_twak_wallet_provider_sign_transaction_raises(twak: MagicMock):
    provider = TWAKWalletProvider(twak)
    with pytest.raises(NotImplementedError, match="erc8004_register"):
        provider.sign_transaction({"to": "0x0", "value": 0})


@pytest.mark.asyncio
async def test_bnb_identity_register_via_twak(env: EnvSettings, twak: MagicMock):
    manager = BNBIdentityManager(env, twak=twak)
    result = await manager.register_agent()
    assert result["agentId"] == 42
    assert manager.agent_id == "42"
    twak.erc8004_register.assert_called_once()
    call_kwargs = twak.erc8004_register.call_args.kwargs
    assert call_kwargs["uri"].startswith("data:")
    assert call_kwargs["metadata"]["wallet"] == "twak"


@pytest.mark.asyncio
async def test_bnb_identity_get_sdk_uses_twak_wallet(env: EnvSettings, twak: MagicMock):
    manager = BNBIdentityManager(env, twak=twak)
    with patch("bnbagent.ERC8004Agent") as mock_sdk:
        sdk = manager.get_sdk()
        mock_sdk.assert_called_once()
        wallet = mock_sdk.call_args.kwargs["wallet_provider"]
        assert isinstance(wallet, TWAKWalletProvider)
        assert sdk is mock_sdk.return_value


@pytest.mark.asyncio
async def test_bnb_identity_requires_twak(env: EnvSettings):
    manager = BNBIdentityManager(env, twak=None)
    with pytest.raises(ValueError, match="TWAK provider required"):
        await manager.register_agent()