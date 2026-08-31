"""
Deterministic, non-LLM options strategy selection logic.

Pure functions/dataclasses only — no network calls, no `datetime.now()`.
Given a chain of contract dicts (as produced by options_client.py) and
plain account-state scalars, decide eligibility for the two supported
defined-risk strategies (covered call, cash-secured put) and pick the
best candidate contract. Kept pure so it's directly unit-testable with
synthetic data, mirroring indicators.py's separation of pure math from
live fetching.
"""

from __future__ import annotations

from datetime import date

TARGET_DELTA = 0.30
DELTA_MIN, DELTA_MAX = 0.25, 0.35
DTE_MIN, DTE_MAX = 30, 45


def check_covered_call_eligible(shares_held: float) -> bool:
    return shares_held >= 100


def check_cash_secured_put_eligible(available_cash: float, put_chain: list[dict]) -> bool:
    return any(c["strike"] * 100 <= available_cash for c in put_chain)


def score_candidate(contract: dict, target_delta: float = TARGET_DELTA) -> float:
    return abs(abs(contract["delta"]) - target_delta)


def select_best_candidate(
    chain: list[dict],
    dte_min: int = DTE_MIN,
    dte_max: int = DTE_MAX,
    delta_min: float = DELTA_MIN,
    delta_max: float = DELTA_MAX,
) -> dict | None:
    """
    Pick the contract whose delta is closest to TARGET_DELTA within the
    given DTE and delta bands, requiring a positive bid (liquidity floor).
    Ties broken in favor of the higher bid. Returns None if nothing in
    the chain qualifies — no relaxation/fallback search.
    """
    candidates = [
        c for c in chain
        if c.get("dte") is not None and dte_min <= c["dte"] <= dte_max
        and c.get("delta") is not None and delta_min <= abs(c["delta"]) <= delta_max
        and (c.get("bid") or 0) > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda c: (score_candidate(c), -c["bid"]))


def parse_occ_symbol(occ_symbol: str) -> dict:
    """
    Decode a standard OCC option symbol, e.g. "AAPL240119C00150000" ->
    {"underlying_symbol": "AAPL", "expiration_date": date(2024, 1, 19),
     "type": "call", "strike": 150.0}.

    Format: root symbol (variable length) + YYMMDD (6) + C/P (1) +
    strike*1000 (8, zero-padded).
    """
    if len(occ_symbol) < 15:
        raise ValueError(f"Malformed OCC option symbol: {occ_symbol!r}")

    root = occ_symbol[:-15]
    date_part = occ_symbol[-15:-9]
    type_char = occ_symbol[-9]
    strike_part = occ_symbol[-8:]

    if not (date_part.isdigit() and strike_part.isdigit()) or type_char not in ("C", "P"):
        raise ValueError(f"Malformed OCC option symbol: {occ_symbol!r}")

    yy, mm, dd = int(date_part[0:2]), int(date_part[2:4]), int(date_part[4:6])
    try:
        expiration = date(2000 + yy, mm, dd)
    except ValueError as exc:
        raise ValueError(f"Malformed OCC option symbol: {occ_symbol!r}") from exc

    return {
        "underlying_symbol": root,
        "expiration_date": expiration,
        "type": "call" if type_char == "C" else "put",
        "strike": int(strike_part) / 1000.0,
    }
