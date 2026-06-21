"""Tests for Track 2 strategy skill export, generator, and backtest."""

from __future__ import annotations

from genesis.core.models import RulesConfig
from genesis.strategy_skill.backtest import backtest_from_audits
from genesis.strategy_skill.builder import build_strategy_spec, export_strategy_skill
from genesis.strategy_skill.generator import generate_strategy
from genesis.strategy_skill.models import StrategyConditions


def test_build_strategy_spec_track_2():
    rules = RulesConfig()
    spec = build_strategy_spec(rules)
    assert spec["hackathon"]["track"] == 2
    assert spec["hackathon"]["track_name"] == "Strategy Skills"
    assert "entry_rules" in spec
    assert "market_scope" in spec
    assert "indicators" in spec
    assert "position_sizing" in spec
    assert "expected_performance" in spec
    assert "get_crypto_technical_analysis" in spec["data_sources"]["tools"]


def test_generate_strategy_respects_conditions():
    rules = RulesConfig()
    conditions = StrategyConditions(
        primary_asset="CAKE",
        risk_profile="aggressive",
        market_regime="volatile",
        take_profit_pct=15.0,
        stop_loss_pct=8.0,
    )
    spec = generate_strategy(conditions, rules)
    assert spec["market_scope"]["primary_asset"] == "CAKE"
    assert spec["market_scope"]["market_regime"] == "volatile"
    assert spec["exit_rules"]["take_profit"]["value_pct"] == 15.0
    assert spec["exit_rules"]["stop_loss"]["value_pct"] == 8.0
    assert spec["entry_rules"]["conservative"]["min_conviction"] == 0.50
    assert len(spec["indicators"]) >= 5


def test_export_strategy_skill_writes_files(tmp_path, monkeypatch):
    rules = RulesConfig()
    monkeypatch.setattr(
        "genesis.strategy_skill.builder.SKILL_DIR",
        tmp_path / "skills" / "genesis-momentum-sentiment",
    )
    monkeypatch.setattr(
        "genesis.strategy_skill.builder.SPEC_PATH",
        tmp_path / "data" / "strategy_spec.json",
    )
    monkeypatch.setattr(
        "genesis.strategy_skill.builder.SKILL_PATH",
        tmp_path / "skills" / "genesis-momentum-sentiment" / "SKILL.md",
    )

    paths = export_strategy_skill(rules)
    assert paths["skill_md"].endswith("SKILL.md")
    assert (tmp_path / "data" / "strategy_spec.json").exists()
    content = (tmp_path / "skills" / "genesis-momentum-sentiment" / "SKILL.md").read_text()
    assert "genesis-momentum-sentiment" in content
    assert "Track 2" in content


def test_backtest_from_audits_counts_signals():
    rules = RulesConfig()
    audits = [
        {
            "composites": [
                {
                    "symbol": "CAKE",
                    "conviction": 0.72,
                    "direction": "bullish",
                    "components": {
                        "technicals": 0.7,
                        "sentiment": 0.65,
                        "onchain": 0.6,
                        "news": 0.55,
                    },
                },
                {
                    "symbol": "BNB",
                    "conviction": 0.30,
                    "direction": "bearish",
                    "components": {"technicals": 0.3},
                },
            ]
        }
    ]
    result = backtest_from_audits(audits, rules)
    assert result["signals_evaluated"] == 2
    assert result["buy_signals"] >= 1
    assert result["sell_signals"] >= 1
    assert "simulated_round_trips" in result
    assert "estimated_win_rate_pct" in result