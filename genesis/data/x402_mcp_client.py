"""CoinMarketCap x402 MCP client — pay-per-call with proper Streamable HTTP headers."""

from __future__ import annotations

import json
import os
from typing import Any, TYPE_CHECKING

import httpx
from loguru import logger

from genesis.data.mcp_client import CMCMCPClient, CMC_X402_MCP_URL, CMCCircuitBreaker

if TYPE_CHECKING:
    from genesis.execution.twak_provider import TWAKProvider

# CMC Streamable HTTP MCP requires this Accept value (tools/list is free at HTTP 200).
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class X402MCPClient:
    """
    MCP client for CMC x402 endpoint (https://mcp.coinmarketcap.com/x402/mcp).

    - tools/list: direct httpx (free; TWAK x402 fails because no 402 challenge)
    - tools/call: httpx + x402 PAYMENT-SIGNATURE with MCP headers on retry
    """

    def __init__(
        self,
        twak: TWAKProvider,
        mcp_url: str = CMC_X402_MCP_URL,
        max_payment: str = "10000",
        prefer_network: str = "base",
        auto_approve: bool = True,
        wallet_password: str = "",
    ) -> None:
        self.twak = twak
        self.mcp_url = mcp_url
        self.max_payment = max_payment
        self.prefer_network = prefer_network
        self.auto_approve = auto_approve
        self._wallet_password = wallet_password or os.getenv("TWAK_WALLET_PASSWORD", "")
        self._breaker = CMCCircuitBreaker()
        self._request_id = 0
        self._cached_tools: list[dict[str, Any]] | None = None
        self._http = httpx.AsyncClient(timeout=60.0)
        self._x402_http = None

    def _ensure_x402_client(self):
        if self._x402_http is not None:
            return self._x402_http
        if not self._wallet_password:
            raise ValueError("TWAK_WALLET_PASSWORD required for x402 MCP paid calls")
        from genesis.onchain.twak_evm_signer import build_x402_payment_client

        self._x402_http = build_x402_payment_client(
            self.twak,
            password=self._wallet_password,
            max_payment=self.max_payment,
            prefer_network=self.prefer_network,
        )
        return self._x402_http

    async def close(self) -> None:
        await self._http.aclose()

    def _next_payload(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return payload

    @staticmethod
    def _parse_rpc_response(data: dict[str, Any]) -> Any:
        if "error" in data:
            raise RuntimeError(f"x402 MCP RPC error: {data['error']}")

        result = data.get("result", {})
        if isinstance(result, dict) and result.get("isError"):
            content = result.get("content", [{}])
            msg = content[0].get("text", "Unknown MCP error") if content else "MCP error"
            raise RuntimeError(msg)
        return result

    async def _rpc_free(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Free MCP methods (e.g. tools/list) — no x402 payment."""
        payload = self._next_payload(method, params)
        response = await self._http.post(
            self.mcp_url,
            json=payload,
            headers=MCP_HEADERS,
        )
        response.raise_for_status()
        data = response.json()
        self._breaker.record_success()
        return self._parse_rpc_response(data)

    async def _rpc_paid(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Paid MCP methods — httpx 402 challenge + x402 signature + MCP retry."""
        if self._breaker.is_open:
            raise ConnectionError("CMC x402 MCP circuit breaker is open")

        payload = self._next_payload(method, params)
        x402_http = self._ensure_x402_client()

        try:
            response = await self._http.post(
                self.mcp_url,
                json=payload,
                headers=MCP_HEADERS,
            )

            if response.status_code == 200:
                data = response.json()
                self._breaker.record_success()
                return self._parse_rpc_response(data)

            if response.status_code != 402:
                response.raise_for_status()

            payment_headers, _payment_payload = await x402_http.handle_402_response(
                dict(response.headers),
                response.content,
            )
            if not payment_headers:
                raise RuntimeError("x402 payment headers missing from 402 response")

            retry_headers = {**MCP_HEADERS, **payment_headers}
            paid = await self._http.post(
                self.mcp_url,
                json=payload,
                headers=retry_headers,
            )
            paid.raise_for_status()
            data = paid.json()
            self._breaker.record_success()
            logger.debug(f"x402 MCP paid call OK: {method}")
            return self._parse_rpc_response(data)

        except Exception:
            self._breaker.record_failure()
            raise

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if method == "tools/list":
            return await self._rpc_free(method, params)
        return await self._rpc_paid(method, params)

    async def list_tools(self, refresh: bool = False) -> list[dict[str, Any]]:
        """List available MCP tools (free on x402 endpoint)."""
        if self._cached_tools is None or refresh:
            result = await self._rpc_free("tools/list", {})
            self._cached_tools = result.get("tools", []) if isinstance(result, dict) else []
        return self._cached_tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call an MCP tool (paid — ~0.01 USDC per call)."""
        logger.debug(f"x402 MCP call: {name}")
        result = await self._rpc_paid(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return CMCMCPClient.parse_content(result)

    async def health_check(self) -> dict[str, Any]:
        """Verify x402 MCP connectivity (tools/list is free)."""
        tools = await self.list_tools(refresh=True)
        payment_ready = bool(self._wallet_password)
        return {
            "url": self.mcp_url,
            # No CMC API key — auth is per-call via x402 micropayment signature.
            "authenticated": False,
            "auth_method": "x402",
            "payment_ready": payment_ready,
            "payment_mode": "x402",
            "max_payment_atomic": self.max_payment,
            "prefer_network": self.prefer_network,
            "tool_count": len(tools),
            "tools": [t.get("name") for t in tools],
            "circuit_breaker_open": self._breaker.is_open,
        }