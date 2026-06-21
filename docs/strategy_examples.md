# Genesis Strategy Examples

## Conservative Momentum + Sentiment Fusion (Default)

**Philosophy:** Only act when multiple independent signals align. Default to HOLD.

### BUY Conditions (all must be true)
- Composite conviction >= 0.60
- Technicals bullish (RSI > 60, positive MACD)
- Sentiment score >= 0.55
- Funding rate within ±1% (neutral, not crowded)
- On-chain accumulation detected
- News sentiment supportive
- Portfolio drawdown < max limit
- Confidence >= 0.65

### SELL Conditions
- Composite conviction <= 0.35, OR
- Drawdown approaching limit, OR
- Bearish technical breakdown

### Position Sizing
- Max 2% of portfolio per trade
- Adjusted down automatically if risk budget exceeded
- Minimum $5, maximum $500 per trade

## Configuration

Edit `config/rules.yaml`:

```yaml
risk:
  max_portfolio_risk_per_trade_pct: 2.0
  max_drawdown_pct: 10.0
  min_confidence: 0.65

signals:
  buy_conviction_min: 0.60
  sell_conviction_max: 0.35

signal_weights:
  technicals: 0.30
  sentiment: 0.20
  derivatives: 0.15
  onchain: 0.20
  news: 0.15
```

## Iteration Tips

1. **Tighten for safety:** Lower `max_portfolio_risk_per_trade_pct` to 1.0
2. **More active:** Lower `buy_conviction_min` to 0.55, reduce `cooldown_minutes`
3. **Sentiment-heavy:** Increase `sentiment` weight to 0.35
4. **Custom prompt:** Change `strategy.prompt_template` and edit `llm_prompts.py`