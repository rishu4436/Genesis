"""Tests for live cycle feed and dashboard controls."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.agent_logic import build_cycle_feed_view, categorize_tokens
from dashboard.main import app


def test_categorize_tokens_splits_directions():
    tokens = [
        {"symbol": "BNB", "direction": "bullish", "conviction": 0.8},
        {"symbol": "ETH", "direction": "neutral", "conviction": 0.5},
        {"symbol": "CAKE", "direction": "bearish", "conviction": 0.2},
    ]
    cats = categorize_tokens(tokens)
    assert len(cats["bullish"]) == 1
    assert len(cats["neutral"]) == 1
    assert len(cats["bearish"]) == 1


def test_build_cycle_feed_view_from_progress():
    from genesis.core.config import get_rules

    rules = get_rules()
    feed = {
        "active": True,
        "cycle_id": "abc12345",
        "phase": "scanning",
        "current_symbol": "BNB",
        "scanned": 2,
        "total": 5,
        "composites": [
            {
                "symbol": "BNB",
                "conviction": 0.72,
                "direction": "bullish",
                "components": {"technicals": 0.8, "sentiment": 0.6},
            },
            {
                "symbol": "ETH",
                "conviction": 0.48,
                "direction": "neutral",
                "components": {"technicals": 0.5},
            },
        ],
        "updated_at": "2026-06-20T12:00:00+00:00",
    }
    view = build_cycle_feed_view(feed, rules, llm_enabled=True)
    assert view["active"] is True
    assert view["progress"]["scanned"] == 2
    assert view["stats"]["bullish"] == 1
    assert view["stats"]["neutral"] == 1
    assert view["categories"]["bullish"][0]["symbol"] == "BNB"


def test_agent_controls_blocked_when_disabled():
    client = TestClient(app)
    with patch("dashboard.main._controls_enabled", return_value=False):
        res = client.post("/api/agent/start")
    assert res.status_code == 403


def test_localhost_can_control_when_env_disabled():
    client = TestClient(app)
    with patch("dashboard.main.get_env_settings") as mock_env:
        mock_env.return_value.dashboard_controls_enabled = False
        with patch("dashboard.main._is_local_request", return_value=True):
            with patch("dashboard.main.get_agent_runner") as mock_runner:
                mock_runner.return_value.start_loop = AsyncMock(
                    return_value={"ok": True, "state": "running"}
                )
                res = client.post("/api/agent/start")
    assert res.status_code == 200


def test_cycle_feed_endpoint_returns_json():
    client = TestClient(app)
    res = client.get("/api/cycle/feed")
    assert res.status_code == 200
    data = res.json()
    assert "categories" in data
    assert "bullish" in data["categories"]
    assert "stats" in data