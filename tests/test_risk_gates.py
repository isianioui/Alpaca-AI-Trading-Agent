from src.risk_gates import classify_options_reason, classify_stock_reason, compute_gate_stats
from src.risk_manager import OptionsRiskLimits, OptionsRiskManager, RiskLimits, RiskManager

# --------------------------------------------------------------------------- #
# Classifiers matched directly against RiskManager / OptionsRiskManager's own
# evaluate() output -- if risk_manager.py's wording ever drifts from what
# risk_gates.py pattern-matches, these fail instead of the dashboard silently
# mis-bucketing decisions under "other".
# --------------------------------------------------------------------------- #
def _stock_rm(**overrides) -> RiskManager:
    return RiskManager(RiskLimits(
        max_position_pct=overrides.get("max_position_pct", 0.05),
        max_open_positions=overrides.get("max_open_positions", 5),
        max_daily_loss_pct=overrides.get("max_daily_loss_pct", 0.03),
        min_confidence_to_act=overrides.get("min_confidence_to_act", 0.55),
    ))


def test_stock_circuit_breaker_classified():
    rm = _stock_rm()
    r = rm.evaluate("buy", 0.9, "AAPL", 200, 100_000, 50_000, 0, False, daily_pnl_pct=-0.05)
    assert classify_stock_reason(r.reason) == "circuit_breaker"


def test_stock_confidence_classified():
    rm = _stock_rm()
    r = rm.evaluate("buy", 0.1, "AAPL", 200, 100_000, 50_000, 0, False, 0.0)
    assert classify_stock_reason(r.reason) == "confidence"


def test_stock_eligibility_sell_classified():
    rm = _stock_rm()
    r = rm.evaluate("sell", 0.9, "AAPL", 200, 100_000, 50_000, 0, already_holds_symbol=False, daily_pnl_pct=0.0)
    assert classify_stock_reason(r.reason) == "eligibility"


def test_stock_eligibility_buy_no_pyramiding_classified():
    rm = _stock_rm()
    r = rm.evaluate("buy", 0.9, "AAPL", 200, 100_000, 50_000, 0, already_holds_symbol=True, daily_pnl_pct=0.0)
    assert classify_stock_reason(r.reason) == "eligibility"


def test_stock_position_limit_classified():
    rm = _stock_rm(max_open_positions=1)
    r = rm.evaluate("buy", 0.9, "AAPL", 200, 100_000, 50_000, open_position_count=1,
                     already_holds_symbol=False, daily_pnl_pct=0.0)
    assert classify_stock_reason(r.reason) == "position_limit"


def test_stock_sizing_classified():
    rm = _stock_rm()
    r = rm.evaluate("buy", 0.9, "AAPL", 1_000_000, 100_000, 1.0, 0, False, 0.0)
    assert classify_stock_reason(r.reason) == "sizing"


def test_stock_approved_classified():
    rm = _stock_rm()
    r = rm.evaluate("buy", 0.9, "AAPL", 200, 100_000, 50_000, 0, False, 0.0)
    assert r.approved is True
    assert classify_stock_reason(r.reason) == "approved"


def test_stock_hold_is_unclassified_other():
    rm = _stock_rm()
    r = rm.evaluate("hold", 0.9, "AAPL", 200, 100_000, 50_000, 0, False, 0.0)
    assert classify_stock_reason(r.reason) == "other"


def _put_candidate(strike: float = 100.0) -> dict:
    return {
        "symbol": "AAPL250101P00100000", "underlying_symbol": "AAPL", "type": "put",
        "strike": strike, "expiration": "2025-01-01", "dte": 30,
        "bid": 1.0, "ask": 1.2, "mid": 1.1, "implied_volatility": 0.3,
        "delta": -0.3, "gamma": 0.01, "theta": -0.02, "vega": 0.1, "open_interest": 500,
    }


def _options_rm(**overrides) -> OptionsRiskManager:
    limits = OptionsRiskLimits(
        max_options_collateral_pct=overrides.get("max_options_collateral_pct", 0.25),
        max_open_option_positions=overrides.get("max_open_option_positions", 3),
        min_confidence_to_act=overrides.get("min_confidence_to_act", 0.55),
    )
    return OptionsRiskManager(limits)


def test_options_whitelist_classified():
    rm = _options_rm()
    r = rm.evaluate("open_naked_call", 0.9, "AAPL", "X", None, 0, 100_000, 100_000, 0, 0, False, 0.0)
    assert classify_options_reason(r.reason) == "whitelist"


def test_options_confidence_classified():
    rm = _options_rm()
    r = rm.evaluate("open_cash_secured_put", 0.1, "AAPL", "X", None, 0, 100_000, 100_000, 0, 0, False, 0.0)
    assert classify_options_reason(r.reason) == "confidence"


def test_options_candidate_validation_classified():
    rm = _options_rm()
    candidate = _put_candidate()
    r = rm.evaluate("open_cash_secured_put", 0.9, "AAPL", "MISMATCHED_SYMBOL", candidate,
                     0, 100_000, 100_000, 0, 0, False, 0.0)
    assert classify_options_reason(r.reason) == "candidate_validation"


def test_options_underlying_eligibility_cash_classified():
    rm = _options_rm()
    candidate = _put_candidate(strike=100.0)
    r = rm.evaluate("open_cash_secured_put", 0.9, "AAPL", candidate["symbol"], candidate,
                     0, 500, 100_000, 0, 0, False, 0.0)
    assert classify_options_reason(r.reason) == "underlying_eligibility"


def test_options_collateral_cap_classified():
    rm = _options_rm(max_options_collateral_pct=0.01)
    candidate = _put_candidate(strike=100.0)
    r = rm.evaluate("open_cash_secured_put", 0.9, "AAPL", candidate["symbol"], candidate,
                     0, 100_000, 100_000, existing_options_collateral=0,
                     open_option_position_count=0, already_has_option_position=False, daily_pnl_pct=0.0)
    assert classify_options_reason(r.reason) == "collateral_cap"


def test_options_approved_classified():
    rm = _options_rm()
    candidate = _put_candidate(strike=100.0)
    r = rm.evaluate("open_cash_secured_put", 0.9, "AAPL", candidate["symbol"], candidate,
                     0, 100_000, 100_000, 0, 0, False, 0.0)
    assert r.approved is True
    assert classify_options_reason(r.reason) == "approved"


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def test_compute_gate_stats_separates_stock_and_options_and_excludes_exit_engine():
    decisions = [
        {"risk_reason": "Approved: 10 shares (~$2,000.00, 5% of equity cap)."},
        {"risk_reason": "Confidence 0.20 below minimum 0.55 threshold."},
        {"risk_reason": "Model recommended hold."},
        {"status": "error", "risk_reason": "Approved: whatever"},
        {"asset_class": "option", "risk_reason": "Approved: sell 1 cash-secured put contract(s) at strike $100.00 ($10,000.00 collateral)."},
        {"asset_class": "option", "risk_reason": "Cash-secured put rejected: collateral cap (25% of equity) leaves no room."},
        {"trigger": "exit_engine", "risk_reason": "STOP_LOSS: -9.00% <= -8% limit"},
        {"record_type": "exit_engine_summary"},
    ]
    stock_stats, options_stats = compute_gate_stats(decisions)

    assert stock_stats.evaluated == 2   # approved + confidence-blocked; hold excluded
    assert stock_stats.approved == 1
    assert stock_stats.blocked_counts["confidence"] == 1
    assert stock_stats.holds_excluded == 1
    assert stock_stats.errors_excluded == 1

    assert options_stats.evaluated == 2
    assert options_stats.approved == 1
    assert options_stats.blocked_counts["collateral_cap"] == 1
