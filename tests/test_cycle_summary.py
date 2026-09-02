from datetime import datetime, timedelta, timezone

from src.cycle_summary import (
    compute_activity_counters,
    group_into_cycles,
    summarize_latest_cycle,
)


def ts(offset_seconds: float, base: datetime = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)) -> str:
    return (base + timedelta(seconds=offset_seconds)).isoformat()


def stock_decision(symbol, action="hold", confidence=0.6, risk_approved=False, order=None,
                    offset=0, status="ok", risk_reason="Model recommended hold."):
    return {
        "symbol": symbol, "llm_action": action, "confidence": confidence,
        "risk_approved": risk_approved, "risk_reason": risk_reason,
        "reasoning": f"{symbol} reasoning", "order": order, "status": status,
        "timestamp": ts(offset),
    }


def option_decision(symbol, offset=0, **kwargs):
    d = stock_decision(symbol, offset=offset, **kwargs)
    d["asset_class"] = "option"
    return d


def exit_summary(offset, checked, closes, holds, closed_symbols=None, dry_run=True):
    return {
        "record_type": "exit_engine_summary", "trigger": "exit_engine",
        "positions_checked": checked, "closes": closes, "holds": holds,
        "closed_symbols": closed_symbols or [], "dry_run": dry_run, "status": "ok",
        "timestamp": ts(offset),
    }


# ---------------------------------------------------------------------- #
# summarize_latest_cycle
# ---------------------------------------------------------------------- #
def test_no_decisions_returns_none():
    assert summarize_latest_cycle([]) is None


def test_all_holds_narrative():
    decisions = [
        stock_decision("AAPL", offset=0),
        stock_decision("MSFT", offset=30),
    ]
    summary = summarize_latest_cycle(decisions)
    assert summary is not None
    assert summary.kind == "stock"
    assert "Checked 2 symbols" in summary.narrative
    assert "All were holds" in summary.narrative


def test_mixed_cycle_narrative_mentions_executed_and_rejected():
    decisions = [
        stock_decision("AAPL", action="buy", confidence=0.9, risk_approved=True,
                        order={"id": "1"}, offset=0, risk_reason="Approved: 10 shares."),
        stock_decision("MSFT", action="buy", confidence=0.5, risk_approved=False, offset=30,
                        risk_reason="Confidence below minimum threshold."),
        stock_decision("TSLA", offset=60),  # hold
    ]
    summary = summarize_latest_cycle(decisions)
    assert "1 trade executed" in summary.narrative
    assert "1 rejected by the risk gate" in summary.narrative
    assert "1 hold" in summary.narrative
    # highest confidence non-hold (AAPL, 0.9) should be the highlighted call
    assert "AAPL" in summary.narrative
    assert "Executed" in summary.narrative


def test_errored_symbols_are_mentioned_but_not_counted_as_checked():
    decisions = [
        stock_decision("AAPL", offset=0),
        {"symbol": "MSFT", "status": "error", "reason": "429 quota", "timestamp": ts(30)},
    ]
    summary = summarize_latest_cycle(decisions)
    assert "Checked 1 symbol" in summary.narrative
    assert "1 skipped due to errors" in summary.narrative


def test_exit_engine_summary_all_within_thresholds():
    decisions = [
        stock_decision("AAPL", action="buy", risk_approved=True, order={"id": "1"}, offset=0),
        exit_summary(offset=300, checked=3, closes=0, holds=3),
    ]
    summary = summarize_latest_cycle(decisions)
    assert summary.kind == "exit_engine"
    assert "3 checked against exit rules, all within thresholds" in summary.narrative


def test_exit_engine_summary_with_closes_names_symbols():
    decisions = [exit_summary(offset=0, checked=2, closes=1, holds=1, closed_symbols=["NVDA"])]
    summary = summarize_latest_cycle(decisions)
    assert "1 triggered a close (NVDA)" in summary.narrative
    assert "1 remain within thresholds" in summary.narrative


def test_exit_engine_summary_no_positions():
    decisions = [exit_summary(offset=0, checked=0, closes=0, holds=0)]
    summary = summarize_latest_cycle(decisions)
    assert "No open positions to evaluate" in summary.narrative


def test_latest_cycle_is_the_most_recent_by_timestamp_not_list_order():
    older = stock_decision("AAPL", offset=0)
    newer = option_decision("NVDA", offset=600)
    summary = summarize_latest_cycle([newer, older])  # deliberately out of order
    assert summary.kind == "option"
    assert summary.timestamp == newer["timestamp"]


# ---------------------------------------------------------------------- #
# group_into_cycles
# ---------------------------------------------------------------------- #
def test_groups_split_on_large_time_gap():
    decisions = [
        stock_decision("AAPL", offset=0),
        stock_decision("MSFT", offset=30),
        stock_decision("AAPL", offset=1200),  # 20 min later -> new cycle
    ]
    groups = group_into_cycles(decisions)
    assert len(groups) == 2
    assert len(groups[0]) == 2
    assert len(groups[1]) == 1


def test_groups_split_on_kind_change_even_without_large_gap():
    decisions = [
        stock_decision("AAPL", offset=0),
        option_decision("AAPL", offset=10),
    ]
    groups = group_into_cycles(decisions)
    assert len(groups) == 2


def test_exit_engine_records_excluded_from_llm_grouping():
    decisions = [
        stock_decision("AAPL", offset=0),
        exit_summary(offset=10, checked=1, closes=0, holds=1),
    ]
    groups = group_into_cycles(decisions)
    assert len(groups) == 1
    assert len(groups[0]) == 1


# ---------------------------------------------------------------------- #
# compute_activity_counters
# ---------------------------------------------------------------------- #
def test_activity_counters_basic():
    decisions = [
        stock_decision("AAPL", action="buy", risk_approved=True, order={"id": "1"}, offset=0),
        stock_decision("MSFT", action="buy", risk_approved=False, offset=30),
        stock_decision("TSLA", offset=60),
        option_decision("NVDA", action="open_cash_secured_put", risk_approved=False, offset=1200),
    ]
    trades = [{"symbol": "AAPL", "realized_pnl": 50.0}]
    counters = compute_activity_counters(
        decisions, trades, open_positions_count=2,
        account_created_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    assert counters.days_live == 5
    assert counters.trades_filled == 1
    assert counters.open_positions == 2
    # risk_approved is False for MSFT (rejected buy), TSLA (a plain hold is
    # also risk_approved=False per RiskManager.evaluate()'s own contract),
    # and NVDA (rejected option open) -- matches the literal spec: count
    # every decision where risk_approved is False, not just active rejections.
    assert counters.refused_by_risk == 3
    assert counters.cycles_run == 2  # one stock cycle, one option cycle (different kind)


def test_activity_counters_excludes_exit_engine_from_refused_count():
    decisions = [
        exit_summary(offset=0, checked=1, closes=1, holds=0, closed_symbols=["AAPL"]),
    ]
    counters = compute_activity_counters(decisions, [], open_positions_count=0)
    assert counters.refused_by_risk == 0
    assert counters.cycles_run == 1


def test_activity_counters_days_live_falls_back_to_first_decision_timestamp():
    decisions = [stock_decision("AAPL", offset=0)]
    counters = compute_activity_counters(decisions, [], open_positions_count=0, account_created_at=None)
    assert counters.days_live is not None
    assert counters.days_live >= 0


def test_activity_counters_no_data_at_all():
    counters = compute_activity_counters([], [], open_positions_count=0, account_created_at=None)
    assert counters.days_live is None
    assert counters.cycles_run == 0
    assert counters.trades_filled == 0
    assert counters.refused_by_risk == 0
