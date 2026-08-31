"""
Deterministic, non-LLM safety layer.

The LLM proposes; this module disposes. It converts a qualitative
buy/sell/hold call into a concrete order size (or a rejection), and
enforces hard limits the LLM cannot override — position sizing,
max concurrent positions, and a daily drawdown circuit breaker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
