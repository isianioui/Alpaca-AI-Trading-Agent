"""
Orchestrates one full options agent cycle, mirroring trading_agent.py:

  for each symbol in the options watchlist:
      1. pull recent bars + technical indicators for the underlying
      2. fetch covered-call and cash-secured-put candidates from Alpaca
      3. ask the LLM (GPT-OSS-120B via Groq) which (if any) defined-risk strategy to open/close
      4. pass that decision through the deterministic options risk manager
      5. execute the resulting order on Alpaca paper trading (if approved)
      6. log everything, tagged asset_class="option", for the dashboard

Only two strategies are ever open-able here: covered call and
cash-secured put. This is additive to the existing stock pipeline --
trading_agent.py, llm_agent.py's stock flow, and risk_manager.py's
stock flow are untouched.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from src import trade_history
from src.alpaca_client import AlpacaClient
from src.indicators import build_feature_snapshot
from src.llm_agent import OptionsLLMAgent
from src.logger import log_decision
from src.options_client import OptionsClient
from src.risk_manager import OptionsRiskManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("options_trading_agent")

# See trading_agent.py's GROQ_CALL_DELAY_SECONDS for why this exists: one Groq
# call per symbol, and this loop often runs concurrently with the stock loop
# against the same free-tier rate limit.
GROQ_CALL_DELAY_SECONDS = float(os.getenv("GROQ_CALL_DELAY_SECONDS", "1.5"))


class OptionsTradingAgent:
    def __init__(
        self,
        alpaca_client: Optional[AlpacaClient] = None,
        options_client: Optional[OptionsClient] = None,
        llm_agent: Optional[OptionsLLMAgent] = None,
        risk_manager: Optional[OptionsRiskManager] = None,
        dry_run: bool = False,
    ):
        self.alpaca = alpaca_client or AlpacaClient()
        self.options = options_client or OptionsClient(trading_client=self.alpaca.trading_client)
        self.llm = llm_agent or OptionsLLMAgent()
        self.risk = risk_manager or OptionsRiskManager()
        self.dry_run = dry_run

    def run_cycle(self, watchlist: list[str]) -> list[dict]:
        """Run one full pass over the options watchlist. Returns the list of decision records."""
        account = self.alpaca.get_account()
        stock_positions = {p["symbol"]: p for p in self.alpaca.get_positions()}
        option_positions = self.options.get_option_positions()
        option_positions_by_underlying = {p["underlying_symbol"]: p for p in option_positions}
        existing_options_collateral = sum(
            p["strike"] * 100 * abs(p["qty"]) for p in option_positions if p["option_type"] == "put"
        )
        # Options market orders are rejected outright by Alpaca outside
        # regular market hours (422 "options market orders are only allowed
        # during market hours") -- checked once per cycle so every symbol's
        # order-submission decision (below) can skip cleanly instead of
        # attempting the order and catching the resulting error.
        market_is_open = self.alpaca.is_market_open()
        results = []

        daily_pnl_pct = account.daily_pnl_pct
        if self.risk.circuit_breaker_tripped(daily_pnl_pct):
            logger.warning("Circuit breaker tripped (daily P&L %.2f%%). Skipping options cycle.",
                            daily_pnl_pct * 100)

        for i, symbol in enumerate(watchlist):
            record = self._process_symbol(
                symbol, account, stock_positions, option_positions_by_underlying,
                existing_options_collateral, len(option_positions), daily_pnl_pct, market_is_open,
            )
            results.append(record)
            log_decision(record)
            if i < len(watchlist) - 1 and GROQ_CALL_DELAY_SECONDS > 0:
                time.sleep(GROQ_CALL_DELAY_SECONDS)

        return results

    def _process_symbol(
        self,
        symbol: str,
        account,
        stock_positions: dict,
        option_positions_by_underlying: dict,
        existing_options_collateral: float,
        open_option_position_count: int,
        daily_pnl_pct: float,
        market_is_open: bool,
    ) -> dict:
        try:
            shares_held = stock_positions.get(symbol, {}).get("qty", 0.0)
            current_option_position = option_positions_by_underlying.get(symbol)

            bars = self.alpaca.get_bars(symbol, lookback_days=90)
            features = build_feature_snapshot(bars)

            if "error" in features:
                return {"symbol": symbol, "action": "hold", "status": "skipped", "asset_class": "option",
                        "reason": "Not enough underlying price history yet."}

            cc_candidate = self.options.get_best_covered_call_candidate(symbol, shares_held)
            csp_candidate = self.options.get_best_cash_secured_put_candidate(symbol, account.cash)

            if cc_candidate is None and csp_candidate is None and current_option_position is None:
                return {"symbol": symbol, "action": "hold", "status": "skipped", "asset_class": "option",
                        "reason": "No covered-call or cash-secured-put candidates eligible, "
                                  "and no open option position to manage."}

            decision = self.llm.decide_option(
                symbol=symbol,
                underlying_features=features,
                account_context={
                    "equity": account.equity,
                    "cash": account.cash,
                    "shares_held": shares_held,
                    "daily_pnl_pct": round(daily_pnl_pct * 100, 2),
                    "open_option_position_count": open_option_position_count,
                },
                covered_call_candidate=cc_candidate,
                cash_secured_put_candidate=csp_candidate,
                current_option_position=current_option_position,
            )

            candidate_for_action = {
                "open_covered_call": cc_candidate,
                "open_cash_secured_put": csp_candidate,
            }.get(decision.action)

            risk_result = self.risk.evaluate(
                action=decision.action,
                confidence=decision.confidence,
                symbol=symbol,
                contract_symbol=decision.contract_symbol,
                candidate=candidate_for_action,
                shares_held=shares_held,
                available_cash=account.cash,
                equity=account.equity,
                existing_options_collateral=existing_options_collateral,
                open_option_position_count=open_option_position_count,
                already_has_option_position=current_option_position is not None,
                daily_pnl_pct=daily_pnl_pct,
            )

            order_result = None
            execution_status = None
            needs_order = decision.action in ("open_covered_call", "open_cash_secured_put") or (
                decision.action == "close_position" and current_option_position is not None
            )
            if risk_result.approved and not self.dry_run and needs_order:
                if not market_is_open:
                    # Alpaca rejects options market orders outright outside
                    # regular market hours -- this is expected, not an error,
                    # so execution is deferred rather than attempted and caught.
                    execution_status = "skipped_market_closed"
                elif decision.action in ("open_covered_call", "open_cash_secured_put"):
                    order_result = self.options.submit_option_order(
                        contract_symbol=decision.contract_symbol,
                        qty=risk_result.qty,
                        side="sell",
                        position_intent="sell_to_open",
                    )
                    execution_status = "executed"
                elif decision.action == "close_position":
                    order_result = self.options.close_option_position(current_option_position["symbol"])
                    execution_status = "executed" if order_result else execution_status
                    if order_result:
                        trade_history.record_closed_trade(
                            symbol=symbol,
                            asset_class="option",
                            qty=current_option_position["qty"],
                            entry_price=current_option_position["avg_entry_price"],
                            exit_price=current_option_position.get(
                                "current_price", current_option_position["avg_entry_price"]),
                            realized_pnl=current_option_position["unrealized_pl"],
                            realized_pnl_pct=current_option_position.get("unrealized_plpc", 0.0),
                            reason="LLM_DECISION",
                            trigger="llm_decision",
                            contract_symbol=current_option_position["symbol"],
                        )

            return {
                "symbol": symbol,
                "asset_class": "option",
                "llm_action": decision.action,
                "contract_symbol": decision.contract_symbol,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "risk_note": decision.risk_note,
                "covered_call_candidate": cc_candidate,
                "cash_secured_put_candidate": csp_candidate,
                "indicators": features,
                "risk_approved": risk_result.approved,
                "risk_reason": risk_result.reason,
                "order": order_result,
                "market_open": market_is_open,
                "execution_status": execution_status,
                "dry_run": self.dry_run,
                "status": "ok",
            }

        except Exception as exc:  # noqa: BLE001 - one bad symbol doesn't kill the cycle
            logger.exception("Error processing options for %s", symbol)
            return {"symbol": symbol, "status": "error", "asset_class": "option", "reason": str(exc)}
