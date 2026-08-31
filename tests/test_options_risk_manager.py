from src.risk_manager import OptionsRiskLimits, OptionsRiskManager


def make_options_manager(**overrides) -> OptionsRiskManager:
    limits = OptionsRiskLimits(
        max_options_collateral_pct=overrides.get("max_options_collateral_pct", 0.25),
        max_open_option_positions=overrides.get("max_open_option_positions", 3),
        min_confidence_to_act=overrides.get("min_confidence_to_act", 0.55),
    )
    return OptionsRiskManager(limits)


def make_candidate(**overrides) -> dict:
    candidate = {"symbol": "AAPL240119P00150000", "strike": 150.0, "delta": -0.30}
    candidate.update(overrides)
    return candidate


def base_kwargs(**overrides) -> dict:
    kwargs = dict(
        action="hold",
        confidence=0.9,
        symbol="AAPL",
        contract_symbol="",
        candidate=None,
        shares_held=0.0,
        available_cash=50_000.0,
        equity=100_000.0,
        existing_options_collateral=0.0,
        open_option_position_count=0,
        already_has_option_position=False,
        daily_pnl_pct=0.0,
    )
    kwargs.update(overrides)
    return kwargs


def test_hold_is_never_approved():
    rm = make_options_manager()
    result = rm.evaluate(**base_kwargs(action="hold"))
    assert result.approved is False


def test_disallowed_action_rejected():
    rm = make_options_manager()
    result = rm.evaluate(**base_kwargs(action="open_naked_call", confidence=0.95))
    assert result.approved is False
    assert "not an allowed" in result.reason.lower()


def test_circuit_breaker_blocks_options_trades_too():
    rm = make_options_manager()
    candidate = make_candidate()
    result = rm.evaluate(**base_kwargs(
        action="open_cash_secured_put", confidence=0.95, contract_symbol=candidate["symbol"],
        candidate=candidate, daily_pnl_pct=-0.05,
    ))
    assert result.approved is False
    assert "circuit breaker" in result.reason.lower()


def test_covered_call_rejected_with_insufficient_shares():
    rm = make_options_manager()
    candidate = make_candidate(symbol="AAPL240119C00150000", delta=0.30)
    result = rm.evaluate(**base_kwargs(
        action="open_covered_call", confidence=0.9, contract_symbol=candidate["symbol"],
        candidate=candidate, shares_held=50,
    ))
    assert result.approved is False
    assert "covered call" in result.reason.lower()


def test_covered_call_approved_sizes_by_shares_div_100():
    rm = make_options_manager()
    candidate = make_candidate(symbol="AAPL240119C00150000", delta=0.30)
    result = rm.evaluate(**base_kwargs(
        action="open_covered_call", confidence=0.9, contract_symbol=candidate["symbol"],
        candidate=candidate, shares_held=250,
    ))
    assert result.approved is True
    assert result.qty == 2


def test_cash_secured_put_rejected_with_insufficient_cash():
    rm = make_options_manager()
    candidate = make_candidate(strike=500.0)  # needs $50,000
    result = rm.evaluate(**base_kwargs(
        action="open_cash_secured_put", confidence=0.9, contract_symbol=candidate["symbol"],
        candidate=candidate, available_cash=1_000.0, equity=100_000.0,
    ))
    assert result.approved is False
    assert "cash-secured put" in result.reason.lower()


def test_cash_secured_put_sizes_by_cash_and_collateral_cap():
    rm = make_options_manager(max_options_collateral_pct=0.25)
    candidate = make_candidate(strike=100.0)  # $10,000 per contract
    # cash allows 5 contracts (50,000/10,000) but 25% of 100k equity = 25,000 -> caps at 2
    result = rm.evaluate(**base_kwargs(
        action="open_cash_secured_put", confidence=0.9, contract_symbol=candidate["symbol"],
        candidate=candidate, available_cash=50_000.0, equity=100_000.0,
    ))
    assert result.approved is True
    assert result.qty == 2


def test_cash_secured_put_capped_by_existing_collateral():
    rm = make_options_manager(max_options_collateral_pct=0.25)
    candidate = make_candidate(strike=100.0)  # $10,000 per contract
    # 25% of 100k = 25,000 budget, already 20,000 used -> only 5,000 remaining -> 0 contracts
    result = rm.evaluate(**base_kwargs(
        action="open_cash_secured_put", confidence=0.9, contract_symbol=candidate["symbol"],
        candidate=candidate, available_cash=50_000.0, equity=100_000.0,
        existing_options_collateral=20_000.0,
    ))
    assert result.approved is False
    assert "collateral" in result.reason.lower()


def test_close_position_requires_existing_position():
    rm = make_options_manager()
    result = rm.evaluate(**base_kwargs(
        action="close_position", confidence=0.9, already_has_option_position=False,
    ))
    assert result.approved is False
    assert "no existing option position" in result.reason.lower()


def test_close_position_approved_when_held():
    rm = make_options_manager()
    result = rm.evaluate(**base_kwargs(
        action="close_position", confidence=0.9, already_has_option_position=True,
    ))
    assert result.approved is True


def test_contract_symbol_mismatch_rejected():
    rm = make_options_manager()
    candidate = make_candidate(symbol="AAPL240119C00150000", delta=0.30)
    result = rm.evaluate(**base_kwargs(
        action="open_covered_call", confidence=0.9, contract_symbol="SOME_OTHER_SYMBOL",
        candidate=candidate, shares_held=100,
    ))
    assert result.approved is False
    assert "untrusted" in result.reason.lower()


def test_already_has_option_position_blocks_new_open():
    rm = make_options_manager()
    candidate = make_candidate(symbol="AAPL240119C00150000", delta=0.30)
    result = rm.evaluate(**base_kwargs(
        action="open_covered_call", confidence=0.9, contract_symbol=candidate["symbol"],
        candidate=candidate, shares_held=100, already_has_option_position=True,
    ))
    assert result.approved is False
    assert "already have an open option position" in result.reason.lower()
