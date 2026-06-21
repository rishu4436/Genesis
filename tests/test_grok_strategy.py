"""Tests for Grok-powered Track 2 strategy assistant."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from genesis.core.config import EnvSettings
from genesis.core.models import RulesConfig, TokenConfig
from genesis.strategy_skill.grok_generator import (
    GrokStrategyIntent,
    _is_market_wide_question,
    build_cmc_market_context,
    extract_symbols_from_text,
    generate_strategy_from_text,
    handle_strategy_chat,
)
from genesis.strategy_skill.models import StrategyConditions


def _rules_with_tokens() -> RulesConfig:
    rules = RulesConfig()
    rules.allowed_tokens = [
        TokenConfig(symbol="BNB", address="0x1", cmc_id=1839),
        TokenConfig(symbol="CAKE", address="0x2", cmc_id=7186),
        TokenConfig(symbol="LINK", address="0x3", cmc_id=1975),
    ]
    return rules


def test_is_market_wide_question():
    assert _is_market_wide_question("What's happening in the market today?")
    assert not _is_market_wide_question("What's CAKE price?")


def test_extract_symbols_from_text():
    rules = _rules_with_tokens()
    assert extract_symbols_from_text("buy CAKE on dips", rules) == ["CAKE"]
    assert extract_symbols_from_text("BNB and LINK momentum", rules) == ["BNB", "LINK"]
    assert extract_symbols_from_text("general crypto question", rules) == []


@pytest.mark.asyncio
async def test_build_cmc_market_context():
    from genesis.core.models import Signal, SignalCategory

    rules = _rules_with_tokens()
    provider = MagicMock()
    provider._get_global_metrics = AsyncMock(
        return_value={
            "sentiment": {"fear_greed": {"current": {"index": 62, "value_classification": "Greed"}}},
            "quote": {"USD": {"total_market_cap": 2_500_000_000_000}},
        }
    )
    provider.get_global_derivatives_signal = AsyncMock(
        return_value=Signal(
            category=SignalCategory.MARKET,
            symbol="MARKET",
            value=0.1,
            raw_data={},
            source="test",
            summary="Global funding neutral",
        )
    )
    provider.get_narratives = AsyncMock(
        return_value=Signal(
            category=SignalCategory.COMPOSITE,
            symbol="MARKET",
            value=0.2,
            raw_data={},
            source="test",
            summary="Narratives mixed",
        )
    )
    provider.get_macro_events = AsyncMock(return_value={"rows": []})
    provider.get_all_signals = AsyncMock(
        return_value=[
            Signal(
                category=SignalCategory.QUOTE,
                symbol="CAKE",
                value=0.3,
                raw_data={"price": 2.5, "percent_change_24h": -5.0},
                source="test",
                summary="CAKE $2.50, 24h -5%",
            )
        ]
    )

    context, symbols, sources = await build_cmc_market_context(provider, rules, "What's CAKE price?")
    assert symbols == ["CAKE"]
    assert context["pipeline"] == "cmc_mcp_facts_then_genesis_reasoning"
    assert "CAKE" in context["assets"]
    assert context["assets"]["CAKE"]["price_usd"] == 2.5
    assert "genesis_llm_reasoning" in sources


@pytest.mark.asyncio
async def test_handle_strategy_chat_generates_strategy():
    rules = _rules_with_tokens()
    env = EnvSettings(xai_api_key="test-key", llm_provider="grok")
    provider = MagicMock()
    provider._get_global_metrics = AsyncMock(return_value={})
    provider.get_global_derivatives_signal = AsyncMock(return_value=MagicMock(summary="ok", value=0))
    provider.get_narratives = AsyncMock(return_value=MagicMock(summary="ok", value=0))
    provider.get_macro_events = AsyncMock(return_value={})
    provider.get_all_signals = AsyncMock(return_value=[])

    intent = GrokStrategyIntent(
        intent="generate_strategy",
        reply="Conservative BNB strategy with 15% take profit.",
        conditions=StrategyConditions(
            primary_asset="BNB",
            risk_profile="conservative",
            take_profit_pct=15.0,
            backtest_limit=0,
        ),
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=intent)

    with patch(
        "genesis.strategy_skill.grok_generator.create_instructor_client",
        return_value=mock_client,
    ):
        result = await handle_strategy_chat(
            env,
            rules,
            provider,
            "conservative BNB 15% TP",
            include_backtest=False,
        )

    assert result.intent == "generate_strategy"
    assert result.strategy is not None
    assert result.strategy["market_scope"]["primary_asset"] == "BNB"
    assert result.strategy["exit_rules"]["take_profit"]["value_pct"] == 15.0
    assert "Conservative" in result.reply or "BNB" in result.reply


@pytest.mark.asyncio
async def test_handle_strategy_chat_answer_intent():
    rules = _rules_with_tokens()
    env = EnvSettings(xai_api_key="test-key")
    provider = MagicMock()
    provider._get_global_metrics = AsyncMock(return_value={})
    provider.get_global_derivatives_signal = AsyncMock(return_value=MagicMock(summary="ok", value=0))
    provider.get_narratives = AsyncMock(return_value=MagicMock(summary="ok", value=0))
    provider.get_macro_events = AsyncMock(return_value={})
    provider.get_all_signals = AsyncMock(return_value=[])

    intent = GrokStrategyIntent(
        intent="answer",
        reply="CAKE is around $2.50, down 3% on 24h.",
        conditions=None,
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=intent)

    with patch(
        "genesis.strategy_skill.grok_generator.create_instructor_client",
        return_value=mock_client,
    ):
        result = await handle_strategy_chat(env, rules, provider, "What's CAKE doing?")

    assert result.intent == "answer"
    assert result.strategy is None
    assert "CAKE" in result.reply


@pytest.mark.asyncio
async def test_handle_strategy_chat_no_api_key():
    rules = _rules_with_tokens()
    env = EnvSettings(xai_api_key="")
    provider = MagicMock()
    provider._get_global_metrics = AsyncMock(return_value={})
    provider.get_global_derivatives_signal = AsyncMock(return_value=MagicMock(summary="ok", value=0))
    provider.get_narratives = AsyncMock(return_value=MagicMock(summary="ok", value=0))
    provider.get_macro_events = AsyncMock(return_value={})
    provider.get_all_signals = AsyncMock(return_value=[])

    result = await handle_strategy_chat(env, rules, provider, "make a strategy")
    assert result.intent == "clarify"
    assert "XAI_API_KEY" in result.reply or "not fully online" in result.reply


@pytest.mark.asyncio
async def test_generate_strategy_from_text():
    rules = _rules_with_tokens()
    env = EnvSettings(xai_api_key="test-key")
    provider = MagicMock()
    provider._get_global_metrics = AsyncMock(return_value={})
    provider.get_global_derivatives_signal = AsyncMock(return_value=MagicMock(summary="ok", value=0))
    provider.get_narratives = AsyncMock(return_value=MagicMock(summary="ok", value=0))
    provider.get_macro_events = AsyncMock(return_value={})
    provider.get_all_signals = AsyncMock(return_value=[])

    intent = GrokStrategyIntent(
        intent="generate_strategy",
        reply="Aggressive LINK strategy.",
        conditions=StrategyConditions(primary_asset="LINK", risk_profile="aggressive", backtest_limit=0),
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=intent)

    with patch(
        "genesis.strategy_skill.grok_generator.create_instructor_client",
        return_value=mock_client,
    ):
        result = await generate_strategy_from_text(env, rules, provider, "aggressive LINK")

    assert result.strategy is not None
    assert result.strategy["market_scope"]["primary_asset"] == "LINK"