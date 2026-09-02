"""
Thin wrapper around Alpaca's official CLI (github.com/alpacahq/cli), the
Go binary invoked as `alpaca`. Shells out via subprocess and parses its
JSON stdout into plain Python objects.

This is what satisfies the hackathon's "must utilize Alpaca's MCP server
or its CLI tools" requirement -- it is a real, wired-in execution path
(see alpaca_client.py's submit_market_order/close_position and
options_client.py's submit_option_order/close_option_position, both
gated by EXECUTION_BACKEND), not a decorative example file.

Auth: ALPACA_API_KEY / ALPACA_SECRET_KEY env vars (same keys the SDK path
uses) -- never the interactive `alpaca profile login` OAuth flow, since
this needs to run unattended as part of an automated agent cycle.
ALPACA_LIVE_TRADE is explicitly stripped from the subprocess environment
on every call, so this wrapper can never accidentally opt into live
trading -- it always resolves to Alpaca's paper trading environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class AlpacaCLIError(Exception):
    """Raised on a non-zero CLI exit, a missing binary, or unparseable JSON output."""

    def __init__(self, message: str, *, exit_code: Optional[int] = None, stderr: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    last_equity: float

    @property
    def daily_pnl_pct(self) -> float:
        if self.last_equity == 0:
            return 0.0
        return (self.equity - self.last_equity) / self.last_equity


def _find_binary() -> str:
    """Resolution order: ALPACA_CLI_PATH env override -> PATH -> project-local ./bin/."""
    override = os.getenv("ALPACA_CLI_PATH")
    if override:
        return override

    on_path = shutil.which("alpaca")
    if on_path:
        return on_path

    project_root = Path(__file__).resolve().parent.parent
    for candidate in ("bin/alpaca.exe", "bin/alpaca"):
        local = project_root / candidate
        if local.exists():
            return str(local)

    raise AlpacaCLIError(
        "Alpaca CLI binary not found. Install it with "
        "`python scripts/install_alpaca_cli.py`, put it on PATH, or set "
        "ALPACA_CLI_PATH to its location. See README.md's 'Alpaca CLI' section."
    )


def _run(args: list[str], timeout: int = 30) -> "dict | list":
    binary = _find_binary()

    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise AlpacaCLIError(
            "Missing Alpaca credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
            "in your .env file (see .env.example)."
        )

    env = dict(os.environ)
    env["ALPACA_API_KEY"] = api_key
    env["ALPACA_SECRET_KEY"] = secret_key
    env.pop("ALPACA_LIVE_TRADE", None)  # force paper trading regardless of environment

    try:
        completed = subprocess.run(
            [binary, *args, "--quiet"],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise AlpacaCLIError(f"Alpaca CLI binary at {binary!r} is not executable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AlpacaCLIError(f"Alpaca CLI call timed out after {timeout}s: alpaca {' '.join(args)}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise AlpacaCLIError(
            f"Alpaca CLI exited {completed.returncode} for `alpaca {' '.join(args)}`: {detail}",
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AlpacaCLIError(
            f"Alpaca CLI returned non-JSON stdout for `alpaca {' '.join(args)}`: "
            f"{completed.stdout[:500]!r}"
        ) from exc


# ---------------------------------------------------------------------- #
# Account / positions
# ---------------------------------------------------------------------- #
def get_account() -> AccountSnapshot:
    data = _run(["account", "get"])
    return AccountSnapshot(
        equity=float(data["equity"]),
        cash=float(data["cash"]),
        buying_power=float(data["buying_power"]),
        portfolio_value=float(data["portfolio_value"]),
        last_equity=float(data["last_equity"]),
    )


def get_positions() -> list[dict]:
    data = _run(["position", "list"])
    return [
        {
            "symbol": p["symbol"],
            "qty": float(p["qty"]),
            "avg_entry_price": float(p["avg_entry_price"]),
            "current_price": float(p["current_price"]),
            "market_value": float(p["market_value"]),
            "unrealized_pl": float(p["unrealized_pl"]),
            "unrealized_plpc": float(p["unrealized_plpc"]),
        }
        for p in data
    ]


# ---------------------------------------------------------------------- #
# Orders (stocks + options -- Alpaca's order endpoint is shared; an
# options order is just one whose --symbol is an OCC contract symbol)
# ---------------------------------------------------------------------- #
def submit_market_order(symbol: str, qty: float, side: str) -> dict:
    """side must be 'buy' or 'sell'."""
    order = _run([
        "order", "submit",
        "--symbol", symbol,
        "--qty", str(qty),
        "--side", side.lower(),
        "--type", "market",
    ])
    return {
        "id": order["id"],
        "symbol": order["symbol"],
        "qty": order["qty"],
        "side": order["side"],
        "status": order["status"],
    }


def submit_option_order(contract_symbol: str, qty: int, side: str, position_intent: str) -> dict:
    """
    In this app, always called with side="sell", position_intent="sell_to_open"
    (opening a short covered call or cash-secured put) -- mirrors
    OptionsClient.submit_option_order's signature for drop-in compatibility.
    """
    order = _run([
        "order", "submit",
        "--symbol", contract_symbol,
        "--qty", str(qty),
        "--side", side.lower(),
        "--type", "market",
        "--position-intent", position_intent,
    ])
    return {
        "id": order["id"],
        "symbol": order["symbol"],
        "qty": order["qty"],
        "side": order["side"],
        "status": order["status"],
        "position_intent": order.get("position_intent"),
    }


def close_position(symbol: str) -> dict:
    order = _run(["position", "close", "--symbol-or-asset-id", symbol])
    return {"symbol": symbol, "status": "closed", "order_id": order["id"]}


def close_option_position(contract_symbol: str) -> dict:
    order = _run(["position", "close", "--symbol-or-asset-id", contract_symbol])
    return {"symbol": contract_symbol, "status": "closed", "order_id": order["id"]}
