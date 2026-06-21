"""Pydantic models for Track 2 Strategy Skill Generator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


RiskProfile = Literal["conservative", "moderate", "aggressive"]
MarketRegime = Literal["bullish", "bearish", "neutral", "volatile"]


class StrategyConditions(BaseModel):
    """User-supplied market conditions for strategy generation."""

    primary_asset: str = Field(default="BNB", description="Primary asset symbol")
    timeframe: str = Field(default="5m", description="Candle / cycle timeframe")
    risk_profile: RiskProfile = "conservative"
    market_regime: MarketRegime = "bullish"
    take_profit_pct: float = Field(default=12.0, ge=1.0, le=100.0)
    stop_loss_pct: float = Field(default=6.0, ge=0.5, le=50.0)
    focus_signals: list[str] = Field(
        default_factory=lambda: ["technicals", "sentiment", "onchain", "news"],
        description="Signal components to emphasize",
    )
    backtest_limit: int = Field(default=50, ge=0, le=200)
    idle_cycles: int = Field(default=0, ge=0, le=100)


class GenerateStrategyResponse(BaseModel):
    """API response wrapping generated strategy + optional backtest preview."""

    strategy: dict
    conditions: StrategyConditions
    backtest_preview: dict | None = None