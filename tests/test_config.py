"""Unit tests for configuration loading."""

from pathlib import Path

import pytest

from genesis.core.config import PROJECT_ROOT, load_rules
from genesis.core.models import RulesConfig


def test_load_rules_yaml():
    rules = load_rules()
    assert isinstance(rules, RulesConfig)
    assert rules.strategy.name == "conservative_momentum_sentiment"
    assert len(rules.allowed_tokens) >= 78


def test_rules_file_exists():
    rules_path = PROJECT_ROOT / "config" / "rules.yaml"
    assert rules_path.exists()


def test_load_rules_missing_file():
    with pytest.raises(FileNotFoundError):
        load_rules("/nonexistent/rules.yaml")