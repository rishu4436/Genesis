"""Short-lived USD price cache (TWAK/CMC calls are slow)."""

from __future__ import annotations

import time
from typing import Any

_CACHE: dict[str, tuple[Any, float]] = {}
_DEFAULT_TTL = 30.0


def cache_get(key: str) -> Any | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    value, expires = entry
    if time.monotonic() > expires:
        _CACHE.pop(key, None)
        return None
    return value


def cache_set(key: str, value: Any, ttl: float = _DEFAULT_TTL) -> None:
    _CACHE[key] = (value, time.monotonic() + ttl)


def cache_clear(prefix: str | None = None) -> None:
    if prefix is None:
        _CACHE.clear()
        return
    for key in list(_CACHE):
        if key.startswith(prefix):
            _CACHE.pop(key, None)