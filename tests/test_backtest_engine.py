import numpy as np
import pandas as pd

from src.backtest_engine import (
    _max_drawdown_pct,
    _sharpe_ratio,
    estimate_decision_count,
    run_backtest,
)
from src.llm_agent import TradeDecision


# --------------------------------------------------------------------------- #
# Pure stat helpers
# --------------------------------------------------------------------------- #
def test_sharpe_ratio_none_when_flat():
    assert _sharpe_ratio([100.0, 100.0, 100.0, 100.0], periods_per_year=252) is None


def test_sharpe_ratio_none_with_too_few_points():
    assert _sharpe_ratio([100.0, 101.0], periods_per_year=252) is None


def test_sharpe_ratio_positive_for_steady_gains():
    values = [100.0 * (1.01 ** i) for i in range(30)]
    sharpe = _sharpe_ratio(values, periods_per_year=252)
    assert sharpe is not None and sharpe > 0


def test_max_drawdown_pct_known_sequence():
    # peak 120 -> trough 90 is a 25% drawdown from that peak
    assert _max_drawdown_pct([100, 120, 90, 110]) == -25.0


def test_max_drawdown_pct_empty():
    assert _max_drawdown_pct([]) == 0.0


def test_estimate_decision_count_scales_with_symbols_and_cadence():
    one_symbol = estimate_decision_count(1, lookback_months=3, cadence_days=1)
    two_symbols = estimate_decision_count(2, lookback_months=3, cadence_days=1)
    assert two_symbols == one_symbol * 2

    daily = estimate_decision_count(1, lookback_months=3, cadence_days=1)
    weekly = estimate_decision_count(1, lookback_months=3, cadence_days=5)
    assert weekly < daily


# --------------------------------------------------------------------------- #
# Integration: fake client + fake LLM agent, no network calls
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, bars: pd.DataFrame):
        self._bars = bars

    def get_bars(self, symbol, lookback_days=60, timeframe=None):
        return self._bars


class ScriptedLLMAgent:
    """Cycles through a fixed script of (action, confidence) pairs regardless
    of symbol -- deterministic stand-in for LLMTradingAgent.decide() so the
    engine can be tested without a real Groq call."""

    def __init__(self, script: list[tuple[str, float]]):
        self.script = script
        self.calls = 0

    def decide(self, symbol, features, account_context, current_position=None):
        action, confidence = self.script[self.calls % len(self.script)]
        self.calls += 1
        return TradeDecision(
            symbol=symbol, action=action, confidence=confidence,
            reasoning="synthetic test reasoning", risk_note="synthetic test risk note",
            raw_features=features,
        )


def _make_uptrend_bars(num_days: int = 140, start_price: float = 100.0, end_price: float = 220.0) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=num_days)
    closes = np.linspace(start_price, end_price, num_days)
    return pd.DataFrame({"close": closes}, index=idx)


def test_run_backtest_full_lifecycle_buy_then_exit_engine_take_profit():
    bars = _make_uptrend_bars()
    client = FakeClient(bars)
    llm = ScriptedLLMAgent([("buy", 0.9), ("hold", 0.9)])

    result = run_backtest(
        client=client, symbols=["FAKE"], lookback_months=2, cadence_days=5,
        starting_equity=100_000.0, llm_agent=llm, call_delay_seconds=0,
    )

    assert result.decisions_evaluated > 0
    assert result.llm_errors == 0
    assert len(result.equity_curve) == result.decisions_evaluated

    actions = {t["action"] for t in result.trades}
    assert "BUY" in actions
    assert "SELL" in actions  # the steep uptrend must cross the +15% take-profit threshold

    sell_trades = [t for t in result.trades if t["action"] == "SELL"]
    assert all(t["reason"].startswith("exit_engine:TAKE_PROFIT") for t in sell_trades)

    assert result.ending_equity > result.starting_equity
    assert result.total_return_pct > 0


def test_run_backtest_raises_without_symbols():
    client = FakeClient(_make_uptrend_bars())
    try:
        run_backtest(client=client, symbols=[], llm_agent=ScriptedLLMAgent([("hold", 0.9)]))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_run_backtest_counts_llm_errors_without_crashing():
    class RaisingLLMAgent:
        def decide(self, *args, **kwargs):
            raise RuntimeError("simulated API failure")

    bars = _make_uptrend_bars()
    client = FakeClient(bars)
    result = run_backtest(
        client=client, symbols=["FAKE"], lookback_months=1, cadence_days=5,
        llm_agent=RaisingLLMAgent(), call_delay_seconds=0,
    )
    assert result.decisions_evaluated == 0
    assert result.llm_errors > 0
    assert result.trades == []
