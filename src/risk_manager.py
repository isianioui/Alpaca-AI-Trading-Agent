"""
Deterministic, non-LLM safety layer.

The LLM proposes; this module disposes. It converts a qualitative
buy/sell/hold call into a concrete order size (or a rejection), and
enforces hard limits the LLM cannot override — position sizing,
max concurrent positions, and a daily drawdown circuit breaker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.options_strategy import check_cash_secured_put_eligible, check_covered_call_eligible


@dataclass
class RiskLimits:
    max_position_pct: float = 0.05       # max % of equity in a single new position
    max_open_positions: int = 5
    max_daily_loss_pct: float = 0.03     # circuit breaker: stop trading for the day
    min_confidence_to_act: float = 0.55  # ignore low-conviction LLM calls


@dataclass
class RiskDecision:
    approved: bool
    qty: float
    reason: str


class RiskManager:
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()

    def circuit_breaker_tripped(self, daily_pnl_pct: float) -> bool:
        return daily_pnl_pct <= -abs(self.limits.max_daily_loss_pct)

    def evaluate(
        self,
        action: str,
        confidence: float,
        symbol: str,
        last_price: float,
        equity: float,
        cash: float,
        open_position_count: int,
        already_holds_symbol: bool,
        daily_pnl_pct: float,
    ) -> RiskDecision:
        if self.circuit_breaker_tripped(daily_pnl_pct):
            return RiskDecision(False, 0, f"Circuit breaker: daily P&L {daily_pnl_pct:.2%} "
                                           f"breached -{self.limits.max_daily_loss_pct:.0%} limit. No new trades today.")

        if action == "hold":
            return RiskDecision(False, 0, "Model recommended hold.")

        if confidence < self.limits.min_confidence_to_act:
            return RiskDecision(False, 0, f"Confidence {confidence:.2f} below minimum "
                                           f"{self.limits.min_confidence_to_act:.2f} threshold.")

        if action == "sell":
            if not already_holds_symbol:
                return RiskDecision(False, 0, f"No existing position in {symbol} to sell.")
            return RiskDecision(True, 0, "Full close of existing position approved.")

        # action == "buy"
        if already_holds_symbol:
            return RiskDecision(False, 0, f"Already holding {symbol}; skipping to avoid over-concentration.")

        if open_position_count >= self.limits.max_open_positions:
            return RiskDecision(False, 0, f"Max open positions ({self.limits.max_open_positions}) reached.")

        max_dollar_position = equity * self.limits.max_position_pct
        affordable_dollar_position = min(max_dollar_position, cash)

        if affordable_dollar_position < last_price:
            return RiskDecision(False, 0, "Insufficient buying power for even 1 share within risk limits.")

        qty = round(affordable_dollar_position / last_price, 4)
        return RiskDecision(True, qty, f"Approved: {qty} shares (~${qty * last_price:,.2f}, "
                                        f"{self.limits.max_position_pct:.0%} of equity cap).")


@dataclass
class OptionsRiskLimits:
    max_options_collateral_pct: float = 0.25   # max % of equity tied up as CSP cash collateral at once
    max_open_option_positions: int = 3
    min_confidence_to_act: float = 0.55

    @classmethod
    def from_env(cls) -> "OptionsRiskLimits":
        return cls(
            max_options_collateral_pct=float(os.getenv("MAX_OPTIONS_COLLATERAL_PCT", "0.25")),
            max_open_option_positions=int(os.getenv("MAX_OPEN_OPTION_POSITIONS", "3")),
        )


@dataclass
class OptionsRiskDecision:
    approved: bool
    qty: int
    reason: str


ALLOWED_OPTIONS_ACTIONS = {"open_covered_call", "open_cash_secured_put", "hold", "close_position"}


class OptionsRiskManager:
    """
    Sibling to RiskManager for the two allowed defined-risk options
    strategies. Composes a RiskManager instance to reuse its circuit
    breaker (same account, same daily P&L should halt both pipelines)
    without duplicating that math.

    Never trusts the LLM's claim of eligibility -- shares_held / cash /
    the candidate's own strike are independently re-checked here every
    time, and any action outside ALLOWED_OPTIONS_ACTIONS is rejected as
    defense in depth alongside the LLM prompt's own constraint.
    """

    def __init__(self, limits: Optional[OptionsRiskLimits] = None, stock_risk_manager: Optional[RiskManager] = None):
        self.limits = limits or OptionsRiskLimits.from_env()
        self._stock_rm = stock_risk_manager or RiskManager()

    def circuit_breaker_tripped(self, daily_pnl_pct: float) -> bool:
        return self._stock_rm.circuit_breaker_tripped(daily_pnl_pct)

    def evaluate(
        self,
        action: str,
        confidence: float,
        symbol: str,
        contract_symbol: str,
        candidate: Optional[dict],
        shares_held: float,
        available_cash: float,
        equity: float,
        existing_options_collateral: float,
        open_option_position_count: int,
        already_has_option_position: bool,
        daily_pnl_pct: float,
    ) -> OptionsRiskDecision:
        if self.circuit_breaker_tripped(daily_pnl_pct):
            return OptionsRiskDecision(False, 0, f"Circuit breaker: daily P&L {daily_pnl_pct:.2%} "
                                                   f"breached limit. No new options trades today.")

        if action not in ALLOWED_OPTIONS_ACTIONS:
            return OptionsRiskDecision(False, 0, f"Rejected: '{action}' is not an allowed options action "
                                                   f"(only open_covered_call, open_cash_secured_put, "
                                                   f"close_position, hold).")

        if action == "hold":
            return OptionsRiskDecision(False, 0, "Model recommended hold.")

        if confidence < self.limits.min_confidence_to_act:
            return OptionsRiskDecision(False, 0, f"Confidence {confidence:.2f} below minimum "
                                                   f"{self.limits.min_confidence_to_act:.2f} threshold.")

        if action == "close_position":
            if not already_has_option_position:
                return OptionsRiskDecision(False, 0, f"No existing option position on {symbol} to close.")
            return OptionsRiskDecision(True, 0, "Close approved.")

        # action is one of the two open strategies
        if already_has_option_position:
            return OptionsRiskDecision(False, 0, f"Already have an open option position on {symbol}; skipping.")

        if open_option_position_count >= self.limits.max_open_option_positions:
            return OptionsRiskDecision(False, 0, f"Max open option positions "
                                                   f"({self.limits.max_open_option_positions}) reached.")

        if candidate is None or candidate.get("symbol") != contract_symbol:
            return OptionsRiskDecision(False, 0, "Contract offered does not match a validated "
                                                   "candidate; rejecting untrusted symbol.")

        if action == "open_covered_call":
            if not check_covered_call_eligible(shares_held):
                return OptionsRiskDecision(False, 0, f"Covered call rejected: only {shares_held} "
                                                       f"shares held (need >=100).")
            qty = int(shares_held // 100)
            return OptionsRiskDecision(True, qty, f"Approved: sell {qty} covered call contract(s) "
                                                    f"against {shares_held} shares.")

        # action == "open_cash_secured_put"
        strike = candidate.get("strike")
        if not strike or strike <= 0:
            return OptionsRiskDecision(False, 0, "Cash-secured put rejected: candidate missing a valid strike.")

        if not check_cash_secured_put_eligible(available_cash, [candidate]):
            return OptionsRiskDecision(False, 0, f"Cash-secured put rejected: ${available_cash:,.2f} cash "
                                                   f"insufficient to secure strike ${strike:.2f} "
                                                   f"(needs ${strike * 100:,.2f}).")

        max_collateral_dollars = equity * self.limits.max_options_collateral_pct
        remaining_budget = max(0.0, max_collateral_dollars - existing_options_collateral)
        qty = min(int(remaining_budget // (strike * 100)), int(available_cash // (strike * 100)))

        if qty < 1:
            return OptionsRiskDecision(False, 0, f"Cash-secured put rejected: collateral cap "
                                                   f"({self.limits.max_options_collateral_pct:.0%} of equity) "
                                                   f"leaves no room.")

        return OptionsRiskDecision(True, qty, f"Approved: sell {qty} cash-secured put contract(s) at "
                                                f"strike ${strike:.2f} (${qty * strike * 100:,.2f} collateral).")
