---
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

Multi-signal momentum strategy: fuse CMC technicals (28%), sentiment (18%), derivatives (14%), on-chain (18%), and news (12%) into a 0–1 conviction score. BUY when conviction ≥ 0.60 with ≥2 aligned core signals; SELL when conviction ≤ 0.35. After 10 idle cycles without a swap, relax gates to encourage at least one trade, then revert.

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
conviction = weighted_mean(components, weights={
  technicals: 0.28,
  sentiment: 0.18,
  derivatives: 0.14,
  onchain: 0.18,
  news: 0.12
})
```

### 4. Entry / exit rules

**BUY (conservative)**

- `direction == bullish`
- `conviction >= 0.6`
- At least **2** of [technicals, sentiment, onchain, news] ≥ 0.55
- Pick largest market-cap candidate among eligible tokens

**SELL**

- Held position AND `conviction <= 0.35`

**Adaptive aggression**

- After **10** cycles with no swap:
  - Lower buy conviction to **0.52**
  - Allow **neutral** tokens with conviction ≥ 0.55
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

- Max drawdown halt: 30.0%
- Max open positions: 6
- Spot size: 5.0% of USDT/USDC per swap
- Cooldown: 30 minutes per token

## Track 1 bridge

This skill is the **strategy brain** behind the Genesis autonomous agent. Track 1 adds TWAK execution, ERC-8004 identity, and live BSC swaps using the same rules.
