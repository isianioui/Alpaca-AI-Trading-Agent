"""
Historical simulation of the exact live decision pipeline
(src.llm_agent.LLMTradingAgent.decide() + src.risk_manager.RiskManager +
src.exit_engine.ExitLimits' thresholds), replayed day-by-day over past
Alpaca daily bars for a chosen watchlist.

BACKTEST -- HISTORICAL SIMULATION, NOT LIVE RESULTS. This exists because a
two-day-old paper account has no closed trades to show real performance on;
a labeled, best-effort simulation is the only honest way to give reviewers
P&L-shaped signal on whether the strategy has any edge at all in the
meantime. It is not a forecast and not a guarantee live results will
resemble it.

What this DOES reuse verbatim from the live pipeline (no shortcut, no
simplified proxy):
  - src.indicators.build_feature_snapshot() for every day's feature snapshot,
    computed from only the bars up to and including that day (no lookahead).
  - src.llm_agent.LLMTradingAgent.decide() -- the same Groq (GPT-OSS-120B) call, same
    system prompt, same schema -- for every decision point.
  - src.risk_manager.RiskManager.evaluate() -- the same sizing/eligibility/
    circuit-breaker/position-limit checks -- for every proposed trade.
  - src.exit_engine.ExitLimits' stop-loss/take-profit thresholds, re-checked
    against the simulated position every decision point (mirrors ExitEngine
    without needing a second live-position-dependent code path).

What is NOT the same as live trading, stated plainly (also surfaced in the
dashboard's Backtest view copy):
  - Fills are simulated at the same day's close used to make the decision --
    optimistic relative to a live market order, which fills moments later at
    a possibly different price. No slippage, spreads, or partial fills.
  - Every symbol shares one simulated cash/equity pool walked forward in
    (date, symbol) order; multi-symbol contention for the position-limit gate
    depends on that processing order, same as it would in a live cycle.
  - Options are out of scope -- Alpaca's free tier doesn't expose historical
    options chains/greeks, so only the stock pipeline is replayed.
  - One real Groq call per decision point: Groq's free tier is generous
    (14,400 requests/day as of this writing) but not unlimited, so
    cadence_days exists to trade fidelity for a bounded call count (see
    estimate_decision_count()).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import pandas as pd

from src.exit_engine import ExitLimits
from src.indicators import build_feature_snapshot
from src.llm_agent import LLMTradingAgent
from src.risk_manager import RiskLimits, RiskManager

BACKTEST_DISCLAIMER = (
    "Backtest — historical simulation, not live results. Every decision replays the same "
    "LLM call and risk-manager thresholds the live agent uses, but fills are simulated at "
    "each day's close with no slippage, spreads, or partial fills modeled."
)

WARMUP_CALENDAR_DAYS = 100  # buffer before `start` so SMA50/RSI/MACD are never NaN on day 1


@dataclass
class SimPosition:
    symbol: str
    qty: float
    entry_price: float
    entry_date: str


@dataclass
class SimTrade:
    symbol: str
    action: str          # "BUY" | "SELL"
    date: str
    price: float
    qty: float
    reason: str
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None


@dataclass
class BacktestResult:
    symbols: list[str]
    start_date: str
    end_date: str
    cadence_days: int
    starting_equity: float
    ending_equity: float
    equity_curve: list[dict] = field(default_factory=list)   # [{"date", "equity"}]
    trades: list[dict] = field(default_factory=list)
    decisions_evaluated: int = 0
    llm_errors: int = 0
    total_return_pct: float = 0.0
    win_rate: Optional[float] = None
    winning_trades: int = 0
    losing_trades: int = 0
    sharpe_ratio: Optional[float] = None
    max_drawdown_pct: float = 0.0
    generated_at: str = ""


def estimate_decision_count(num_symbols: int, lookback_months: float, cadence_days: int) -> int:
    """Rough, calendar-based estimate (not the real Alpaca trading calendar)
    of how many LLM calls a given configuration will make -- for the
    dashboard to show a cost/time estimate before the user commits to a run."""
    approx_trading_days = int(lookback_months * 30.44 * (5 / 7))
    per_symbol = max(1, approx_trading_days // max(1, cadence_days))
    return per_symbol * max(1, num_symbols)


def _sharpe_ratio(equity_values: list[float], periods_per_year: float) -> Optional[float]:
    """Pure function over a plain list of equity values (one per decision
    step) so it's testable without pandas Timestamps or a live client."""
    if len(equity_values) < 3:
        return None
    returns = pd.Series(equity_values).pct_change().dropna()
    if returns.empty or returns.std() == 0:
        return None
    return float((returns.mean() / returns.std()) * (periods_per_year ** 0.5))


def _max_drawdown_pct(equity_values: list[float]) -> float:
    if not equity_values:
        return 0.0
    s = pd.Series(equity_values)
    running_max = s.cummax()
    drawdown = (s - running_max) / running_max
    return float(drawdown.min() * 100)


def run_backtest(
    client,
    symbols: list[str],
    lookback_months: float = 3.0,
    cadence_days: int = 1,
    starting_equity: float = 100_000.0,
    risk_limits: Optional[RiskLimits] = None,
    exit_limits: Optional[ExitLimits] = None,
    llm_agent: Optional[LLMTradingAgent] = None,
    call_delay_seconds: float = 0.4,
    progress_cb: Optional[Callable[[int, int, str, date], None]] = None,
) -> BacktestResult:
    """client: anything exposing get_bars(symbol, lookback_days) -> DataFrame
    with a 'close' column (src.alpaca_client.AlpacaClient in production,
    a fake in tests). progress_cb(step, total_steps, symbol, date) is called
    once per decision point so the caller can drive a progress bar."""
    if not symbols:
        raise ValueError("At least one symbol is required for a backtest.")

    risk = RiskManager(risk_limits or RiskLimits())
    exits = exit_limits or ExitLimits.from_env()
    llm = llm_agent or LLMTradingAgent()

    end = date.today()
    start = end - timedelta(days=int(lookback_months * 30.44))

    all_bars: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        bars = client.get_bars(sym, lookback_days=(end - start).days + WARMUP_CALENDAR_DAYS)
        if bars is not None and not bars.empty:
            all_bars[sym] = bars

    if not all_bars:
        raise ValueError("No historical bar data returned for any requested symbol.")

    decision_points: list[tuple[pd.Timestamp, str]] = []
    for sym, bars in all_bars.items():
        days_in_range = [ts for ts in bars.index if start <= ts.date() <= end]
        decision_points.extend((ts, sym) for ts in days_in_range[:: max(1, cadence_days)])
    decision_points.sort(key=lambda x: (x[0], x[1]))

    def mark_price(sym: str, dt: pd.Timestamp) -> Optional[float]:
        bars = all_bars.get(sym)
        if bars is None:
            return None
        sliced = bars[bars.index <= dt]
        return float(sliced["close"].iloc[-1]) if not sliced.empty else None

    def mark_equity(cash_amt: float, positions: dict[str, SimPosition], dt: pd.Timestamp) -> float:
        return cash_amt + sum(
            (mark_price(s, dt) or p.entry_price) * p.qty for s, p in positions.items()
        )

    cash = starting_equity
    positions: dict[str, SimPosition] = {}
    trades: list[SimTrade] = []
    equity_curve: list[dict] = []
    llm_errors = 0
    evaluated = 0
    total_steps = len(decision_points)

    for step, (dt, sym) in enumerate(decision_points):
        if progress_cb:
            progress_cb(step, total_steps, sym, dt.date())

        sliced = all_bars[sym][all_bars[sym].index <= dt]
        if len(sliced) < 20:
            continue
        features = build_feature_snapshot(sliced)
        if "error" in features:
            continue

        last_price = features["last_close"]
        held = positions.get(sym)

        # Deterministic exit check first, mirroring ExitEngine -- runs every
        # step regardless of what the LLM would say, no LLM call needed.
        if held is not None:
            plpc = (last_price - held.entry_price) / held.entry_price
            if plpc <= exits.stop_loss_pct or plpc >= exits.take_profit_pct:
                reason = "STOP_LOSS" if plpc <= exits.stop_loss_pct else "TAKE_PROFIT"
                pnl = (last_price - held.entry_price) * held.qty
                cash += held.qty * last_price
                trades.append(SimTrade(sym, "SELL", str(dt.date()), last_price, held.qty,
                                        f"exit_engine:{reason} ({plpc:+.2%})", pnl, plpc))
                del positions[sym]
                held = None

        equity_now = mark_equity(cash, positions, dt)

        try:
            decision = llm.decide(
                symbol=sym,
                features=features,
                account_context={
                    "equity": round(equity_now, 2), "cash": round(cash, 2),
                    "buying_power": round(cash, 2), "daily_pnl_pct": 0.0,
                    "open_position_count": len(positions),
                },
                current_position=(
                    {"symbol": sym, "qty": held.qty, "avg_entry_price": held.entry_price}
                    if held else None
                ),
            )
        except Exception:
            llm_errors += 1
            equity_curve.append({"date": str(dt.date()), "equity": round(equity_now, 2)})
            time.sleep(call_delay_seconds * 3)
            continue

        evaluated += 1

        risk_result = risk.evaluate(
            action=decision.action, confidence=decision.confidence, symbol=sym,
            last_price=last_price, equity=equity_now, cash=cash,
            open_position_count=len(positions), already_holds_symbol=sym in positions,
            daily_pnl_pct=0.0,
        )

        if risk_result.approved:
            if decision.action == "buy":
                cash -= risk_result.qty * last_price
                positions[sym] = SimPosition(sym, risk_result.qty, last_price, str(dt.date()))
                trades.append(SimTrade(sym, "BUY", str(dt.date()), last_price, risk_result.qty,
                                        decision.reasoning[:160]))
            elif decision.action == "sell" and held:
                pnl = (last_price - held.entry_price) * held.qty
                pnl_pct = (last_price - held.entry_price) / held.entry_price
                cash += held.qty * last_price
                trades.append(SimTrade(sym, "SELL", str(dt.date()), last_price, held.qty,
                                        f"llm_decision ({pnl_pct:+.2%})", pnl, pnl_pct))
                del positions[sym]

        equity_curve.append({"date": str(dt.date()), "equity": round(mark_equity(cash, positions, dt), 2)})
        time.sleep(call_delay_seconds)

    final_equity = equity_curve[-1]["equity"] if equity_curve else starting_equity

    closed_trades = [t for t in trades if t.action == "SELL"]
    wins = [t for t in closed_trades if (t.pnl or 0) > 0]
    losses = [t for t in closed_trades if (t.pnl or 0) <= 0]

    equity_values = [e["equity"] for e in equity_curve]
    periods_per_year = 252 / max(1, cadence_days)
    sharpe = _sharpe_ratio(equity_values, periods_per_year)

    return BacktestResult(
        symbols=list(all_bars.keys()), start_date=str(start), end_date=str(end),
        cadence_days=cadence_days, starting_equity=starting_equity, ending_equity=final_equity,
        equity_curve=equity_curve, trades=[t.__dict__ for t in trades],
        decisions_evaluated=evaluated, llm_errors=llm_errors,
        total_return_pct=round((final_equity - starting_equity) / starting_equity * 100, 2),
        win_rate=(len(wins) / len(closed_trades)) if closed_trades else None,
        winning_trades=len(wins), losing_trades=len(losses),
        sharpe_ratio=round(sharpe, 2) if sharpe is not None else None,
        max_drawdown_pct=round(_max_drawdown_pct(equity_values), 2),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
