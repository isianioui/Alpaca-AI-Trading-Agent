# Alpaca AI Trading Agent — Technical Write-Up

## AI logic

Google **Gemini** (`GEMINI_MODEL` env var, currently `gemini-3.6-flash`) is the
sole reasoning component, called once per symbol per cycle via
`src/llm_agent.py`. It is never given tool-calling authority — it only
returns a structured decision that downstream code independently validates
and sizes.

**Stock pipeline** (`LLMTradingAgent.decide`): Gemini receives a JSON payload
of the symbol, a technical-indicator snapshot (SMA/EMA/RSI/MACD, volatility,
last close — computed in `indicators.py` from 90 days of Alpaca bars), an
account-context block (equity, cash, buying power, daily P&L%, open position
count), and the current position in that symbol if any. It must return
`action` (`buy`/`sell`/`hold`), `confidence` (0–1), `reasoning`, and
`risk_note`, enforced via a Pydantic `response_schema` (`response_mime_type:
application/json`) — never free-text parsed with regex, so output can't
silently fail to parse.

**Options pipeline** (`OptionsLLMAgent.decide_option`): same indicator/account
context, plus up to two pre-vetted contract candidates — a covered-call and a
cash-secured-put candidate, each already filtered server-side to Alpaca's live
option chain, 30–45 DTE, delta 0.25–0.35 — and any open option position on
that underlying. The system prompt restricts the model to exactly two
strategies: `open_covered_call` (sell a call against 100+ shares already
held) and `open_cash_secured_put` (sell a put fully backed by cash), plus
`close_position`/`hold`. Naked calls, uncovered puts, spreads, and straddles
are explicitly forbidden in the prompt. `contract_symbol` must be copied
verbatim from the candidate data — the model cannot invent an OCC symbol.

## Risk gates

Every LLM decision passes through a **deterministic** risk manager
(`src/risk_manager.py`) with no model calls — the LLM proposes, this disposes,
and it cannot be overridden by prompt engineering.

`RiskManager` (stock): rejects on a **daily circuit breaker** (daily P&L ≤
`-MAX_DAILY_LOSS_PCT`, default 3%, halts *all* new trades for the day),
a **minimum confidence threshold** (`min_confidence_to_act`, default 0.55),
a **max open positions** cap (default 5), and caps any new position at
`MAX_POSITION_PCT` of equity (default 5%), sized by
`min(equity × cap, cash) / price`.

`OptionsRiskManager`: composes the stock `RiskManager` to share the same
circuit breaker across both pipelines. It also independently re-verifies
everything the LLM claimed: an **action allowlist**
(`open_covered_call`/`open_cash_secured_put`/`close_position`/`hold` only —
anything else is rejected outright, defense-in-depth against the strategy
restriction living only in the prompt); **contract-identity validation**
(the proposed `contract_symbol` must match a server-fetched candidate, never
trusted from the LLM alone); **covered-call eligibility** (≥100 shares held,
re-checked, sized at `shares_held // 100` contracts); **cash-secured-put
eligibility** (cash ≥ strike × 100, re-checked against the actual candidate
strike); and a **collateral cap** (`MAX_OPTIONS_COLLATERAL_PCT`, default 25%
of equity, across all open CSPs) plus a **max open option positions** cap
(default 3).

## Alpaca infrastructure

**Trading API (paper)** — read inside each agent cycle (account snapshot,
open positions) via the **alpaca-py SDK**, kept in-process to avoid a
subprocess spawn per symbol in a multi-symbol loop.

**Market Data API** — historical stock bars and live option chains/quotes/
Greeks, also via the **SDK** (`StockHistoricalDataClient`,
`OptionHistoricalDataClient`), because bulk market data is far more ergonomic
as pandas DataFrames than parsed CLI JSON.

**Alpaca CLI** (`github.com/alpacahq/cli`) — the live **execution** mechanism,
satisfying the hackathon's CLI requirement as a real, wired-in dependency, not
a decoration. `src/alpaca_cli.py` shells out to the `alpaca` binary via
`subprocess`, authenticating non-interactively through `ALPACA_API_KEY`/
`ALPACA_SECRET_KEY` (never the OAuth login flow), parses its JSON stdout, and
raises `AlpacaCLIError` on a non-zero exit or malformed output so one bad
call can't crash a cycle. With `EXECUTION_BACKEND=cli` (the default),
`submit_market_order`/`close_position` (`alpaca_client.py`) and
`submit_option_order`/`close_option_position` (`options_client.py`) all route
through it (`alpaca order submit`, `alpaca position close`); `EXECUTION_BACKEND=sdk`
falls back to the direct SDK call. `python main.py status` and
`python main.py cli-check` run `alpaca account get`/`alpaca position list`
directly through the CLI as a live, non-order-placing demonstration.
`ALPACA_LIVE_TRADE` is never set — the CLI path always resolves to paper
trading.
