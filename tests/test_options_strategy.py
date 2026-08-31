from datetime import date

import pytest

from src.options_strategy import (
    check_cash_secured_put_eligible,
    check_covered_call_eligible,
    parse_occ_symbol,
    select_best_candidate,
)


def make_contract(**overrides) -> dict:
    contract = {
        "symbol": "AAPL240119C00150000",
        "underlying_symbol": "AAPL",
        "type": "call",
        "strike": 150.0,
        "expiration": "2024-01-19",
        "dte": 35,
        "bid": 2.0,
        "ask": 2.2,
        "mid": 2.1,
        "implied_volatility": 0.3,
        "delta": 0.30,
        "gamma": 0.02,
        "theta": -0.03,
        "vega": 0.1,
        "open_interest": "500",
    }
    contract.update(overrides)
    return contract


def test_covered_call_eligible_requires_100_shares():
    assert check_covered_call_eligible(99) is False
    assert check_covered_call_eligible(100) is True


def test_cash_secured_put_eligible_checks_cheapest_strike():
    chain = [make_contract(strike=200.0), make_contract(strike=50.0)]
    assert check_cash_secured_put_eligible(5_000, chain) is True   # covers the 50 strike (50*100)
    assert check_cash_secured_put_eligible(20_000, chain) is True  # covers both strikes
    assert check_cash_secured_put_eligible(1_000, chain) is False  # covers neither


def test_select_best_candidate_picks_closest_to_target_delta():
    chain = [
        make_contract(symbol="A", delta=0.20),
        make_contract(symbol="B", delta=0.30),
        make_contract(symbol="C", delta=0.45),
    ]
    result = select_best_candidate(chain)
    assert result["symbol"] == "B"


def test_select_best_candidate_filters_out_of_dte_band():
    chain = [
        make_contract(symbol="PERFECT_DELTA_BAD_DTE", delta=0.30, dte=20),
        make_contract(symbol="OK", delta=0.27, dte=35),
    ]
    result = select_best_candidate(chain)
    assert result["symbol"] == "OK"


def test_select_best_candidate_returns_none_when_no_candidates():
    chain = [make_contract(delta=0.05), make_contract(delta=0.90, dte=10)]
    assert select_best_candidate(chain) is None
    assert select_best_candidate([]) is None


def test_select_best_candidate_tiebreaks_on_highest_bid():
    chain = [
        make_contract(symbol="LOW_BID", delta=0.25, bid=1.0),
        make_contract(symbol="HIGH_BID", delta=0.35, bid=5.0),
    ]
    # both are 0.05 away from target delta 0.30 -> tiebreak on bid
    result = select_best_candidate(chain)
    assert result["symbol"] == "HIGH_BID"


def test_select_best_candidate_excludes_zero_bid():
    chain = [make_contract(delta=0.30, bid=0)]
    assert select_best_candidate(chain) is None


def test_parse_occ_symbol_decodes_call():
    result = parse_occ_symbol("AAPL240119C00150000")
    assert result == {
        "underlying_symbol": "AAPL",
        "expiration_date": date(2024, 1, 19),
        "type": "call",
        "strike": 150.0,
    }


def test_parse_occ_symbol_decodes_put_with_fractional_strike():
    result = parse_occ_symbol("MSFT250620P00047500")
    assert result == {
        "underlying_symbol": "MSFT",
        "expiration_date": date(2025, 6, 20),
        "type": "put",
        "strike": 47.5,
    }


def test_parse_occ_symbol_raises_on_malformed_input():
    with pytest.raises(ValueError):
        parse_occ_symbol("not_a_real_symbol")
