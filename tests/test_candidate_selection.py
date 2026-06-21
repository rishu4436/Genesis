"""Tests for bullish BUY candidate prioritization."""

from genesis.core.models import CompositeSignal
from genesis.decision.candidate_selection import pick_best_buy_candidate


def _bullish(
    symbol: str,
    conviction: float,
    *,
    rank: int = 0,
    mcap: float = 0.0,
) -> CompositeSignal:
    return CompositeSignal(
        symbol=symbol,
        conviction=conviction,
        direction="bullish",
        components={"technicals": conviction},
        features={"cmc_rank": rank, "market_cap_usd": mcap},
        summary=symbol,
    )


def test_prefers_higher_market_cap_over_higher_conviction():
    candidates = [
        _bullish("TAG", 0.72, rank=900, mcap=50_000_000),
        _bullish("BNB", 0.65, rank=4, mcap=90_000_000_000),
    ]
    best = pick_best_buy_candidate(candidates)
    assert best.symbol == "BNB"


def test_uses_cmc_rank_when_market_cap_missing():
    candidates = [
        _bullish("SMALL", 0.80, rank=500),
        _bullish("ETH", 0.62, rank=2),
    ]
    best = pick_best_buy_candidate(candidates)
    assert best.symbol == "ETH"


def test_conviction_tiebreaks_same_market_cap():
    candidates = [
        _bullish("CAKE", 0.61, rank=100, mcap=1_000_000_000),
        _bullish("LINK", 0.68, rank=100, mcap=1_000_000_000),
    ]
    best = pick_best_buy_candidate(candidates)
    assert best.symbol == "LINK"