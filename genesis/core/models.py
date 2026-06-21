"""Pydantic models for signals, decisions, trades, and configuration."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from genesis.utils import utc_now


class Action(str, Enum):
    """Trading action enum."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class TradeStatus(str, Enum):
    """Trade execution status."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    SIMULATED = "simulated"


class SignalCategory(str, Enum):
    """CMC signal categories."""

    QUOTE = "quote"
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"
    ONCHAIN = "onchain"
    DERIVATIVES = "derivatives"
    NEWS = "news"
    COMPOSITE = "composite"
    METADATA = "metadata"
    DISCOVERY = "discovery"
    MARKET = "market"
    MACRO = "macro"


class TokenConfig(BaseModel):
    """Allowed token configuration."""

    symbol: str
    address: str
    cmc_id: int


class RiskRules(BaseModel):
    """Hard risk limits enforced before execution."""

    max_portfolio_risk_per_trade_pct: float = 2.0
    max_drawdown_pct: float = 30.0
    max_open_positions: int = 3
    max_slippage_bps: int = 100
    min_confidence: float = 0.65
    cooldown_minutes: int = 30
    spot_stable_pct: float = 5.0
    min_swap_usd: float = 1.0


class SignalWeights(BaseModel):
    """Weights for signal fusion."""

    technicals: float = 0.28
    sentiment: float = 0.18
    derivatives: float = 0.14
    onchain: float = 0.18
    news: float = 0.12
    metadata: float = 0.05
    discovery: float = 0.05

    @field_validator(
        "technicals",
        "sentiment",
        "derivatives",
        "onchain",
        "news",
        "metadata",
        "discovery",
    )
    @classmethod
    def non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Signal weights must be non-negative")
        return v


class SignalThresholds(BaseModel):
    """Composite signal thresholds."""

    buy_conviction_min: float = 0.60
    sell_conviction_max: float = 0.35
    funding_rate_neutral_max: float = 0.01
    sentiment_positive_min: float = 0.55


class AdaptiveAggressionConfig(BaseModel):
    """Relax buy rules after N consecutive cycles without an executed swap."""

    enabled: bool = True
    idle_cycles_threshold: int = 10
    buy_conviction_min: float = 0.52
    min_aligned_components: int = 1
    bullish_component_min: float = 0.50
    min_technicals: float = 0.48
    min_sentiment: float = 0.48
    allow_neutral_direction: bool = True
    neutral_conviction_min: float = 0.55
    force_best_candidate: bool = True


class DemoScanConfig(BaseModel):
    """Faster cycles for live demos — scan fewer tokens, optionally skip LLM."""

    enabled: bool = False
    token_limit: int = 14
    concurrency: int = 8
    priority_symbols: list[str] = Field(
        default_factory=lambda: [
            "BNB",
            "CAKE",
            "ETH",
            "DOGE",
            "ADA",
            "XRP",
            "LINK",
            "AAVE",
            "UNI",
            "DOT",
            "AVAX",
            "BONK",
            "SHIB",
            "PEPE",
        ]
    )
    rule_based_only: bool = True


class LoopConfig(BaseModel):
    """Agent loop configuration."""

    interval_seconds: int = 300
    min_trades_per_day: int = 1
    signal_change_threshold: float = 0.15
    adaptive_aggression: AdaptiveAggressionConfig = Field(
        default_factory=AdaptiveAggressionConfig
    )
    demo: DemoScanConfig = Field(default_factory=DemoScanConfig)


class ExecutionConfig(BaseModel):
    """DEX execution settings."""

    dex: str = "pancakeswap"
    default_quote: str = "USDT"
    gas_buffer_pct: int = 10
    retry_attempts: int = 3
    retry_delay_seconds: int = 5


class ExitRulesConfig(BaseModel):
    """Spot exit rules — take-profit replaces conviction-based sells when enabled."""

    take_profit_pct: float = 10.0
    prefer_take_profit_over_conviction: bool = True


class DcaDipConfig(BaseModel):
    """
    DCA dip-buy: top-N CMC coins down >= trigger in 24h, then ladder buys every step_drop
    until stables depleted; exit at take_profit_pct from average entry.
    """

    enabled: bool = False
    max_cmc_rank: int = 100
    trigger_drop_24h_pct: float = 20.0
    step_drop_pct: float = 10.0
    take_profit_pct: float = 20.0
    max_concurrent_positions: int = 3


class DcaPositionState(BaseModel):
    """Persisted DCA ladder state per symbol."""

    symbol: str
    buy_count: int = 0
    last_buy_price_usd: float = 0.0
    avg_entry_price_usd: float = 0.0
    total_cost_usd: float = 0.0
    trigger_change_24h_pct: float = 0.0
    active: bool = True


class PerpsConfig(BaseModel):
    """PancakeSwap Perps (ApolloX Diamond) settings."""

    enabled: bool = False
    max_leverage: int = 5
    exchange: str = "pancakeswap_perps"
    margin_stable_pct: float = 4.0
    collateral_token: str = "USDT"
    default_slippage_bps: int = 50
    allowed_symbols: list[str] = Field(
        default_factory=lambda: [
            "TWT",
            "UNI",
            "ETH",
            "ASTER",
            "XRP",
            "TRX",
            "BNB",
            "BTC",
            "BTCB",
        ]
    )


class StrategyConfig(BaseModel):
    """Strategy metadata."""

    name: str = "conservative_momentum_sentiment"
    description: str = ""
    prompt_template: str = "default"


class RulesConfig(BaseModel):
    """Full rules.yaml schema."""

    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    risk: RiskRules = Field(default_factory=RiskRules)
    allowed_tokens: list[TokenConfig] = Field(default_factory=list)
    preferred_pairs: list[str] = Field(default_factory=list)
    signal_weights: SignalWeights = Field(default_factory=SignalWeights)
    signals: SignalThresholds = Field(default_factory=SignalThresholds)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    exit: ExitRulesConfig = Field(default_factory=ExitRulesConfig)
    dca_dip: DcaDipConfig = Field(default_factory=DcaDipConfig)
    perps: PerpsConfig = Field(default_factory=PerpsConfig)


class Signal(BaseModel):
    """Individual market signal from a data source."""

    category: SignalCategory
    symbol: str
    value: float = Field(ge=-1.0, le=1.0, description="Normalized -1 to 1")
    raw_data: dict[str, Any] = Field(default_factory=dict)
    source: str = "cmc"
    timestamp: datetime = Field(default_factory=utc_now)
    summary: str = ""


class CompositeSignal(BaseModel):
    """Fused multi-source signal."""

    symbol: str
    conviction: float = Field(ge=0.0, le=1.0)
    direction: str  # bullish | bearish | neutral
    components: dict[str, float] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
    summary: str = ""


class Decision(BaseModel):
    """Structured LLM trading decision (instructor output)."""

    action: Action
    asset: str
    size_usd: float | None = None
    size_pct: float | None = None
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    risk_notes: str = ""
    signals_used: list[str] = Field(default_factory=list)
    current_price_usd: float | None = None
    take_profit_pct: float | None = None
    take_profit_price_usd: float | None = None
    exit_trigger: str | None = None
    strategy_mode: str | None = None
    change_24h_pct: float | None = None
    dca_step: int | None = None
    timestamp: datetime = Field(default_factory=utc_now)

    @field_validator("size_pct")
    @classmethod
    def validate_size_pct(cls, v: float | None) -> float | None:
        if v is not None and (v <= 0 or v > 100):
            raise ValueError("size_pct must be between 0 and 100")
        return v


class RiskValidation(BaseModel):
    """Result of risk manager validation."""

    approved: bool
    reason: str
    adjusted_size_usd: float | None = None
    violations: list[str] = Field(default_factory=list)


class Trade(BaseModel):
    """Executed or simulated trade record."""

    id: str | None = None
    decision_id: str | None = None
    symbol: str
    side: Action
    amount_usd: float
    amount_token: float | None = None
    price: float | None = None
    slippage_bps: int | None = None
    tx_hash: str | None = None
    status: TradeStatus = TradeStatus.PENDING
    gas_used: int | None = None
    error: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    simulated: bool = False
    execution_type: str = "spot"
    position_id: str | None = None
    leverage: int | None = None


class Position(BaseModel):
    """Open position snapshot."""

    symbol: str
    amount: float
    entry_price: float
    current_price: float | None = None
    unrealized_pnl_usd: float = 0.0
    unrealized_pnl_pct: float = 0.0
    opened_at: datetime = Field(default_factory=datetime.utcnow)


class PortfolioSnapshot(BaseModel):
    """Point-in-time portfolio state."""

    total_value_usd: float
    available_usd: float
    positions: list[Position] = Field(default_factory=list)
    daily_pnl_usd: float = 0.0
    cumulative_pnl_usd: float = 0.0
    drawdown_pct: float = 0.0
    peak_value_usd: float = 0.0
    timestamp: datetime = Field(default_factory=utc_now)


class MarketContext(BaseModel):
    """Market-wide CMC context fetched once per agent cycle."""

    signals: list[Signal] = Field(default_factory=list)
    blocks_buys: bool = False
    block_reason: str = ""
    market_conviction_delta: float = 0.0


class AuditRecord(BaseModel):
    """Full audit trail entry for a decision cycle."""

    cycle_id: str
    market_context: MarketContext | None = None
    signals: list[Signal] = Field(default_factory=list)
    composites: list[CompositeSignal] = Field(default_factory=list)
    composite: CompositeSignal | None = None
    decision: Decision | None = None
    risk_validation: RiskValidation | None = None
    trade: Trade | None = None
    portfolio: PortfolioSnapshot | None = None
    duration_ms: int = 0
    timestamp: datetime = Field(default_factory=utc_now)