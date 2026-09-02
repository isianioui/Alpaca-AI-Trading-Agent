"""
Append-only JSONL log of CLOSED trades (realized P&L), sibling to
logger.py's decision log. Populated whenever a position is actually
closed -- by an LLM sell/close_position decision or by the
deterministic ExitEngine's stop-loss / take-profit rules. This is
what the dashboard's Performance panel reads; it is never populated
with fabricated or placeholder numbers -- an empty file means an
honest "no closed trades yet" empty state.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.logger import load_decisions

DEFAULT_TRADE_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_history.jsonl"


def _find_entry_timestamp(symbol: str, contract_symbol: Optional[str] = None) -> Optional[str]:
    """Best-effort only: Alpaca's Position object carries no open-timestamp
    field, so the entry time is recovered from the most recent prior
    decision-log entry that actually placed an opening order for this
    symbol/contract. Returns None if no matching entry is found (e.g. the
    position pre-dates the decision log)."""
    match_key = contract_symbol or symbol
    for record in reversed(load_decisions()):
        if record.get("status") != "ok" or not record.get("order"):
            continue
        action = (record.get("llm_action") or "").lower()
        if action in ("sell", "close_position") or record.get("trigger") == "exit_engine":
            continue  # only opening actions count as an "entry"
        record_key = record.get("contract_symbol") or record.get("symbol")
        if record_key == match_key:
            return record.get("timestamp")
    return None


def record_closed_trade(
    symbol: str,
    asset_class: str,
    qty: float,
    entry_price: float,
    exit_price: float,
    realized_pnl: float,
    realized_pnl_pct: float,
    reason: str,
    trigger: str,
    contract_symbol: Optional[str] = None,
    path: Path = DEFAULT_TRADE_HISTORY_PATH,
) -> dict:
    """Appends one closed-trade record.

    exit_price / realized_pnl are read from Alpaca's live position snapshot
    taken immediately before the close order is submitted -- a close
    approximation of the actual fill, not a guaranteed-exact fill price
    (market orders can see minor slippage between decision and fill).
    """
    record = {
        "symbol": symbol,
        "contract_symbol": contract_symbol,
        "asset_class": asset_class,
        "qty": qty,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_time": _find_entry_timestamp(symbol, contract_symbol),
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
        "reason": reason,
        "trigger": trigger,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def load_trade_history(path: Path = DEFAULT_TRADE_HISTORY_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def compute_performance_stats(trades: list[dict]) -> dict:
    """Never fabricates: an empty trade list yields explicit zeros/None,
    not a placeholder number."""
    if not trades:
        return {
            "total_trades": 0,
            "total_realized_pnl": 0.0,
            "win_rate": None,
            "winning_trades": 0,
            "losing_trades": 0,
            "avg_pnl": 0.0,
        }
    total = sum(t["realized_pnl"] for t in trades)
    wins = [t for t in trades if t["realized_pnl"] > 0]
    losses = [t for t in trades if t["realized_pnl"] <= 0]
    return {
        "total_trades": len(trades),
        "total_realized_pnl": total,
        "win_rate": len(wins) / len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "avg_pnl": total / len(trades),
    }
