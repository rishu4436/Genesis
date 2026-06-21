"""In-process Genesis agent controller for the dashboard."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from genesis.core.agent import GenesisAgent
from genesis.core.config import get_env_settings, get_rules

_runner: AgentRunner | None = None


def _empty_cycle_feed() -> dict[str, Any]:
    return {
        "active": False,
        "cycle_id": None,
        "phase": "idle",
        "current_symbol": None,
        "scanned": 0,
        "total": 0,
        "composites": [],
        "updated_at": None,
    }


class AgentRunner:
    """Start/stop the autonomous loop or run a single cycle from the dashboard."""

    def __init__(self) -> None:
        self._agent: GenesisAgent | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._cycle_task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()
        self._last_cycle: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._cycles_completed: int = 0
        self._cycle_feed: dict[str, Any] = _empty_cycle_feed()

    @property
    def state(self) -> str:
        if self._cycle_feed.get("active"):
            return "cycling"
        if self._cycle_task is not None and not self._cycle_task.done():
            return "cycling"
        if self._loop_task is not None and not self._loop_task.done():
            return "running"
        return "idle"

    def _touch_feed(self) -> None:
        self._cycle_feed["updated_at"] = datetime.now(timezone.utc).isoformat()

    def _reset_cycle_feed(self, cycle_id: str) -> None:
        self._cycle_feed = {
            "active": True,
            "cycle_id": cycle_id,
            "phase": "starting",
            "current_symbol": None,
            "scanned": 0,
            "total": 0,
            "composites": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _clear_cycle_feed(self) -> None:
        self._cycle_feed = _empty_cycle_feed()

    def _progress_handler(self, event: str, payload: dict[str, Any]) -> None:
        feed = self._cycle_feed
        if not feed.get("active"):
            return

        if event == "scan_start":
            feed["active"] = True
            feed["composites"] = []
            if payload.get("cycle_id"):
                feed["cycle_id"] = payload["cycle_id"]
            feed["phase"] = "scanning"
            feed["total"] = int(payload.get("total") or 0)
            feed["scanned"] = 0
            feed["current_symbol"] = None
        elif event == "scanning":
            feed["phase"] = "scanning"
            feed["current_symbol"] = payload.get("current_symbol")
            feed["scanned"] = int(payload.get("scanned") or 0)
            feed["total"] = int(payload.get("total") or feed.get("total") or 0)
        elif event == "composite":
            composite = payload.get("composite")
            if isinstance(composite, dict) and composite.get("symbol"):
                symbol = str(composite["symbol"]).upper()
                composites: list[dict[str, Any]] = feed.setdefault("composites", [])
                composites = [c for c in composites if str(c.get("symbol", "")).upper() != symbol]
                composites.append(composite)
                feed["composites"] = composites
            feed["current_symbol"] = payload.get("current_symbol")
            feed["scanned"] = int(payload.get("scanned") or len(feed.get("composites") or []))
            feed["total"] = int(payload.get("total") or feed.get("total") or 0)
            feed["phase"] = "scanning"
        elif event == "phase":
            feed["active"] = True
            if payload.get("cycle_id"):
                feed["cycle_id"] = payload["cycle_id"]
            feed["phase"] = str(payload.get("phase") or feed.get("phase") or "running")
            if payload.get("scanned") is not None:
                feed["scanned"] = int(payload["scanned"])
            if payload.get("total") is not None:
                feed["total"] = int(payload["total"])
        elif event == "complete":
            feed["active"] = False
            feed["phase"] = "idle"
            feed["current_symbol"] = None

        self._touch_feed()

    def cycle_feed(self) -> dict[str, Any]:
        return dict(self._cycle_feed)

    async def _ensure_agent(self) -> GenesisAgent:
        if self._agent is None:
            env = get_env_settings()
            rules = get_rules()
            self._agent = GenesisAgent(env, rules, simulate=False)
            await self._agent.initialize()
        return self._agent

    def _bind_progress(self, agent: GenesisAgent) -> None:
        agent._progress_cb = self._progress_handler

    def _unbind_progress(self, agent: GenesisAgent) -> None:
        agent._progress_cb = None

    async def start_loop(self) -> dict[str, Any]:
        async with self._lock:
            if self.state == "running":
                return {"ok": False, "message": "Agent loop is already running"}
            if self.state == "cycling":
                return {"ok": False, "message": "Wait for the current cycle to finish"}

            agent = await self._ensure_agent()
            agent._running = True
            self._last_error = None
            self._loop_task = asyncio.create_task(self._run_loop(agent))
            logger.info("Dashboard started autonomous agent loop")
            return {"ok": True, "state": self.state, "message": "Autonomous loop started"}

    async def _run_loop(self, agent: GenesisAgent) -> None:
        try:
            self._bind_progress(agent)
            await agent.run_loop()
        except asyncio.CancelledError:
            agent.stop()
            logger.info("Dashboard agent loop cancelled")
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Dashboard agent loop error: {e}")
        finally:
            self._unbind_progress(agent)
            self._clear_cycle_feed()
            self._loop_task = None

    async def stop_loop(self) -> dict[str, Any]:
        async with self._lock:
            if self._agent is not None:
                self._agent.stop()
            if self._loop_task is not None and not self._loop_task.done():
                self._loop_task.cancel()
                try:
                    await self._loop_task
                except asyncio.CancelledError:
                    pass
            self._loop_task = None
            self._clear_cycle_feed()
            logger.info("Dashboard stopped agent loop")
            return {"ok": True, "state": self.state, "message": "Agent stopped"}

    async def run_one_cycle(self) -> dict[str, Any]:
        async with self._lock:
            if self.state == "running":
                return {
                    "ok": False,
                    "message": "Stop the autonomous loop before running a manual cycle",
                }
            if self.state == "cycling":
                return {"ok": False, "message": "A cycle is already in progress"}

            agent = await self._ensure_agent()
            self._last_error = None
            self._reset_cycle_feed("pending")
            self._bind_progress(agent)

            async def _cycle() -> Any:
                return await agent.run_cycle()

            self._cycle_task = asyncio.create_task(_cycle())
            try:
                audit = await self._cycle_task
                self._cycles_completed += 1
                decision = audit.decision
                action = decision.action.value if decision else "none"
                self._last_cycle = {
                    "cycle_id": audit.cycle_id,
                    "action": action,
                    "asset": decision.asset if decision else None,
                    "reason": decision.reason if decision else None,
                    "duration_ms": audit.duration_ms,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if self._cycle_feed.get("active"):
                    self._cycle_feed["cycle_id"] = audit.cycle_id
                return {"ok": True, "state": self.state, "cycle": self._last_cycle}
            except Exception as e:
                self._last_error = str(e)
                return {"ok": False, "message": str(e)}
            finally:
                self._unbind_progress(agent)
                self._clear_cycle_feed()
                self._cycle_task = None

    def status(self) -> dict[str, Any]:
        env = get_env_settings()
        rules = get_rules()
        return {
            "state": self.state,
            "managed_by": "dashboard",
            "network": env.genesis_network,
            "loop_interval_seconds": rules.loop.interval_seconds,
            "cycles_completed": self._cycles_completed,
            "last_cycle": self._last_cycle,
            "last_error": self._last_error,
            "llm_enabled": env.llm_enabled,
            "cycle_feed": self.cycle_feed(),
        }


def get_agent_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner