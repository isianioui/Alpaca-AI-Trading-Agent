"""
Covers the market-hours gate added to both agent pipelines:

  - options market orders are rejected outright by Alpaca outside regular
    market hours (422 "options market orders are only allowed during market
    hours") -- OptionsTradingAgent must check the market clock BEFORE
    attempting an order and skip cleanly (status "ok",
    execution_status "skipped_market_closed") rather than attempt-and-catch.
  - stock day-orders can queue for next open, so TradingAgent still submits
    regardless of market state, but records market_open on every decision
    for consistency/visibility.

All Alpaca/Groq calls are faked -- no network access.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.llm_agent import OptionsTradeDecision, TradeDecision
from src.options_trading_agent import OptionsTradingAgent
from src.risk_manager import OptionsRiskDecision, RiskDecision
from src.trading_agent import TradingAgent


def _account(cash=10_000.0, equity=10_000.0, buying_power=10_000.0):
    account = MagicMock()
    account.cash = cash
    account.equity = equity
    account.buying_power = buying_power
    account.daily_pnl_pct = 0.0
    return account


CANDIDATE = {
    "symbol": "NVDA260116P00500000",
    "underlying_symbol": "NVDA",
    "type": "put",
    "strike": 500.0,
    "expiration": "2026-01-16",
    "dte": 40,
    "bid": 4.0,
    "ask": 4.2,
    "mid": 4.1,
    "implied_volatility": 0.3,
    "delta": -0.3,
    "gamma": 0.01,
    "theta": -0.02,
    "vega": 0.1,
    "open_interest": 500,
}


def _make_options_agent(market_open: bool, dry_run: bool = False):
    alpaca = MagicMock()
    alpaca.get_account.return_value = _account()
    alpaca.get_positions.return_value = []
    alpaca.get_bars.return_value = "fake-bars"
    alpaca.is_market_open.return_value = market_open

    options = MagicMock()
    options.get_option_positions.return_value = []
    options.get_best_covered_call_candidate.return_value = None
    options.get_best_cash_secured_put_candidate.return_value = CANDIDATE

    llm = MagicMock()
    llm.decide_option.return_value = OptionsTradeDecision(
        symbol="NVDA",
        action="open_cash_secured_put",
        contract_symbol=CANDIDATE["symbol"],
        confidence=0.8,
        reasoning="Strong setup on NVDA per the indicators provided.",
        risk_note="Assignment risk if NVDA drops below the strike.",
    )

    risk = MagicMock()
    risk.circuit_breaker_tripped.return_value = False
    risk.evaluate.return_value = OptionsRiskDecision(True, 1, "Approved: sell 1 cash-secured put contract(s).")

    agent = OptionsTradingAgent(
        alpaca_client=alpaca, options_client=options, llm_agent=llm, risk_manager=risk, dry_run=dry_run,
    )
    return agent, alpaca, options, llm, risk


def test_options_order_skipped_cleanly_when_market_closed(monkeypatch):
    import src.options_trading_agent as mod
    monkeypatch.setattr(mod, "build_feature_snapshot", lambda bars: {"last_close": 100.0})

    agent, alpaca, options, llm, risk = _make_options_agent(market_open=False)

    results = agent.run_cycle(["NVDA"])

    assert len(results) == 1
    record = results[0]

    # Not an error -- the analysis and risk verdict still happened normally.
    assert record["status"] == "ok"
    assert record["risk_approved"] is True
    assert record["reasoning"] == "Strong setup on NVDA per the indicators provided."
    assert record["risk_reason"] == "Approved: sell 1 cash-secured put contract(s)."

    # Execution was deferred, not attempted.
    assert record["market_open"] is False
    assert record["execution_status"] == "skipped_market_closed"
    assert record["order"] is None
    options.submit_option_order.assert_not_called()
    options.close_option_position.assert_not_called()


def test_options_order_submitted_normally_when_market_open(monkeypatch):
    import src.options_trading_agent as mod
    monkeypatch.setattr(mod, "build_feature_snapshot", lambda bars: {"last_close": 100.0})

    agent, alpaca, options, llm, risk = _make_options_agent(market_open=True)
    options.submit_option_order.return_value = {"id": "abc123", "status": "accepted"}

    results = agent.run_cycle(["NVDA"])

    record = results[0]
    assert record["market_open"] is True
    assert record["execution_status"] == "executed"
    assert record["order"] == {"id": "abc123", "status": "accepted"}
    options.submit_option_order.assert_called_once()


def test_options_dry_run_does_not_report_market_closed_skip(monkeypatch):
    """dry_run already suppresses order submission for an unrelated reason --
    it must not also claim execution was skipped for a market-closed reason
    it never actually checked/hit that branch for."""
    import src.options_trading_agent as mod
    monkeypatch.setattr(mod, "build_feature_snapshot", lambda bars: {"last_close": 100.0})

    agent, alpaca, options, llm, risk = _make_options_agent(market_open=False, dry_run=True)

    results = agent.run_cycle(["NVDA"])

    record = results[0]
    assert record["dry_run"] is True
    assert record["execution_status"] is None
    assert record["order"] is None
    options.submit_option_order.assert_not_called()


def test_options_market_closed_skip_does_not_raise_or_log_error(monkeypatch, caplog):
    import src.options_trading_agent as mod
    monkeypatch.setattr(mod, "build_feature_snapshot", lambda bars: {"last_close": 100.0})

    agent, alpaca, options, llm, risk = _make_options_agent(market_open=False)

    with caplog.at_level("ERROR"):
        results = agent.run_cycle(["NVDA"])

    assert results[0]["status"] == "ok"
    assert not any(r.levelname == "ERROR" for r in caplog.records)


def test_stock_agent_still_submits_when_market_closed(monkeypatch):
    """Stock day-orders queue for next open, so unlike options this must NOT
    be skipped -- market_open is recorded on the decision for visibility,
    but current submit behavior is unchanged."""
    import src.trading_agent as mod
    monkeypatch.setattr(mod, "build_feature_snapshot", lambda bars: {"last_close": 100.0})

    alpaca = MagicMock()
    alpaca.get_account.return_value = _account()
    alpaca.get_positions.return_value = []
    alpaca.get_bars.return_value = "fake-bars"
    alpaca.is_market_open.return_value = False
    alpaca.submit_market_order.return_value = {"id": "xyz", "status": "accepted"}

    llm = MagicMock()
    llm.decide.return_value = TradeDecision(
        symbol="AAPL", action="buy", confidence=0.9,
        reasoning="Bullish setup.", risk_note="Could reverse.",
    )

    risk = MagicMock()
    risk.circuit_breaker_tripped.return_value = False
    risk.evaluate.return_value = RiskDecision(True, 5, "Approved: 5 shares.")

    agent = TradingAgent(alpaca_client=alpaca, llm_agent=llm, risk_manager=risk, dry_run=False)
    results = agent.run_cycle(["AAPL"])

    record = results[0]
    assert record["market_open"] is False
    assert record["order"] == {"id": "xyz", "status": "accepted"}
    alpaca.submit_market_order.assert_called_once()
