from src.risk_manager import RiskLimits, RiskManager


def make_manager(**overrides) -> RiskManager:
    limits = RiskLimits(
        max_position_pct=overrides.get("max_position_pct", 0.05),
        max_open_positions=overrides.get("max_open_positions", 5),
        max_daily_loss_pct=overrides.get("max_daily_loss_pct", 0.03),
        min_confidence_to_act=overrides.get("min_confidence_to_act", 0.55),
    )
    return RiskManager(limits)


def test_hold_is_never_approved():
    rm = make_manager()
    result = rm.evaluate("hold", 0.9, "AAPL", 200, 100_000, 50_000, 0, False, 0.0)
    assert result.approved is False


def test_low_confidence_buy_rejected():
    rm = make_manager()
    result = rm.evaluate("buy", 0.3, "AAPL", 200, 100_000, 50_000, 0, False, 0.0)
    assert result.approved is False
    assert "confidence" in result.reason.lower()


def test_circuit_breaker_blocks_all_trades():
    rm = make_manager(max_daily_loss_pct=0.03)
    result = rm.evaluate("buy", 0.95, "AAPL", 200, 100_000, 50_000, 0, False, daily_pnl_pct=-0.05)
    assert result.approved is False
    assert "circuit breaker" in result.reason.lower()


def test_buy_sizes_position_within_cap():
    rm = make_manager(max_position_pct=0.05)
    result = rm.evaluate("buy", 0.9, "AAPL", 200, 100_000, 50_000, 0, False, 0.0)
    assert result.approved is True
    # 5% of 100k equity = 5000 -> 5000 / 200 = 25 shares
    assert result.qty == 25


def test_buy_capped_by_available_cash():
    rm = make_manager(max_position_pct=0.20)
    # 20% of equity would be 20k, but only 1000 cash available
    result = rm.evaluate("buy", 0.9, "AAPL", 200, 100_000, 1_000, 0, False, 0.0)
    assert result.approved is True
    assert result.qty == 5  # 1000 / 200


def test_buy_rejected_if_already_holds_symbol():
    rm = make_manager()
    result = rm.evaluate("buy", 0.9, "AAPL", 200, 100_000, 50_000, 1, True, 0.0)
    assert result.approved is False
    assert "already holding" in result.reason.lower()


def test_buy_rejected_at_max_open_positions():
    rm = make_manager(max_open_positions=3)
    result = rm.evaluate("buy", 0.9, "AAPL", 200, 100_000, 50_000, 3, False, 0.0)
    assert result.approved is False
    assert "max open positions" in result.reason.lower()


def test_sell_requires_existing_position():
    rm = make_manager()
    result = rm.evaluate("sell", 0.9, "AAPL", 200, 100_000, 50_000, 1, False, 0.0)
    assert result.approved is False
    assert "no existing position" in result.reason.lower()


def test_sell_of_held_position_approved():
    rm = make_manager()
    result = rm.evaluate("sell", 0.9, "AAPL", 200, 100_000, 50_000, 1, True, 0.0)
    assert result.approved is True
