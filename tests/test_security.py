"""Security hardening tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from dashboard.main import _is_local_request, _is_proxied_request, app
from genesis.execution.twak_provider import _safe_cmd_repr


def test_safe_cmd_repr_redacts_password_flag():
    cmd = ["twak", "wallet", "create", "--password", "super-secret"]
    assert _safe_cmd_repr(cmd) == "twak wallet create --password ***"


def test_safe_cmd_repr_redacts_wsl_export():
    cmd = [
        "wsl",
        "bash",
        "-lc",
        "export TWAK_WALLET_PASSWORD='my-pass'; twak wallet status --json",
    ]
    assert "my-pass" not in _safe_cmd_repr(cmd)
    assert "TWAK_WALLET_PASSWORD=***" in _safe_cmd_repr(cmd)


def test_proxied_request_not_treated_as_local():
    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {"cf-connecting-ip": "203.0.113.50"}
    assert _is_proxied_request(request) is True
    assert _is_local_request(request) is False


def test_direct_localhost_still_allowed():
    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {}
    assert _is_proxied_request(request) is False
    assert _is_local_request(request) is True


def test_tunnel_controls_blocked_when_env_disabled():
    client = TestClient(app)
    with patch("dashboard.main.get_env_settings") as mock_env:
        mock_env.return_value.dashboard_controls_enabled = False
        with patch("dashboard.main.get_agent_runner") as mock_runner:
            mock_runner.return_value.start_loop = AsyncMock(
                return_value={"ok": True, "state": "running"}
            )
            res = client.post(
                "/api/agent/start",
                headers={"cf-connecting-ip": "203.0.113.50"},
            )
    assert res.status_code == 403


def test_api_decisions_limit_capped():
    client = TestClient(app)
    res = client.get("/api/decisions", params={"limit": 9999})
    assert res.status_code == 422