"""Build Track 2 strategy skill spec and SKILL.md from Genesis rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from genesis.core.config import PROJECT_ROOT, RulesConfig
from genesis.strategy_skill.generator import generate_strategy
from genesis.strategy_skill.models import StrategyConditions

SKILL_DIR = PROJECT_ROOT / "skills" / "genesis-momentum-sentiment"
SPEC_PATH = PROJECT_ROOT / "data" / "strategy_spec.json"
SKILL_PATH = SKILL_DIR / "SKILL.md"

CMC_TOOLS_USED = [
    "get_crypto_quotes_latest",
    "get_crypto_technical_analysis",
    "get_crypto_metrics",
    "get_crypto_latest_news",
    "get_global_metrics_latest",
    "get_global_crypto_derivatives_metrics",
    "get_crypto_marketcap_technical_analysis",
    "trending_crypto_narratives",
    "get_upcoming_macro_events",
    "search_cryptos",
    "get_crypto_info",
]


def build_strategy_spec(
    rules: RulesConfig,
    conditions: StrategyConditions | None = None,
) -> dict[str, Any]:
    """Machine-readable backtestable strategy spec (Track 2 deliverable)."""
    cond = conditions or StrategyConditions(
        primary_asset="BNB",
        timeframe="5m",
        risk_profile="conservative",
        market_regime="bullish",
        take_profit_pct=12.0,
        stop_loss_pct=6.0,
    )
    return generate_strategy(cond, rules)


def _skill_markdown(rules: RulesConfig, spec: dict[str, Any]) -> str:
    s = rules.signals
    w = rules.signal_weights
    return f"""---
name: genesis-momentum-sentiment
description: |
  Genesis Track 2 Strategy Skill — multi-signal momentum + sentiment fusion using CoinMarketCap MCP.
  Turns live CMC data (RSI, MACD, Fear & Greed, holders, funding, news) into backtestable entry/exit rules.
  Use for strategy generation, backtests, or feeding an autonomous agent (Track 1).
  Trigger: "genesis strategy", "momentum sentiment skill", "backtest BNB strategy", "/genesis-skill"
license: MIT
compatibility: ">=1.0.0"
user-invocable: true
metadata:
  hackathon_track: 2
  hackathon_track_name: Strategy Skills
  genesis_track_1_compatible: true
allowed-tools:
  - mcp__cmc-mcp__get_crypto_quotes_latest
  - mcp__cmc-mcp__get_crypto_technical_analysis
  - mcp__cmc-mcp__get_crypto_metrics
  - mcp__cmc-mcp__get_crypto_latest_news
  - mcp__cmc-mcp__get_global_metrics_latest
  - mcp__cmc-mcp__get_global_crypto_derivatives_metrics
  - mcp__cmc-mcp__get_crypto_marketcap_technical_analysis
  - mcp__cmc-mcp__trending_crypto_narratives
  - mcp__cmc-mcp__get_upcoming_macro_events
  - mcp__cmc-mcp__search_cryptos
  - mcp__cmc-mcp__get_crypto_info
---

# Genesis Momentum + Sentiment Strategy Skill (Track 2)

{spec['natural_language_summary']}

## When to use this skill

- User wants a **backtestable crypto strategy** without running a live trading agent
- User asks how to combine **RSI, MACD, Fear & Greed, on-chain holders, and news** into rules
- User wants the **same logic** that powers the Genesis autonomous agent (Track 1)

## Workflow

### 1. Fetch CMC signals per token

For each token symbol:

1. `search_cryptos` → resolve CMC id
2. `get_crypto_technical_analysis` → RSI14, MACD histogram
3. `get_global_metrics_latest` → Fear & Greed (sentiment macro)
4. `get_crypto_metrics` → holder counts, trader activity
5. `get_crypto_latest_news` → bull/bear article ratio
6. `get_global_crypto_derivatives_metrics` → funding, open interest

### 2. Normalize to component scores (0–1)

| Component | CMC source | Bullish when |
|-----------|------------|--------------|
| technicals | RSI14, MACD | RSI ≥ 60 or ≤ 40 with MACD confirmation |
| sentiment | Fear & Greed, social | Score > 0.55 |
| derivatives | Funding, OI delta | Neutral funding, stable OI |
| onchain | Holders, 30d change | Accumulation (holders up) |
| news | Article sentiment ratio | Bullish > bearish |

### 3. Fuse into conviction

```
conviction = weighted_mean(components, weights={{
  technicals: {w.technicals},
  sentiment: {w.sentiment},
  derivatives: {w.derivatives},
  onchain: {w.onchain},
  news: {w.news}
}})
```

### 4. Entry / exit rules

**BUY (conservative)**

- `direction == bullish`
- `conviction >= {s.buy_conviction_min}`
- At least **2** of [technicals, sentiment, onchain, news] ≥ 0.55
- Pick largest market-cap candidate among eligible tokens

**SELL**

- Held position AND `conviction <= {s.sell_conviction_max}`

**Adaptive aggression**

- After **{rules.loop.adaptive_aggression.idle_cycles_threshold}** cycles with no swap:
  - Lower buy conviction to **{rules.loop.adaptive_aggression.buy_conviction_min}**
  - Allow **neutral** tokens with conviction ≥ {rules.loop.adaptive_aggression.neutral_conviction_min}
  - Force best candidate if no strict match

### 5. Backtest

Run against Genesis audit history:

```bash
python -m genesis.cli strategy-skill backtest --limit 50
```

Or export the machine-readable spec:

```bash
python -m genesis.cli strategy-skill export
```

Output: `data/strategy_spec.json` + `skills/genesis-momentum-sentiment/SKILL.md`

## Risk envelope (optional for live agents)

- Max drawdown halt: {rules.risk.max_drawdown_pct}%
- Max open positions: {rules.risk.max_open_positions}
- Spot size: {rules.risk.spot_stable_pct}% of USDT/USDC per swap
- Cooldown: {rules.risk.cooldown_minutes} minutes per token

## Track 1 bridge

This skill is the **strategy brain** behind the Genesis autonomous agent. Track 1 adds TWAK execution, ERC-8004 identity, and live BSC swaps using the same rules.
"""


def export_strategy_skill(rules: RulesConfig) -> dict[str, str]:
    """Write SKILL.md and strategy_spec.json; return paths."""
    spec = build_strategy_spec(rules)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)

    SKILL_PATH.write_text(_skill_markdown(rules, spec), encoding="utf-8")
    SPEC_PATH.write_text(json.dumps(spec, indent=2), encoding="utf-8")

    return {
        "skill_md": str(SKILL_PATH),
        "spec_json": str(SPEC_PATH),
    }