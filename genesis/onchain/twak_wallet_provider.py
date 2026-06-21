"""bnbagent WalletProvider backed by TWAK CLI (single self-custody wallet)."""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from genesis.execution.twak_provider import TWAKProvider

try:
    from bnbagent.signing import PolicyViolation, SigningPolicy
    from bnbagent.wallets import WalletProvider
except ImportError:  # pragma: no cover - optional at import time in tests
    WalletProvider = object  # type: ignore[misc, assignment]
    SigningPolicy = None  # type: ignore[misc, assignment]
    PolicyViolation = Exception  # type: ignore[misc, assignment]


def _run_async(coro: Any) -> Any:
    """Run async TWAK call from sync bnbagent WalletProvider methods."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class TWAKWalletProvider(WalletProvider):
    """
    Bridge TWAK autonomous wallet into bnbagent's WalletProvider interface.

    Signing stays in TWAK's local HD wallet — no duplicate keystore in ~/.bnbagent.
    Contract writes for ERC-8004 should use TWAKProvider.erc8004_* helpers;
    this adapter exposes address/message signing for SDK read paths and metadata.
    """

    def __init__(
        self,
        twak: TWAKProvider,
        signing_policy: SigningPolicy | None = None,
    ) -> None:
        if WalletProvider is object:
            raise ImportError("bnbagent SDK not installed. Run: pip install bnbagent")

        self._twak = twak
        self._address: str | None = None
        self._signing_policy = signing_policy or SigningPolicy.permissive()

    @property
    def address(self) -> str:
        if not self._address:
            self._address = _run_async(self._twak.get_wallet_address())
        return self._address

    def sign_message(self, message: str) -> dict[str, Any]:
        result = _run_async(
            self._twak._run(
                "wallet",
                "sign-message",
                "--chain",
                self._twak.chain,
                "--message",
                message,
            )
        )
        signature = result.get("signature", "")
        digest = result.get("digest", "")
        return {
            "messageHash": digest,
            "signature": signature,
            "r": "",
            "s": "",
            "v": 0,
        }

    def sign_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """
        bnbagent contract writes — prefer TWAKProvider.erc8004_register() instead.

        TWAK CLI does not expose raw RLP transaction signing; ERC-8004 registration
        and competition signup route through dedicated TWAK commands.
        """
        raise NotImplementedError(
            "Use TWAKProvider.erc8004_register() or other TWAK contract commands "
            "for on-chain writes. TWAK holds the signing key locally."
        )

    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """EIP-712 signing — use TWAK x402_request() for micropayments."""
        if self._signing_policy is not None:
            from bnbagent.signing import check as policy_check

            policy_check(self._signing_policy, domain, types, message)

        raise NotImplementedError(
            "Use TWAKProvider.x402_request() for x402 EIP-3009 payments. "
            "Typed-data signing is handled inside the TWAK CLI."
        )