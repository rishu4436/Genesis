"""Unit tests for configuration loading."""

from pathlib import Path

import pytest

from genesis.core.config import PROJECT_ROOT, load_rules
from genesis.core.models import RulesConfig


def test_load_rules_yaml():
    rules = load_rules()
    assert isinstance(rules, RulesConfig)
    assert rules.strategy.name == "dca_dip"
    assert rules.dca_dip.enabled is True
    assert rules.dca_dip.trigger_drop_24h_pct == 20.0
    assert rules.dca_dip.take_profit_pct == 20.0
    assert len(rules.allowed_tokens) >= 78


def test_rules_file_exists():
    rules_path = PROJECT_ROOT / "config" / "rules.yaml"
    assert rules_path.exists()


def test_load_rules_missing_file():
    with pytest.raises(FileNotFoundError):
        load_rules("/nonexistent/rules.yaml")