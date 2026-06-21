"""Tests for dashboard agent runner."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.agent_runner import AgentRunner


@pytest.mark.asyncio
async def test_runner_idle_by_default():
    runner = AgentRunner()
    assert runner.state == "idle"
    status = runner.status()
    assert status["state"] == "idle"


@pytest.mark.asyncio
async def test_run_one_cycle_success():
    runner = AgentRunner()
    mock_audit = MagicMock()
    mock_audit.cycle_id = "abc12345"
    mock_audit.decision = MagicMock(action=MagicMock(value="hold"), asset="BNB", reason="wait")
    mock_audit.duration_ms = 120

    mock_agent = AsyncMock()
    mock_agent.run_cycle = AsyncMock(return_value=mock_audit)
    mock_agent.initialize = AsyncMock()

    with patch.object(runner, "_ensure_agent", new_callable=AsyncMock, return_value=mock_agent):
        result = await runner.run_one_cycle()

    assert result["ok"] is True
    assert result["cycle"]["cycle_id"] == "abc12345"
    assert runner.state == "idle"


@pytest.mark.asyncio
async def test_cannot_cycle_while_running():
    runner = AgentRunner()
    runner._loop_task = MagicMock()
    runner._loop_task.done.return_value = False

    result = await runner.run_one_cycle()
    assert result["ok"] is False
    assert "Stop" in result["message"]