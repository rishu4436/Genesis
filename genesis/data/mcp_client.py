"""CoinMarketCap Agent Hub — Streamable HTTP MCP client."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from loguru import logger

# Official CMC MCP tools (https://coinmarketcap.com/api/mcp/)
CMC_MCP_TOOLS: tuple[str, ...] = (
    "get_crypto_quotes_latest",
    "get_crypto_technical_analysis",
    "get_crypto_metrics",
    "get_global_crypto_derivatives_metrics",
    "get_crypto_latest_news",
    "trending_crypto_narratives",
    "get_global_metrics_latest",
    "search_crypto_info",
    "search_cryptos",
    "get_crypto_info",
    "get_crypto_marketcap_technical_analysis",
    "get_upcoming_macro_events",
)

CMC_MCP_URL = "https://mcp.coinmarketcap.com/mcp"
CMC_X402_MCP_URL = "https://mcp.coinmarketcap.com/x402/mcp"


class CMCCircuitBreaker:
    """Circuit breaker for MCP resilience."""

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60.0) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._failures = 0
        self._open_until = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_until = time.monotonic() + self.reset_timeout
            logger.warning("CMC MCP circuit breaker OPEN")

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self._open_until


class CMCMCPClient:
    """
    Streamable HTTP client for CoinMarketCap Agent Hub MCP.

    Auth: X-CMC-MCP-API-KEY header (same key as pro.coinmarketcap.com dashboard).
    x402: optional endpoint at https://mcp.coinmarketcap.com/x402/mcp (no API key).
    """

    def __init__(
        self,
        api_key: str,
        mcp_url: str = CMC_MCP_URL,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.mcp_url = mcp_url
        self._breaker = CMCCircuitBreaker()
        self._http = httpx.AsyncClient(timeout=timeout)
        self._request_id = 0
        self._cached_tools: list[dict[str, Any]] | None = None

    async def close(self) -> None:
        await self._http.aclose()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["X-CMC-MCP-API-KEY"] = self.api_key
        if extra:
            headers.update(extra)
        return headers

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._breaker.is_open:
            raise ConnectionError("CMC MCP circuit breaker is open")

        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        try:
            response = await self._http.post(
                self.mcp_url,
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                raise RuntimeError(f"MCP RPC error: {data['error']}")

            result = data.get("result", {})
            if isinstance(result, dict) and result.get("isError"):
                content = result.get("content", [{}])
                msg = content[0].get("text", "Unknown MCP error") if content else "MCP error"
                raise RuntimeError(msg)

            self._breaker.record_success()
            return result

        except Exception:
            self._breaker.record_failure()
            raise

    @staticmethod
    def parse_content(result: Any) -> Any:
        """Parse JSON from MCP tool result content blocks."""
        if not isinstance(result, dict):
            return result

        content = result.get("content", [])
        for block in content:
            if block.get("type") == "text":
                text = block.get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
        return result

    async def list_tools(self, refresh: bool = False) -> list[dict[str, Any]]:
        """List available MCP tools (cached)."""
        if self._cached_tools is None or refresh:
            result = await self._rpc("tools/list", {})
            self._cached_tools = result.get("tools", []) if isinstance(result, dict) else []
        return self._cached_tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call an MCP tool and return parsed JSON payload."""
        result = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return self.parse_content(result)

    async def health_check(self) -> dict[str, Any]:
        """Verify MCP connectivity and auth."""
        tools = await self.list_tools(refresh=True)
        return {
            "url": self.mcp_url,
            "authenticated": bool(self.api_key),
            "tool_count": len(tools),
            "tools": [t.get("name") for t in tools],
            "circuit_breaker_open": self._breaker.is_open,
        }