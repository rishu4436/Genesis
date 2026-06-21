"""Tests for x402 MCP client free/paid routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from genesis.data.x402_mcp_client import MCP_HEADERS, X402MCPClient
from genesis.execution.twak_provider import TWAKProvider


@pytest.mark.asyncio
async def test_list_tools_uses_free_http_path():
    twak = MagicMock(spec=TWAKProvider)
    client = X402MCPClient(twak=twak, wallet_password="pw")

    tools_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": "get_crypto_quotes_latest"}]},
    }

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(
        return_value=httpx.Response(200, json=tools_payload, request=httpx.Request("POST", "http://test"))
    )
    client._http = mock_http

    tools = await client.list_tools(refresh=True)
    assert len(tools) == 1
    mock_http.post.assert_awaited_once()
    call_kwargs = mock_http.post.await_args.kwargs
    assert call_kwargs["headers"] == MCP_HEADERS
    await client.close()