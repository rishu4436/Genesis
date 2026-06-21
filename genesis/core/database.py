"""SQLite persistence for audit trail, trades, and portfolio snapshots."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from genesis.core.models import (
    AuditRecord,
    CompositeSignal,
    DcaPositionState,
    Decision,
    PortfolioSnapshot,
    RiskValidation,
    Signal,
    Trade,
)
from genesis.utils import utc_now_iso


class Database:
    """Async SQLite store for Genesis audit data."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Create tables if not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_records (
                    id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    data JSON NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    tx_hash TEXT,
                    status TEXT NOT NULL,
                    data JSON NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id TEXT PRIMARY KEY,
                    total_value_usd REAL NOT NULL,
                    data JSON NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    data JSON NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dca_positions (
                    symbol TEXT PRIMARY KEY,
                    data JSON NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_records(created_at);
                CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
                """
            )
            await db.commit()

    async def save_audit(self, record: AuditRecord) -> str:
        """Persist full audit record."""
        record_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO audit_records (id, cycle_id, data, created_at) VALUES (?, ?, ?, ?)",
                (record_id, record.cycle_id, record.model_dump_json(), utc_now_iso()),
            )
            await db.commit()
        return record_id

    async def save_trade(self, trade: Trade) -> str:
        """Persist trade record."""
        trade_id = trade.id or str(uuid.uuid4())
        trade.id = trade_id
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO trades (id, symbol, side, amount_usd, tx_hash, status, data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade_id,
                    trade.symbol,
                    trade.side.value,
                    trade.amount_usd,
                    trade.tx_hash,
                    trade.status.value,
                    trade.model_dump_json(),
                    utc_now_iso(),
                ),
            )
            await db.commit()
        return trade_id

    async def save_decision(self, decision: Decision) -> str:
        """Persist decision record."""
        decision_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO decisions (id, action, asset, confidence, data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    decision.action.value,
                    decision.asset,
                    decision.confidence,
                    decision.model_dump_json(),
                    utc_now_iso(),
                ),
            )
            await db.commit()
        return decision_id

    async def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> str:
        """Persist portfolio snapshot."""
        snapshot_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO portfolio_snapshots (id, total_value_usd, data, created_at)
                   VALUES (?, ?, ?, ?)""",
                (snapshot_id, snapshot.total_value_usd, snapshot.model_dump_json(), utc_now_iso()),
            )
            await db.commit()
        return snapshot_id

    async def get_recent_audits(self, limit: int = 20) -> list[dict]:
        """Fetch recent audit records."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM audit_records ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [json.loads(row["data"]) for row in rows]

    async def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """Fetch recent trades."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [json.loads(row["data"]) for row in rows]

    @staticmethod
    def _trade_asset(trade: dict) -> str:
        symbol = trade.get("symbol", "")
        side = str(trade.get("side", "")).upper()
        if "/" in symbol:
            base, quote = symbol.split("/", 1)
            return base if side == "SELL" else quote
        return symbol

    async def update_trade_data(self, trade_id: str, trade_data: dict) -> None:
        """Update persisted trade JSON (e.g. backfill entry price)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE trades SET data = ? WHERE id = ?",
                (json.dumps(trade_data), trade_id),
            )
            await db.commit()

    async def get_traded_asset_symbols(self, limit: int = 100) -> list[str]:
        """Distinct assets from recent confirmed BUY trades (for on-chain supplement)."""
        trades = await self.get_recent_trades(limit)
        symbols: list[str] = []
        seen: set[str] = set()
        for trade in trades:
            if str(trade.get("side", "")).upper() != "BUY":
                continue
            if str(trade.get("status", "")).lower() not in {
                "confirmed",
                "submitted",
                "simulated",
            }:
                continue
            asset = self._trade_asset(trade)
            key = asset.upper()
            if asset and key not in seen:
                symbols.append(asset)
                seen.add(key)
        return symbols

    async def get_entry_prices(self, limit: int = 100) -> dict[str, dict]:
        """Latest BUY entry price and size per asset (for dashboard PnL)."""
        trades = await self.get_recent_trades(limit)
        entries: dict[str, dict] = {}
        for trade in trades:
            if str(trade.get("side", "")).upper() != "BUY":
                continue
            asset = self._trade_asset(trade)
            if not asset or asset in entries:
                continue
            price = trade.get("price")
            if not price and trade.get("amount_token") and trade.get("amount_usd"):
                price = float(trade["amount_usd"]) / float(trade["amount_token"])
            entries[asset] = {
                "entry_price": price,
                "amount_token": trade.get("amount_token"),
                "amount_usd": trade.get("amount_usd"),
                "tx_hash": trade.get("tx_hash"),
                "timestamp": trade.get("timestamp"),
            }
        return entries

    async def get_latest_portfolio(self) -> dict | None:
        """Fetch most recent portfolio snapshot."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT data FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return json.loads(row["data"]) if row else None

    async def get_dca_states(self) -> dict[str, DcaPositionState]:
        """Load all active DCA ladder positions."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT symbol, data FROM dca_positions")
            rows = await cursor.fetchall()
        states: dict[str, DcaPositionState] = {}
        for row in rows:
            payload = json.loads(row["data"])
            state = DcaPositionState.model_validate(payload)
            if state.active:
                states[row["symbol"].upper()] = state
        return states

    async def upsert_dca_state(self, state: DcaPositionState) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO dca_positions (symbol, data, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
                (state.symbol.upper(), state.model_dump_json(), utc_now_iso()),
            )
            await db.commit()

    async def clear_dca_state(self, symbol: str) -> None:
        sym = symbol.upper()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM dca_positions WHERE symbol = ?", (sym,))
            await db.commit()

    async def export_audit_trail(self, output_path: str) -> int:
        """Export all audit records to JSON file for judges."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT data FROM audit_records ORDER BY created_at ASC")
            rows = await cursor.fetchall()

        records = [json.loads(row["data"]) for row in rows]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, default=str)

        return len(records)