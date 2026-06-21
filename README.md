# Genesis

**Self-Custody Autonomous Trader on BSC Powered by CMC + TWAK + BNB SDK**

Genesis is an autonomous AI trading agent for the [BNB Chain × CoinMarketCap × Trust Wallet AI Trading Agent Hackathon](https://dora-hacks.io/hackathon/bnb-trading-agent). It supports **both hackathon tracks**:

- **Track 1 — Autonomous Trading Agents**: Live BSC agent with CMC signal fusion, LLM/rules decisions, TWAK execution, and ERC-8004 identity.
- **Track 2 — Strategy Skills**: A **Strategy Skill Generator** that turns market conditions into a complete, backtestable strategy JSON (indicators, entry/exit, sizing, risk rules) — shareable as a skill without running live trades.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GENESIS AGENT LOOP                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐    ┌──────────────┐    ┌──────────┐    ┌─────────┐  │
│  │ CMC MCP  │───▶│   Signal     │───▶│ Strategy │───▶│  Risk   │  │
│  │ 12+ tools│    │  Aggregator  │    │  Engine  │    │ Manager │  │
│  └──────────┘    └──────────────┘    │ (LLM)    │    │ (HARD)  │  │
│       │                              └──────────┘    └────┬────┘  │
│       │ x402                                              │        │
│       ▼                                              approved      │
│  ┌──────────┐    ┌──────────────┐    ┌──────────┐         │        │
│  │ Premium  │    │    TWAK      │◀───│ Execute  │◀────────┘        │
│  │   Data   │    │ Local Signing│    │  Trade   │                  │
│  └──────────┘    └──────┬───────┘    └──────────┘                  │
│                         │                                           │
│                    ┌────▼─────┐    ┌──────────────┐                  │
│                    │PancakeSwap│    │ BNB SDK     │                  │
│                    │Spot/Perps │    │ ERC-8004 ID │                  │
│                    └──────────┘    └──────────────┘                  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ SQLite Audit DB: signals, decisions, trades, portfolio     │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

See [`docs/architecture.mmd`](docs/architecture.mmd) for the full Mermaid diagram.

---

## Features

- **Autonomous loop** — Configurable interval (default 5 min), signal-change triggers
- **CMC Agent Hub** — MCP-first integration with 6+ data categories (quotes, technicals, sentiment, on-chain, derivatives, news)
- **TWAK deep integration** — Autonomous wallet, local signing, PancakeSwap swaps, x402 payments
- **BNB AI Agent SDK** — ERC-8004 on-chain identity, gas-free testnet registration
- **Structured LLM decisions** — `instructor` + Pydantic, configurable Grok/OpenAI/Anthropic
- **Hard risk controls** — Drawdown halt, position sizing, token allowlist, slippage, cooldowns
- **Full audit trail** — Every signal, decision, trade, and portfolio snapshot in SQLite
- **CLI + Dashboard** — Typer CLI with rich output, FastAPI monitoring dashboard
- **Testnet-first** — Easy mainnet switch via `.env`

---

## Quick Start

### Prerequisites

- Python 3.11+
- [Trust Wallet Agent Kit (TWAK)](https://github.com/trustwallet/tw-agent-skills)
- API keys: xAI (Grok), CoinMarketCap (optional for fallback)

### Install

```bash
# Clone or navigate to project
cd genesis

# Automated setup (Linux/macOS)
bash scripts/setup.sh

# Or manual:
pip install -e ".[dev]"
genesis init
cp .env.example .env   # Edit with your keys
```

### Install TWAK

```bash
curl -fsSL https://raw.githubusercontent.com/trustwallet/tw-agent-skills/main/install.sh | bash
```

### Configure

Edit `.env`:

```env
XAI_API_KEY=your-xai-key
CMC_API_KEY=your-cmc-key          # Optional: MCP fallback
GENESIS_NETWORK=bsc-testnet
LLM_PROVIDER=grok
LLM_MODEL=grok-3-fast
```

Edit `config/rules.yaml` for strategy and risk parameters (see [Strategy Configuration](#strategy-configuration)).

### Run

```bash
# 1. Set up autonomous wallet (keys stay local)
genesis setup-wallet

# 2. Register for competition (ERC-8004 + hackathon)
genesis register-competition

# 3. Test with simulation (no on-chain txs)
genesis run --simulate --cycles 5

# 4. Live on testnet
genesis run

# 5. Monitor via dashboard
genesis dashboard
# Open http://localhost:8080
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `genesis init` | Initialize project, create dirs, validate config |
| `genesis setup-wallet` | Create TWAK autonomous agent wallet |
| `genesis register-competition` | ERC-8004 registration + hackathon signup |
| `genesis run` | Start autonomous trading loop |
| `genesis run --simulate` | Simulated execution (backtest) |
| `genesis status` | Show agent config and status |
| `genesis portfolio` | Current portfolio from TWAK |
| `genesis backtest --cycles 10` | Run N simulated cycles |
| `genesis logs` | View recent decision audit trail |
| `genesis logs --export data/audit.json` | Export full audit for judges |
| `genesis dashboard` | Launch monitoring dashboard |
| `genesis strategy-skill generate` | Generate Track 2 strategy JSON from conditions |
| `genesis strategy-skill export` | Export `SKILL.md` + `strategy_spec.json` |
| `genesis strategy-skill backtest` | Replay audit history through strategy gates |

---

## Track 2: Strategy Skill Generator

Track 2 runs **in parallel** with Track 1 — no changes to the live trading loop. Use the dashboard or CLI to produce a machine-readable strategy spec judges can backtest.

### Dashboard UI

```bash
genesis dashboard
# Track 1 live agent:  http://127.0.0.1:8080/app
# Track 2 generator:   http://127.0.0.1:8080/app/strategy
```

The Track 2 page lets you set asset, timeframe, risk profile, market regime, take-profit/stop-loss, then **Generate**, **Copy JSON**, or **Download** the spec. A backtest preview replays stored agent audits through the same entry/exit gates.

### CLI

```bash
# Generate strategy JSON (prints to stdout + optional backtest)
python -m genesis.cli strategy-skill generate --asset CAKE --risk aggressive --regime volatile

# Export hackathon deliverables
python -m genesis.cli strategy-skill export

# Backtest against audit DB
python -m genesis.cli strategy-skill backtest --limit 50 --idle 10
```

### API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/strategy-skill` | GET | Default strategy spec from `rules.yaml` |
| `/api/strategy-skill/generate` | POST | Generate from conditions (JSON body) |
| `/api/strategy-skill/backtest` | GET | Audit replay (`?limit=50&idle_cycles=0`) |
| `/api/strategy-skill/download` | GET | Download JSON file |

**Generate request example:**

```json
{
  "primary_asset": "BNB",
  "timeframe": "5m",
  "risk_profile": "conservative",
  "market_regime": "bullish",
  "take_profit_pct": 12.0,
  "stop_loss_pct": 6.0,
  "backtest_limit": 50,
  "idle_cycles": 0
}
```

### Strategy JSON structure

The generated spec includes everything needed for backtesting:

| Section | Contents |
|---------|----------|
| `market_scope` | Primary asset, timeframe, network, regime |
| `indicators` | RSI, MACD, Fear & Greed, holders, funding, news — with CMC tool sources and parameters |
| `signal_fusion` | Component weights and conviction formula |
| `entry_rules` | Buy gates (conviction, aligned signals, adaptive aggression) |
| `exit_rules` | Signal sell, take-profit %, stop-loss %, trailing stop |
| `position_sizing` | % of stables, min swap, max positions |
| `risk_management` | Drawdown halt, slippage, cooldown, allowlist |
| `expected_performance` | Target R:R, estimated win rate, optimal conditions |
| `backtest` | Method (`audit_replay`) and metrics |

Full example: [`data/strategy_spec.json`](data/strategy_spec.json). Skill deliverable: [`skills/genesis-momentum-sentiment/SKILL.md`](skills/genesis-momentum-sentiment/SKILL.md).

### How backtesting works

1. **Audit replay** — Genesis stores composite signals every agent cycle in SQLite.
2. The backtester replays each composite through the same `is_buy_eligible` / sell conviction gates as Track 1.
3. Metrics: buy/sell/hold counts, simulated round-trips, estimated win rate.
4. Optional `--idle N` simulates adaptive aggression after N cycles without a swap.

Track 1 and Track 2 share CMC data fetching, signal fusion weights, and rule gates — the skill is the **strategy brain**; Track 1 adds execution and identity.

---

## Strategy Configuration

Genesis uses a **conservative momentum + sentiment fusion** strategy by default. Edit `config/rules.yaml`:

```yaml
strategy:
  name: conservative_momentum_sentiment
  description: >
    Buy when technicals bullish + sentiment positive + funding neutral +
    on-chain accumulation + news supportive, within risk budget.

risk:
  max_portfolio_risk_per_trade_pct: 2.0   # Max 2% per trade
  max_drawdown_pct: 10.0                  # Halt at 10% drawdown
  min_confidence: 0.65
  max_slippage_bps: 50
  cooldown_minutes: 30

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

See [`docs/strategy_examples.md`](docs/strategy_examples.md) for more examples.

### Composite Strategy Logic

> Only **BUY** when technicals bullish + sentiment positive + funding neutral + on-chain accumulation + news supportive, within risk budget.

The LLM receives fused signals and portfolio state, but **RiskManager always has final veto power**.

---

## Integration Deep Dive

### CMC Agent Hub (Best Use of CMC)

- **MCP connection** to `https://mcp.coinmarketcap.com/mcp`
- **6+ data categories**: quotes, technicals, sentiment, on-chain flows, derivatives/funding, news
- **Signal fusion** with configurable weights → composite conviction score
- **x402 support** — commented integration point for premium micropayments via TWAK
- **Circuit breaker** — graceful degradation to HOLD on data failures

### Trust Wallet Agent Kit (Best Use of TWAK)

- **Autonomous wallet mode** — keys in TWAK secure keychain, never leave device
- **Local signing** — all transactions signed locally via TWAK CLI
- **PancakeSwap spot swaps** on BSC with slippage protection
- **x402 micropayments** for premium data access
- **Portfolio queries** — live balance, positions, PnL tracking
- **Competition registration** via `twak compete register`
- **Multiple surfaces** — CLI wrapper + MCP integration points + x402

### BNB AI Agent SDK (Best Use of BNB SDK)

- **ERC-8004 registration** — on-chain agent identity with metadata
- **Gas-free testnet** via MegaFuel paymaster
- **Discoverable profile** — name, description, strategy, endpoints
- **Competition helper** — registration payload export

---

## Demo Script for Judges

```bash
# Full demo flow
bash scripts/demo.sh

# Or step by step:
genesis status                          # Show configuration
genesis portfolio                       # Live portfolio
genesis backtest --cycles 3             # Simulated trades
genesis logs --limit 10                 # Decision reasoning
genesis logs --export data/judge_audit.json  # Full audit export
genesis dashboard                       # Visual monitoring
```

### Testnet → Mainnet Demo Flow

1. Run `genesis run --simulate` to show decision pipeline
2. Run `genesis run` on BSC testnet — show tx on [testnet.bscscan.com](https://testnet.bscscan.com)
3. Switch `.env` to `bsc-mainnet`, run with small position sizes
4. Export audit: `genesis logs --export data/live_audit.json`
5. Show dashboard with PnL, decisions, and risk metrics

---

## PnL & Risk Tracking

| Metric | Source | Enforcement |
|--------|--------|-------------|
| Portfolio value | TWAK `portfolio` | Snapshot every cycle |
| Daily PnL | TWAK + SQLite history | Dashboard display |
| Drawdown | Peak tracking in RiskManager | **Hard halt** at max % |
| Position sizing | % of portfolio | Capped per trade |
| Slippage | TWAK swap params | Max bps enforced |
| Cooldown | Per-token timer | Blocks rapid re-entry |
| Token filter | `rules.yaml` allowlist | Rejects unlisted tokens |

---

## Security Model

- **Self-custody**: Private keys stored in TWAK secure keychain only
- **Local signing**: No keys transmitted to servers or logged
- **Revocable**: TWAK autonomous wallet can be revoked independently
- **No hardcoded secrets**: All sensitive data in `.env` (gitignored)
- **Risk-first**: LLM suggests, RiskManager enforces — LLM cannot override hard limits
- **Audit trail**: Every decision logged with full reasoning for transparency

> **Warning**: This is experimental software for a hackathon. Never risk more than you can afford to lose. Start on testnet.

---

## Project Structure

```
genesis/
├── README.md
├── pyproject.toml
├── .env.example
├── config/rules.yaml
├── genesis/
│   ├── cli.py
│   ├── core/          # Agent loop, models, config, database
│   ├── data/          # CMC provider, signal aggregator
│   ├── decision/      # Strategy engine, risk manager, LLM prompts
│   ├── execution/     # TWAK wrapper, perps stub
│   ├── onchain/       # ERC-8004 identity, competition
│   └── strategy_skill/  # Track 2 generator, builder, backtest
├── skills/            # Track 2 SKILL.md deliverable
├── dashboard/         # FastAPI UI (Track 1 /app, Track 2 /app/strategy)
├── tests/             # Unit tests
├── scripts/           # Setup, demo, registration
└── docs/              # Architecture, strategy examples
```

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check genesis/

# Run single cycle in simulation
genesis run --simulate --cycles 1
```

---

## Switching to Mainnet

1. Edit `.env`:
   ```env
   GENESIS_NETWORK=bsc-mainnet
   TWAK_NETWORK=bsc-mainnet
   BNB_AGENT_NETWORK=bsc-mainnet
   ```
2. Reduce risk limits in `config/rules.yaml` for initial live trading
3. Fund TWAK wallet with small BNB amount
4. Run `genesis run` (remove `--simulate`)

---

## Future Roadmap

- [ ] PancakeSwap Perps integration (calldata + contract addresses)
- [ ] Multi-agent coordination (swarm strategies)
- [ ] ERC-8183 on-chain job submission for agentic commerce
- [x] Track 2 Strategy Skill Generator + audit replay backtester
- [ ] Historical backtester with real CMC time-series data
- [ ] Advanced dashboard: real-time charts, rule editor UI
- [ ] MCP native client (replace HTTP JSON-RPC shim)
- [ ] TWAK MCP server integration alongside CLI
- [ ] Multi-chain support (opBNB, other EVM chains)

---

## License

MIT License. See LICENSE file.

## Contributing

This is a hackathon project. Issues and PRs welcome for post-hackathon development.

---

**Built for Track 1 (Autonomous Trading Agents) and Track 2 (Strategy Skills) on BSC**
*CMC Agent Hub + TWAK + BNB AI Agent SDK*