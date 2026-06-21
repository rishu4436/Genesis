"""Persist generated Track 2 strategy JSON files."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from genesis.core.config import PROJECT_ROOT

GENERATED_DIR = PROJECT_ROOT / "data" / "generated_strategies"
_FILENAME_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def save_strategy_json(strategy: dict[str, Any]) -> tuple[str, Path]:
    """
    Write strategy spec to data/generated_strategies/.

    Returns (filename, absolute_path).
    """
    asset = (
        strategy.get("market_scope", {}).get("primary_asset") or "strategy"
    ).lower()
    asset = _FILENAME_SAFE.sub("", asset) or "strategy"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"genesis-strategy-{asset}-{ts}.json"

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = GENERATED_DIR / filename
    path.write_text(json.dumps(strategy, indent=2), encoding="utf-8")
    return filename, path


def resolve_strategy_file(filename: str) -> Path | None:
    """Resolve a generated strategy file safely (no path traversal)."""
    clean = Path(filename).name
    if clean != filename or not clean.endswith(".json") or not clean.startswith("genesis-strategy-"):
        return None
    path = GENERATED_DIR / clean
    return path if path.is_file() else None