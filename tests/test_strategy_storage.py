"""Tests for generated strategy JSON persistence."""

from __future__ import annotations

import json

from genesis.strategy_skill.storage import resolve_strategy_file, save_strategy_json


def test_save_and_resolve_strategy_json(tmp_path, monkeypatch):
    monkeypatch.setattr("genesis.strategy_skill.storage.GENERATED_DIR", tmp_path)

    strategy = {
        "market_scope": {"primary_asset": "BNB"},
        "hackathon": {"track": 2},
    }
    filename, path = save_strategy_json(strategy)
    assert filename.startswith("genesis-strategy-bnb-")
    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["market_scope"]["primary_asset"] == "BNB"

    resolved = resolve_strategy_file(filename)
    assert resolved == path
    assert resolve_strategy_file("../secrets.json") is None
    assert resolve_strategy_file("other.json") is None