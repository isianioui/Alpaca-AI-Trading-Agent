"""
Orchestrates one full agent cycle:

  for each symbol in the watchlist:
      1. pull recent bars from Alpaca
      2. compute technical indicators
      3. ask the LLM (GPT-OSS-120B via Groq) for a buy/sell/hold decision + reasoning
      4. pass that decision through the deterministic risk manager
      5. execute the resulting order on Alpaca paper trading (if approved)
      6. log everything for the dashboard / audit trail

This is the class both main.py (CLI) and dashboard.py (Streamlit) call
into, so there's a single source of truth for agent behavior.
"""

from __future__ import annotations

import logging
from typing import Optional

from src import trade_history
from src.alpaca_client import AlpacaClient
from src.indicators import build_feature_snapshot
from src.llm_agent import LLMTradingAgent
from src.logger import log_decision
from src.risk_manager import RiskLimits, RiskManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trading_agent")


class TradingAgent:
    def __init__(
        self,
        alpaca_client: Optional[AlpacaClient] = None,
        llm_agent: Optional[LLMTradingAgent] = None,
        risk_manager: Optional[RiskManager] = None,
        dry_run: bool = False,
    ):
        self.alpaca = alpaca_client or AlpacaClient()
        self.llm = llm_agent or LLMTradingAgent()
        self.risk = risk_manager or RiskManager()
        self.dry_run = dry_run

    def run_cycle(self, watchlist: list[str]) -> list[dict]:
        """Run one full pass over the watchlist. Returns the list of decision records."""
        account = self.alpaca.get_account()
        positions = {p["symbol"]: p for p in self.alpaca.get_positions()}
        results = []

        daily_pnl_pct = account.daily_pnl_pct
        if self.risk.circuit_breaker_tripped(daily_pnl_pct):
            logger.warning("Circuit breaker tripped (daily P&L %.2f%%). Skipping cycle.", daily_pnl_pct * 100)

        for symbol in watchlist:
            record = self._process_symbol(symbol, account, positions, daily_pnl_pct)
            results.append(record)
            log_decision(record)

        return results

    def _process_symbol(self, symbol: str, account, positions: dict, daily_pnl_pct: float) -> dict:
        try:
            bars = self.alpaca.get_bars(symbol, lookback_days=90)
            features = build_feature_snapshot(bars)

            if "error" in features:
                return {"symbol": symbol, "action": "hold", "status": "skipped",
                         "reason": "Not enough price history yet."}

            current_position = positions.get(symbol)

            decision = self.llm.decide(
                symbol=symbol,
                features=features,
                account_context={
                    "equity": account.equity,
                    "cash": account.cash,
                    "buying_power": account.buying_power,
                    "daily_pnl_pct": round(daily_pnl_pct * 100, 2),
                    "open_position_count": len(positions),
                },
                current_position=current_position,
            )

            risk_result = self.risk.evaluate(
                action=decision.action,
                confidence=decision.confidence,
                symbol=symbol,
                last_price=features["last_close"],
                equity=account.equity,
                cash=account.cash,
                open_position_count=len(positions),
                already_holds_symbol=symbol in positions,
                daily_pnl_pct=daily_pnl_pct,
            )

            order_result = None
            if risk_result.approved and not self.dry_run:
                if decision.action == "buy":
                    order_result = self.alpaca.submit_market_order(symbol, risk_result.qty, "buy")
                elif decision.action == "sell":
                    order_result = self.alpaca.close_position(symbol)
                    if order_result and current_position:
                        trade_history.record_closed_trade(
                            symbol=symbol,
                            asset_class="stock",
                            qty=current_position["qty"],
                            entry_price=current_position["avg_entry_price"],
                            exit_price=current_position["current_price"],
                            realized_pnl=current_position["unrealized_pl"],
                            realized_pnl_pct=current_position["unrealized_plpc"],
                            reason="LLM_DECISION",
                            trigger="llm_decision",
                        )

            return {
                "symbol": symbol,
                "llm_action": decision.action,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "risk_note": decision.risk_note,
                "indicators": features,
                "risk_approved": risk_result.approved,
                "risk_reason": risk_result.reason,
                "order": order_result,
                "dry_run": self.dry_run,
                "status": "ok",
            }

        except Exception as exc:  # noqa: BLE001 - surface every failure into the log for the demo
            logger.exception("Error processing %s", symbol)
            return {"symbol": symbol, "status": "error", "reason": str(exc)}
