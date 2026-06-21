"""Pytest configuration and fixtures."""

import pytest

from genesis.core.config import clear_config_cache


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear config caches between tests."""
    clear_config_cache()
    yield
    clear_config_cache()