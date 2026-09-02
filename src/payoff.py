"""
Pure payoff-diagram math for the two single-leg options strategies this
project ever opens -- covered call, cash-secured put -- both always
SHORT/sold positions. No network calls, no datetime.now(); mirrors
options_strategy.py's separation of pure math from live fetching so this
is directly unit-testable with hand-calculated values (tests/test_payoff.py).

Both strategies price out in dollars per *contract* (100 shares), so every
function takes a per-share `strike`/`premium`/`cost_basis` and a `contracts`
count, and returns whole-position dollars.
"""

from __future__ import annotations

STRATEGIES = ("cash_secured_put", "covered_call")


def _check_strategy(strategy: str) -> None:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy!r} (expected one of {STRATEGIES})")


def cash_secured_put_pnl(underlying_price: float, strike: float, premium: float, contracts: int) -> float:
    """Short put, cash collateral. Flat at the full premium collected once
    underlying_price >= strike (expires worthless); below strike, the seller
    is assigned and eats every dollar the stock falls short of the strike,
    offset by the premium already collected."""
    premium_total = premium * 100 * contracts
    if underlying_price >= strike:
        return premium_total
    return premium_total - (strike - underlying_price) * 100 * contracts


def cash_secured_put_max_loss(strike: float, premium: float, contracts: int) -> float:
    """Worst case: underlying_price -> 0. Positive number = dollars at risk."""
    return strike * 100 * contracts - premium * 100 * contracts


def cash_secured_put_breakeven(strike: float, premium: float) -> float:
    return strike - premium


def covered_call_pnl(
    underlying_price: float, strike: float, premium: float, contracts: int, cost_basis: float,
) -> float:
    """Combined position: 100+ shares held (at cost_basis) + a short call
    against them. Profit on the shares is capped at the strike (called away
    there), but the downside is only cushioned by the premium, not capped --
    it keeps falling as the stock falls, same as owning the stock outright."""
    capped_price = min(underlying_price, strike)
    premium_total = premium * 100 * contracts
    return (capped_price - cost_basis) * 100 * contracts + premium_total


def covered_call_max_loss(cost_basis: float, premium: float, contracts: int) -> float:
    """Worst case: underlying_price -> 0. Bounded by the stock going to $0,
    not "defined risk" the way the cash-secured put's collateral floor is --
    positive number = dollars at risk."""
    return cost_basis * 100 * contracts - premium * 100 * contracts


def covered_call_breakeven(cost_basis: float, premium: float) -> float:
    return cost_basis - premium


def max_loss(
    strategy: str, strike: float, premium: float, contracts: int, cost_basis: float | None = None,
) -> float:
    _check_strategy(strategy)
    if strategy == "cash_secured_put":
        return cash_secured_put_max_loss(strike, premium, contracts)
    if cost_basis is None:
        raise ValueError("covered_call max_loss requires cost_basis")
    return covered_call_max_loss(cost_basis, premium, contracts)


def breakeven(strategy: str, strike: float, premium: float, cost_basis: float | None = None) -> float:
    _check_strategy(strategy)
    if strategy == "cash_secured_put":
        return cash_secured_put_breakeven(strike, premium)
    if cost_basis is None:
        raise ValueError("covered_call breakeven requires cost_basis")
    return covered_call_breakeven(cost_basis, premium)


def payoff_points(
    strategy: str,
    strike: float,
    premium: float,
    contracts: int,
    cost_basis: float | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    num_points: int = 41,
) -> list[tuple[float, float]]:
    """Returns (underlying_price, pnl) points across a price range, for
    plotting a payoff-at-expiry chart. The strike, breakeven, and $0 floor
    are always included exactly (not just interpolated near them) so a
    renderer can place markers/shading precisely on real computed points."""
    _check_strategy(strategy)
    if strategy == "covered_call" and cost_basis is None:
        raise ValueError("covered_call payoff_points requires cost_basis")
    if num_points < 2:
        raise ValueError("num_points must be >= 2")

    if price_min is None:
        price_min = 0.0
    if price_max is None:
        price_max = strike * 1.6

    step = (price_max - price_min) / (num_points - 1)
    prices = {price_min + i * step for i in range(num_points)}

    be = breakeven(strategy, strike, premium, cost_basis=cost_basis)
    for extra in (strike, be, 0.0):
        if price_min <= extra <= price_max:
            prices.add(extra)

    pnl_fn = (
        (lambda p: cash_secured_put_pnl(p, strike, premium, contracts)) if strategy == "cash_secured_put"
        else (lambda p: covered_call_pnl(p, strike, premium, contracts, cost_basis))
    )
    return [(p, pnl_fn(p)) for p in sorted(prices)]
