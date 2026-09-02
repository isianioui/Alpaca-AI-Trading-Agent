"""
Deterministic, rule-based position-exit engine -- the exit-side
counterpart to risk_manager.py's entry-side sizing/eligibility checks.
No LLM call: closing a position on a stop-loss or take-profit trigger
is a mechanical decision, not a judgment call, so it stays out of the
LLM's hands entirely (same "the LLM proposes, deterministic code
disposes" principle used everywhere else in this app).

Stock positions: Alpaca's `unrealized_plpc` on a stock Position is
already a plain long-only percentage (this app never shorts stocks --
trading_agent.py only ever buys, or fully closes back to flat), so the
stop-loss/take-profit thresholds are applied to it directly.

Option positions: every option position this app ever opens is SHORT
(options_client.submit_option_order is always called with side="sell",
position_intent="sell_to_open" -- covered calls and cash-secured puts
are both short-premium strategies). For a short position, Alpaca
stores qty, cost_basis and market_value all negative-signed, so
unrealized_pl = market_value - cost_basis and
unrealized_plpc = unrealized_pl / abs(cost_basis) already come back
POSITIVE-is-profit the same as a long position. Worked example: sell a
put for a $500 credit -> cost_basis = -500; price drifts and it can
now be bought back for $300 -> market_value = -300;
unrealized_pl = -300 - (-500) = +200 (a real $200 profit, correctly
positive). That also means unrealized_plpc on a short option directly
represents "% of the original credit captured as profit so far" --
exactly the number covered-call/cash-secured-put management rules are
usually expressed in:
  - OPTIONS_PROFIT_TARGET_PCT: close once unrealized_plpc has captured
    this fraction of the original credit (e.g. 0.50 = buy back once
    profitable by 50% of the credit received -- a standard "50% rule"
    for short premium).
  - OPTIONS_STOP_MULTIPLE: close once the cost to buy back the option
    has grown to this multiple of the credit received. In P&L-pct
    terms that threshold is -(OPTIONS_STOP_MULTIPLE - 1); e.g. a 2x
    stop means "it now costs twice what you collected", i.e. you're
    down the entire original credit again = -100% unrealized_plpc.

This sign-convention reasoning is derived from Alpaca's Position field
semantics (qty / cost_basis / market_value all share the position's
sign), not confirmed against a live filled short-option position --
the paper account this was built against currently holds zero option
positions to test it on. Spot-check the first real short-option close
against this logic before trusting it unattended.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExitLimits:
    stop_loss_pct: float = -0.08              # close a stock position down 8%+ from entry
    take_profit_pct: float = 0.15              # close a stock position up 15%+ from entry
    options_profit_target_pct: float = 0.50    # close a short option once 50% of credit is captured
    options_stop_multiple: float = 2.0         # close a short option if cost-to-close reaches 2x credit received

    @classmethod
    def from_env(cls) -> "ExitLimits":
        return cls(
            stop_loss_pct=-abs(float(os.getenv("STOP_LOSS_PCT", "0.08"))),
            take_profit_pct=abs(float(os.getenv("TAKE_PROFIT_PCT", "0.15"))),
            options_profit_target_pct=abs(float(os.getenv("OPTIONS_PROFIT_TARGET_PCT", "0.50"))),
            options_stop_multiple=abs(float(os.getenv("OPTIONS_STOP_MULTIPLE", "2.0"))),
        )


@dataclass
class ExitDecision:
    symbol: str
    asset_class: str   # "stock" | "option"
    action: str          # "HOLD" | "CLOSE"
    reason: str
    unrealized_plpc: float
    position: dict


class ExitEngine:
    """Mirrors RiskManager's evaluate()-returns-a-decision-object shape for
    consistency, but on the exit side: every open position is checked
    independently against fixed thresholds, no LLM in the loop."""

    def __init__(self, limits: Optional[ExitLimits] = None):
        self.limits = limits or ExitLimits.from_env()

    def evaluate_stock_position(self, position: dict) -> ExitDecision:
        plpc = position["unrealized_plpc"]
        symbol = position["symbol"]

        if plpc <= self.limits.stop_loss_pct:
            return ExitDecision(
                symbol, "stock", "CLOSE",
                f"STOP_LOSS: {plpc:+.2%} <= {self.limits.stop_loss_pct:.0%} limit",
                plpc, position,
            )
        if plpc >= self.limits.take_profit_pct:
            return ExitDecision(
                symbol, "stock", "CLOSE",
                f"TAKE_PROFIT: {plpc:+.2%} >= {self.limits.take_profit_pct:.0%} target",
                plpc, position,
            )
        return ExitDecision(
            symbol, "stock", "HOLD", f"Within thresholds ({plpc:+.2%})", plpc, position,
        )

    def evaluate_option_position(self, position: dict) -> ExitDecision:
        plpc = position["unrealized_plpc"]
        symbol = position.get("underlying_symbol", position["symbol"])
        stop_threshold = -(self.limits.options_stop_multiple - 1)

        if plpc <= stop_threshold:
            return ExitDecision(
                symbol, "option", "CLOSE",
                f"OPTIONS_STOP: cost to close has reached ~{self.limits.options_stop_multiple:.1f}x "
                f"the credit received ({plpc:+.2%} of credit)",
                plpc, position,
            )
        if plpc >= self.limits.options_profit_target_pct:
            return ExitDecision(
                symbol, "option", "CLOSE",
                f"OPTIONS_PROFIT_TARGET: {plpc:+.2%} of max credit captured "
                f">= {self.limits.options_profit_target_pct:.0%} target",
                plpc, position,
            )
        return ExitDecision(
            symbol, "option", "HOLD", f"Within thresholds ({plpc:+.2%} of credit)", plpc, position,
        )

    def check_positions(self, stock_positions: list[dict], option_positions: list[dict]) -> list[ExitDecision]:
        decisions = [self.evaluate_stock_position(p) for p in stock_positions]
        decisions += [self.evaluate_option_position(p) for p in option_positions]
        return decisions


def run_exit_engine(
    alpaca_client,
    options_client,
    exit_engine: Optional[ExitEngine] = None,
    dry_run: bool = True,
) -> list[dict]:
    """One full exit-engine pass over every open stock + option position.

    Every CLOSE decision is persisted to the shared decision log (tagged
    trigger="exit_engine", distinct from trigger="llm_decision") and, once
    actually executed (dry_run=False), to trade_history.py. HOLD decisions
    are returned for immediate display only -- not persisted, otherwise
    every routine "still within thresholds" position would spam the
    decision log on every run.
    """
    from src import trade_history
    from src.logger import log_decision

    engine = exit_engine or ExitEngine()
    stock_positions = alpaca_client.get_positions()
    option_positions = options_client.get_option_positions()
    decisions = engine.check_positions(stock_positions, option_positions)

    results = []
    for d in decisions:
        result = {
            "symbol": d.symbol,
            "asset_class": d.asset_class,
            "action": d.action,
            "reason": d.reason,
            "unrealized_plpc": d.unrealized_plpc,
            "dry_run": dry_run,
            "order": None,
        }

        if d.action == "CLOSE":
            position = d.position
            contract_symbol = position["symbol"] if d.asset_class == "option" else None
            reason_code = d.reason.split(":", 1)[0].strip()

            if not dry_run:
                if d.asset_class == "stock":
                    result["order"] = alpaca_client.close_position(position["symbol"])
                else:
                    result["order"] = options_client.close_option_position(position["symbol"])

                # Realized P&L/exit price are read from the live position
                # snapshot taken immediately before the close order was
                # submitted -- a close approximation of the actual fill for
                # a market order, not a guaranteed-exact number.
                trade_history.record_closed_trade(
                    symbol=d.symbol,
                    asset_class=d.asset_class,
                    qty=position["qty"],
                    entry_price=position["avg_entry_price"],
                    exit_price=position.get("current_price", position["avg_entry_price"]),
                    realized_pnl=position["unrealized_pl"],
                    realized_pnl_pct=position["unrealized_plpc"],
                    reason=reason_code,
                    trigger="exit_engine",
                    contract_symbol=contract_symbol,
                )

            log_decision({
                "symbol": d.symbol,
                "asset_class": "option" if d.asset_class == "option" else None,
                "contract_symbol": contract_symbol,
                "llm_action": reason_code,
                "trigger": "exit_engine",
                "reasoning": d.reason,
                "risk_note": "Deterministic exit-engine rule (stop-loss / take-profit) -- not an LLM judgment call.",
                "risk_approved": True,
                "risk_reason": d.reason,
                "indicators": {"unrealized_plpc": d.unrealized_plpc, "position": position},
                "order": result["order"],
                "dry_run": dry_run,
                "status": "ok",
            })

        results.append(result)

    # One lightweight summary record per run (regardless of dry_run), even
    # when nothing closed -- this is what the dashboard's "Latest Cycle"
    # narrative card reads to describe an exit-engine run ("N checked, all
    # within thresholds"), since individual HOLD decisions are deliberately
    # not persisted above (that would spam the decision log every run).
    close_count = sum(1 for r in results if r["action"] == "CLOSE")
    log_decision({
        "record_type": "exit_engine_summary",
        "trigger": "exit_engine",
        "positions_checked": len(results),
        "closes": close_count,
        "holds": len(results) - close_count,
        "closed_symbols": [r["symbol"] for r in results if r["action"] == "CLOSE"],
        "dry_run": dry_run,
        "status": "ok",
    })

    return results
