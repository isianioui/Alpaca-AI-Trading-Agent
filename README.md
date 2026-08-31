# 🦙 Alpaca AI Trading Agent

**Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (lablab.ai)**

An autonomous, explainable AI trading agent that pulls live market data from
**Alpaca's Trading API**, reasons about each trade with **Google Gemini**,
sizes and approves every position through a deterministic risk manager,
executes on **Alpaca paper trading**, and shows its full decision trail on a
**Streamlit dashboard**. No real money is ever at risk, and every API used
here is free (Alpaca paper trading and Gemini's free tier both require no
payment method).

## Why this project

Most "AI trading bot" demos are a black box: money moves and nobody can say
why. This agent is built around **explainability first**:

- Gemini never has raw authority to place a trade — it can only *propose* an
  action, and it must justify it in plain English every time.
- A separate, deterministic `RiskManager` (no LLM involved) enforces position
  sizing, max concurrent positions, and a daily-loss circuit breaker. The LLM
  cannot override these limits.
- Every decision — including ones the risk manager rejects — is logged and
  shown on the dashboard with the reasoning attached, so a judge (or a real
  trader) can audit exactly why the agent did or didn't act.

## Architecture

```
                ┌─────────────────┐
                │   Alpaca API     │  ← account, positions, historical bars
                └────────┬─────────┘
                         │
                ┌────────▼─────────┐
                │  indicators.py    │  SMA / EMA / RSI / MACD / volatility
                └────────┬─────────┘
                         │ structured feature snapshot
                ┌────────▼─────────┐
                │   llm_agent.py    │  Gemini decides buy/sell/hold + reasoning
                │  (Google GenAI)   │  via response_schema → structured JSON
                └────────┬─────────┘
                         │ proposed decision
                ┌────────▼─────────┐
                │  risk_manager.py  │  position sizing, exposure caps,
                │  (deterministic)  │  circuit breaker — LLM cannot override
                └────────┬─────────┘
                         │ approved order (or rejection)
                ┌────────▼─────────┐
                │  Alpaca paper     │  order execution
                │  trading account  │
                └────────┬─────────┘
                         │
                ┌────────▼─────────┐
                │   logger.py       │  JSONL decision log
                └────────┬─────────┘
                         │
                ┌────────▼─────────┐
                │  dashboard.py     │  Streamlit UI: account, positions,
                │   (Streamlit)     │  price charts, full decision log
                └───────────────────┘
```

`src/trading_agent.py` is the orchestrator that wires all of the above
together into one `run_cycle()` call, used by both the CLI (`main.py`) and
the dashboard (`dashboard.py`), so there's a single source of truth for the
agent's behavior.

## Quick start

### 1. Get free API keys (no payment required anywhere)

- **Alpaca (paper trading, free, simulated $100k account):**
  [app.alpaca.markets/paper/dashboard/overview](https://app.alpaca.markets/paper/dashboard/overview)
  → generate an API key + secret.
- **Google Gemini (free tier, no credit card required):**
  [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
  → sign in with a Google account and generate an API key.

### 2. Set up the environment

```bash
git clone https://github.com/isianioui/Alpaca-AI-Trading-Agents-Hackathon.git
cd Alpaca-AI-Trading-Agents-Hackathon

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env            # then edit .env and paste in your API keys
```

### 3. Run it

**Dashboard (recommended for demoing):**

```bash
streamlit run dashboard.py
```

Opens at `http://localhost:8501`. Click **"Run agent cycle now"** in the
sidebar (dry-run is on by default, so it will decide and explain but not
place real paper orders until you uncheck it).

**CLI:**

```bash
# check account + open positions
python main.py status

# run one cycle, decisions only, no orders placed
python main.py run --dry-run

# run one cycle for real (places paper trades)
python main.py run --symbols AAPL,MSFT,TSLA

# run continuously every 5 minutes
python main.py loop --interval 300
```

### 4. Run the tests (no API keys required)

```bash
pip install pytest
pytest tests/ -v
```

The indicator math and risk-management rules are fully unit tested against
synthetic data, so the core logic can be verified without touching any
live API.

## Optional: control it via Alpaca's MCP server too

This hackathon calls out Alpaca's **Trading API, MCP server, and CLI**. The
agent above talks to the Trading API directly (for reliability and testing).
As a complementary interface, `mcp_config.example.json` shows how to point
Claude Desktop / Claude Code / Cursor at
[Alpaca's official hosted MCP server](https://github.com/alpacahq/alpaca-mcp-server)
so you can also drive the *same* paper account with natural language
("what's my current P&L?", "close my AAPL position") outside of this app.

## Risk controls (hard limits, not suggestions)

Configurable in `.env`:

| Control | Default | Purpose |
|---|---|---|
| `MAX_POSITION_PCT` | 5% of equity | Caps size of any single new position |
| `MAX_OPEN_POSITIONS` | 5 | Caps concurrent positions |
| `MAX_DAILY_LOSS_PCT` | 3% | Circuit breaker — halts all new trades for the day |
| Minimum confidence | 0.55 | Low-conviction LLM calls are ignored, not acted on |

## Options Trading

Built for this hackathon's **Options Alpha Agents** track. The agent can propose
exactly two **defined-risk, income-generating** options strategies — nothing
else:

- **Covered call** — sell a call against 100+ shares you already hold.
- **Cash-secured put** — sell a put fully backed by cash on hand to buy 100
  shares if assigned.

**Why only defined-risk strategies:** naked calls, uncovered puts, spreads,
and straddles are permanently out of scope — not just unimplemented, but
explicitly forbidden. This is enforced twice: the LLM's prompt forbids
proposing anything else, and `OptionsRiskManager` independently re-verifies
eligibility (100+ shares held, or cash ≥ strike × 100) and rejects any action
string outside the two allowed strategies, regardless of what the LLM
proposes. Alpaca's own order API has no naked-vs-covered distinction — this
app's risk manager is the only thing enforcing it.

Candidates are picked from Alpaca's live option chain, targeting 30–45 days
to expiration and a delta in the 0.25–0.35 band (a common "sell premium,
stay defined-risk" heuristic), with the closest-to-target-delta contract
chosen and rejected/blocked trades logged with the reason, same as the stock
side.

**CLI:**

```bash
python main.py options-run --dry-run                    # decisions only
python main.py options-run --symbols AAPL,MSFT           # override the watchlist
python main.py options-loop --interval 3600               # continuous, hourly is plenty given the 30-45 DTE target
```

**Dashboard:** open the **🧾 Options** tab for open option positions, a run
button (dry-run on by default), and the options decision log.

Extra risk controls, configurable in `.env`:

| Control | Default | Purpose |
|---|---|---|
| `OPTIONS_WATCHLIST` | same as `WATCHLIST` | Tickers the options agent is allowed to trade |
| `MAX_OPTIONS_COLLATERAL_PCT` | 25% of equity | Caps total cash tied up across all open cash-secured puts |
| `MAX_OPEN_OPTION_POSITIONS` | 3 | Caps concurrent option positions |

## Project structure

```
├── main.py                    # CLI entrypoint (status / run / loop)
├── dashboard.py                # Streamlit dashboard
├── mcp_config.example.json     # optional Alpaca MCP server config
├── src/
│   ├── alpaca_client.py        # Alpaca Trading + Market Data API wrapper
│   ├── indicators.py           # SMA/EMA/RSI/MACD/volatility
│   ├── llm_agent.py             # Gemini decision engine (structured JSON output)
│   ├── risk_manager.py          # deterministic position sizing & limits
│   ├── trading_agent.py         # orchestrates the full stock decision cycle
│   ├── options_client.py        # Alpaca options chain data + order wrapper
│   ├── options_strategy.py      # covered call / cash-secured put candidate selection (pure, testable)
│   ├── options_trading_agent.py # orchestrates the full options decision cycle
│   └── logger.py                # JSONL decision log (shared by stock + options)
├── tests/
│   ├── test_indicators.py
│   ├── test_risk_manager.py
│   ├── test_options_strategy.py
│   └── test_options_risk_manager.py
├── requirements.txt
└── .env.example
```

## Safety

- Defaults to **Alpaca paper trading** (`ALPACA_PAPER=true`) — simulated
  money only. Switching to live trading is a deliberate, explicit config
  change the code does not encourage.
- The LLM's output is always structured (enforced Pydantic `response_schema`),
  never free text parsed with regex, so decisions can't silently fail to parse.
- Every rejected trade is logged with the reason, not just the executed ones.
- Options trading is restricted to exactly two defined-risk strategies —
  Alpaca's API itself does not distinguish covered from naked risk tiers, so
  this app's own `OptionsRiskManager` is the sole safety guarantee,
  independently re-verifying eligibility on every trade regardless of what
  the LLM proposes.

## Built with

[Alpaca Trading API](https://alpaca.markets/) · [Google Gemini API](https://ai.google.dev/) · Streamlit · pandas

---

*This is a paper-trading educational project built for a hackathon. Nothing
here is financial advice, and the agent's output should not be used to make
real trading decisions without independent judgment.*
