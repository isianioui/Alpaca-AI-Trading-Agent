"""
Formal catalog of the deterministic entry-side gates in src/risk_manager.py,
plus a classifier that maps a logged decision's risk_reason string back to
the specific gate that produced it.

This is descriptive, not a new policy: every gate name/description below
names an actual `if` branch in RiskManager.evaluate() or
OptionsRiskManager.evaluate(), in the exact order those methods check them
(both short-circuit on the first failing check, so a decision is only ever
attributable to one gate). The classifier pattern-matches against the exact
reason-string prefixes those methods emit -- if risk_manager.py's wording
ever changes, this file's patterns need to move with it (see
tests/test_risk_gates.py for the coupling this relies on).

"hold" recommendations and exit-engine closes never reach these gates (the
LLM proposed no trade / the exit engine is a separate, unconditional
monitor -- see src/exit_engine.py) and are excluded from gate statistics
rather than counted as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STOCK_GATES: list[dict] = [
    {"id": "circuit_breaker", "name": "Circuit Breaker",
     "description": "Trading halts for the day once daily P&L breaches the max drawdown limit "
                     "-- checked before anything else, for every action."},
    {"id": "confidence", "name": "Confidence Threshold",
     "description": "The LLM's own confidence score must clear a minimum bar before its call is acted on."},
    {"id": "eligibility", "name": "Position Eligibility",
     "description": "A sell needs an existing holding to close; a buy is rejected if the account "
                     "already holds that symbol (no pyramiding)."},
    {"id": "position_limit", "name": "Open Position Limit",
     "description": "A new buy is rejected once the account is already at its max concurrent open positions."},
    {"id": "sizing", "name": "Position Sizing / Buying Power",
     "description": "Size is capped at a fixed % of equity and by available cash; rejected outright "
                     "if that leaves less than one share."},
]

OPTIONS_GATES: list[dict] = [
    {"id": "circuit_breaker", "name": "Circuit Breaker",
     "description": "Shares the stock pipeline's daily-drawdown circuit breaker -- one account, one limit."},
    {"id": "whitelist", "name": "Allowed Strategy Whitelist",
     "description": "Only covered_call, cash_secured_put, close, or hold are legal actions -- anything "
                     "else (naked calls, spreads, etc.) is rejected regardless of LLM confidence."},
    {"id": "confidence", "name": "Confidence Threshold",
     "description": "Same minimum-confidence bar as the stock pipeline, applied to the options call."},
    {"id": "eligibility", "name": "Position Eligibility",
     "description": "A close needs an existing option position on that underlying; an open is rejected "
                     "if one is already open."},
    {"id": "position_limit", "name": "Open Position Limit",
     "description": "A new open is rejected once the account is already at its max concurrent option positions."},
    {"id": "candidate_validation", "name": "Candidate Validation",
     "description": "The contract symbol the LLM names must exactly match a real, independently-fetched "
                     "live candidate -- an anti-hallucination check, not a formality."},
    {"id": "underlying_eligibility", "name": "Underlying Eligibility",
     "description": "A covered call needs >=100 shares already held; a cash-secured put needs enough "
                     "cash on hand to secure the strike."},
    {"id": "collateral_cap", "name": "Collateral Cap",
     "description": "A cash-secured put is additionally capped so total options collateral never "
                     "exceeds a fixed % of equity."},
]

def classify_stock_reason(reason: str) -> str:
    """Maps a RiskManager.evaluate() risk_reason string to the gate id that
    produced it, or 'approved' / 'other' (see module docstring for why this
    has to pattern-match risk_manager.py's literal wording)."""
    r = reason or ""
    if r.startswith("Circuit breaker"):
        return "circuit_breaker"
    if "below minimum" in r and "Confidence" in r:
        return "confidence"
    if r.startswith("No existing position") or "over-concentration" in r:
        return "eligibility"
    if r.startswith("Max open positions"):
        return "position_limit"
    if r.startswith("Insufficient buying power"):
        return "sizing"
    if r.startswith("Approved") or r.startswith("Full close"):
        return "approved"
    return "other"


def classify_options_reason(reason: str) -> str:
    """Options-side counterpart of classify_stock_reason(), matched against
    OptionsRiskManager.evaluate()'s literal reason strings."""
    r = reason or ""
    if r.startswith("Circuit breaker"):
        return "circuit_breaker"
    if "not an allowed options action" in r:
        return "whitelist"
    if "below minimum" in r and "Confidence" in r:
        return "confidence"
    if r.startswith("No existing option position") or "Already have an open option position" in r:
        return "eligibility"
    if r.startswith("Max open option positions"):
        return "position_limit"
    if "does not match a validated" in r:
        return "candidate_validation"
    if "shares held" in r or "cash insufficient" in r or "missing a valid strike" in r:
        return "underlying_eligibility"
    if "collateral cap" in r:
        return "collateral_cap"
    if r.startswith("Approved") or r.startswith("Close approved"):
        return "approved"
    return "other"


@dataclass
class GateStats:
    gates: list[dict]                       # STOCK_GATES or OPTIONS_GATES, in order
    blocked_counts: dict[str, int] = field(default_factory=dict)   # gate_id -> count
    evaluated: int = 0                        # non-hold decisions that actually reached the gate chain
    approved: int = 0                         # survived every gate
    holds_excluded: int = 0                   # LLM said hold -- never reached the gates
    errors_excluded: int = 0                  # status == "error" records, excluded entirely

    @property
    def blocked(self) -> int:
        return self.evaluated - self.approved


def _compute_stats(records: list[dict], gates: list[dict], classify) -> GateStats:
    stats = GateStats(gates=gates, blocked_counts={g["id"]: 0 for g in gates})
    for d in records:
        if d.get("status") == "error":
            stats.errors_excluded += 1
            continue
        reason = d.get("risk_reason") or ""
        gate_id = classify(reason)
        if gate_id == "other":
            # "Model recommended hold." and any unrecognized reason both land
            # here; only the hold case is expected in practice.
            stats.holds_excluded += 1
            continue
        stats.evaluated += 1
        if gate_id == "approved":
            stats.approved += 1
        else:
            stats.blocked_counts[gate_id] = stats.blocked_counts.get(gate_id, 0) + 1
    return stats


def compute_gate_stats(decisions: list[dict]) -> tuple[GateStats, GateStats]:
    """decisions: the raw records from src.logger.load_decisions(). Returns
    (stock_stats, options_stats) -- exit-engine records (trigger ==
    'exit_engine') are excluded from both since ExitEngine is a separate,
    unconditional monitor with no entry gates of its own."""
    stock_records = [
        d for d in decisions
        if d.get("asset_class") != "option" and d.get("trigger") != "exit_engine"
        and d.get("record_type") != "exit_engine_summary"
    ]
    options_records = [d for d in decisions if d.get("asset_class") == "option"]

    stock_stats = _compute_stats(stock_records, STOCK_GATES, classify_stock_reason)
    options_stats = _compute_stats(options_records, OPTIONS_GATES, classify_options_reason)
    return stock_stats, options_stats
