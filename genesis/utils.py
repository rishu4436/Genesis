"""Shared utilities for Genesis."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return current UTC time as ISO string."""
    return utc_now().isoformat()


def safe_json_loads(raw: str, default: Any = None) -> Any:
    """Parse JSON safely, returning default on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def bps_to_fraction(bps: int | float) -> float:
    """Convert basis points to decimal fraction."""
    return float(bps) / 10_000.0


def pct_to_fraction(pct: float) -> float:
    """Convert percentage to decimal fraction."""
    return pct / 100.0


def truncate_address(address: str, left: int = 6, right: int = 4) -> str:
    """Truncate Ethereum address for display."""
    if len(address) <= left + right + 2:
        return address
    return f"{address[:left]}...{address[-right:]}"


def with_retries(max_attempts: int = 3):
    """Standard retry decorator for I/O operations."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )