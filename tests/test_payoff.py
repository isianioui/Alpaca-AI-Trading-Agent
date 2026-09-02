import pytest

from src.payoff import (
    breakeven,
    cash_secured_put_breakeven,
    cash_secured_put_max_loss,
    cash_secured_put_pnl,
    covered_call_breakeven,
    covered_call_max_loss,
    covered_call_pnl,
    max_loss,
    payoff_points,
)

# ---------------------------------------------------------------------- #
# Cash-secured put: strike=100, premium=$2/share, 1 contract.
# Hand-calculated reference values:
#   premium collected total = 2 * 100 * 1 = $200
#   at/above strike: flat profit = $200
#   below strike: pnl = 200 - (100 - price) * 100
#   at price=0 (floor): pnl = 200 - 100*100 = -9800 = -(strike*100 - premium)
#   breakeven = strike - premium = 98
# ---------------------------------------------------------------------- #

def test_csp_pnl_flat_profit_at_strike():
    assert cash_secured_put_pnl(100, strike=100, premium=2, contracts=1) == pytest.approx(200)


def test_csp_pnl_flat_profit_above_strike():
    assert cash_secured_put_pnl(150, strike=100, premium=2, contracts=1) == pytest.approx(200)


def test_csp_pnl_below_strike():
    # pnl = 200 - (100-90)*100 = 200 - 1000 = -800
    assert cash_secured_put_pnl(90, strike=100, premium=2, contracts=1) == pytest.approx(-800)


def test_csp_pnl_floor_at_zero_matches_max_loss():
    pnl_at_zero = cash_secured_put_pnl(0, strike=100, premium=2, contracts=1)
    assert pnl_at_zero == pytest.approx(-9800)
    assert cash_secured_put_max_loss(strike=100, premium=2, contracts=1) == pytest.approx(9800)
    assert pnl_at_zero == pytest.approx(-cash_secured_put_max_loss(strike=100, premium=2, contracts=1))


def test_csp_breakeven():
    be = cash_secured_put_breakeven(strike=100, premium=2)
    assert be == pytest.approx(98)
    assert cash_secured_put_pnl(be, strike=100, premium=2, contracts=1) == pytest.approx(0)


def test_csp_scales_linearly_with_contracts():
    assert cash_secured_put_pnl(90, strike=100, premium=2, contracts=3) == pytest.approx(-2400)
    assert cash_secured_put_max_loss(strike=100, premium=2, contracts=3) == pytest.approx(29400)


def test_csp_zero_premium():
    assert cash_secured_put_max_loss(strike=50, premium=0, contracts=1) == pytest.approx(5000)
    assert cash_secured_put_breakeven(strike=50, premium=0) == pytest.approx(50)


# ---------------------------------------------------------------------- #
# Covered call: strike=110, premium=$3/share, 1 contract, cost_basis=$100/share.
# Hand-calculated reference values:
#   premium collected total = 3 * 100 * 1 = $300
#   at/above strike (called away): pnl = (110-100)*100 + 300 = 1000+300 = $1300 (max profit)
#   below strike: pnl = (price-100)*100 + 300
#   at price=0 (floor): pnl = (0-100)*100 + 300 = -9700 = -(cost_basis*100 - premium)
#   breakeven = cost_basis - premium = 97
# ---------------------------------------------------------------------- #

def test_covered_call_pnl_capped_at_strike():
    assert covered_call_pnl(110, strike=110, premium=3, contracts=1, cost_basis=100) == pytest.approx(1300)


def test_covered_call_pnl_capped_above_strike():
    # Called away at the strike regardless of how far price runs past it.
    assert covered_call_pnl(150, strike=110, premium=3, contracts=1, cost_basis=100) == pytest.approx(1300)
    assert covered_call_pnl(500, strike=110, premium=3, contracts=1, cost_basis=100) == pytest.approx(1300)


def test_covered_call_pnl_below_strike():
    # pnl = (90-100)*100 + 300 = -1000+300 = -700
    assert covered_call_pnl(90, strike=110, premium=3, contracts=1, cost_basis=100) == pytest.approx(-700)


def test_covered_call_pnl_floor_at_zero_matches_max_loss():
    pnl_at_zero = covered_call_pnl(0, strike=110, premium=3, contracts=1, cost_basis=100)
    assert pnl_at_zero == pytest.approx(-9700)
    assert covered_call_max_loss(cost_basis=100, premium=3, contracts=1) == pytest.approx(9700)
    assert pnl_at_zero == pytest.approx(-covered_call_max_loss(cost_basis=100, premium=3, contracts=1))


def test_covered_call_breakeven():
    be = covered_call_breakeven(cost_basis=100, premium=3)
    assert be == pytest.approx(97)
    assert covered_call_pnl(be, strike=110, premium=3, contracts=1, cost_basis=100) == pytest.approx(0)


def test_covered_call_scales_linearly_with_contracts():
    assert covered_call_pnl(90, strike=110, premium=3, contracts=2, cost_basis=100) == pytest.approx(-1400)
    assert covered_call_max_loss(cost_basis=100, premium=3, contracts=2) == pytest.approx(19400)


def test_covered_call_not_falsely_capped_on_the_downside():
    # Loss keeps growing past the "flat-bottomed" appearance of a defined-risk
    # diagram -- honesty check per the spec: only bounded by price -> $0, not
    # a true capped-loss floor like the put's collateral.
    loss_at_50 = -covered_call_pnl(50, strike=110, premium=3, contracts=1, cost_basis=100)
    loss_at_10 = -covered_call_pnl(10, strike=110, premium=3, contracts=1, cost_basis=100)
    assert loss_at_10 > loss_at_50


# ---------------------------------------------------------------------- #
# Dispatch helpers
# ---------------------------------------------------------------------- #

def test_max_loss_dispatch_csp():
    assert max_loss("cash_secured_put", strike=100, premium=2, contracts=1) == pytest.approx(9800)


def test_max_loss_dispatch_covered_call():
    assert max_loss("covered_call", strike=110, premium=3, contracts=1, cost_basis=100) == pytest.approx(9700)


def test_max_loss_covered_call_requires_cost_basis():
    with pytest.raises(ValueError):
        max_loss("covered_call", strike=110, premium=3, contracts=1)


def test_max_loss_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        max_loss("iron_condor", strike=100, premium=1, contracts=1)


def test_breakeven_dispatch():
    assert breakeven("cash_secured_put", strike=100, premium=2) == pytest.approx(98)
    assert breakeven("covered_call", strike=110, premium=3, cost_basis=100) == pytest.approx(97)


# ---------------------------------------------------------------------- #
# payoff_points
# ---------------------------------------------------------------------- #

def test_payoff_points_csp_includes_floor_strike_and_flat_region():
    points = payoff_points("cash_secured_put", strike=100, premium=2, contracts=1)
    by_price = dict(points)
    assert by_price[0.0] == pytest.approx(-9800)
    assert by_price[100.0] == pytest.approx(200)
    above_strike = [pnl for p, pnl in points if p >= 100]
    assert all(pnl == pytest.approx(200) for pnl in above_strike)


def test_payoff_points_csp_breakeven_is_exact_zero_crossing():
    points = payoff_points("cash_secured_put", strike=100, premium=2, contracts=1)
    by_price = dict(points)
    assert by_price[98.0] == pytest.approx(0, abs=1e-9)


def test_payoff_points_covered_call_requires_cost_basis():
    with pytest.raises(ValueError):
        payoff_points("covered_call", strike=110, premium=3, contracts=1)


def test_payoff_points_covered_call_matches_pnl_function():
    points = payoff_points("covered_call", strike=110, premium=3, contracts=1, cost_basis=100)
    for price, pnl in points:
        assert pnl == pytest.approx(covered_call_pnl(price, 110, 3, 1, 100))


def test_payoff_points_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        payoff_points("iron_condor", strike=100, premium=1, contracts=1)


def test_payoff_points_respects_num_points_minimum():
    with pytest.raises(ValueError):
        payoff_points("cash_secured_put", strike=100, premium=2, contracts=1, num_points=1)
