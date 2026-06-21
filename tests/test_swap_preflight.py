"""Tests for TWAK swap preflight and buy rerouting."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from genesis.core.models import Action, CompositeSignal, Decision, RulesConfig, TokenConfig
from genesis.execution.swap_preflight import find_swappable_buy_asset, ordered_buy_attempts


def _composite(symbol: str, conviction: float = 0.65) -> CompositeSignal:
    return CompositeSignal(
        symbol=symbol,
        conviction=conviction,
        direction="bullish",
        components={
            "technicals": 0.62,
            "sentiment": 0.58,
            "onchain": 0.56,
            "news": 0.55,
        },
        features={"cmc_rank": 4 if symbol == "BNB" else 500, "market_cap_usd": 1e10},
        summary=symbol,
    )


def test_ordered_buy_attempts_puts_llm_pick_first():
    rules = RulesConfig()
    decision = Decision(action=Action.BUY, asset="AAVE", reason="test", confidence=0.63)
    composites = [_composite("BNB"), _composite("AAVE"), _composite("LINK")]

    ordered = ordered_buy_attempts(decision, composites, rules)

    assert ordered[0] == "AAVE"
    assert "BNB" in ordered


@pytest.mark.asyncio
async def test_find_swappable_buy_asset_falls_back():
    rules = RulesConfig()
    rules.allowed_tokens = [
        TokenConfig(symbol="USDT", address="0x55d398326f99059fF775485246999027B3197955", cmc_id=825),
        TokenConfig(symbol="AAVE", address="0xfb6115445Bff7b52FeB98650C87f89407e58f802", cmc_id=7278),
        TokenConfig(symbol="BNB", address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", cmc_id=1839),
    ]
    twak = AsyncMock()

    async def _quote(from_token, to_token, amount_usd, **kwargs):
        if to_token.upper() == "AAVE":
            from genesis.execution.twak_provider import TWAKError

            raise TWAKError("API error: 400 Bad Request")
        return {"output": "1 BNB"}

    twak.quote_swap.side_effect = _quote

    decision = Decision(action=Action.BUY, asset="AAVE", reason="llm", confidence=0.63)
    composites = [_composite("AAVE"), _composite("BNB")]

    symbol, err = await find_swappable_buy_asset(
        twak,
        decision=decision,
        composites=composites,
        rules=rules,
        quote="USDT",
        size_usd=2.0,
        slippage_bps=100,
        allowed_tokens=rules.allowed_tokens,
    )

    assert symbol == "BNB"
    assert err is None