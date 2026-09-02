"""
Thin wrapper around Alpaca's options market data and trading APIs.

Mirrors alpaca_client.py's shape: centralizes every options-related call
to Alpaca so the rest of the codebase never talks to the SDK directly.
Defaults to PAPER TRADING via the shared credentials/paper flag.

Both strategies this app supports (covered call, cash-secured put) are
single-leg orders — no multi-leg/OrderClass.MLEG machinery is used here.
"""

from __future__ import annotations

import os
import socket
from datetime import date, timedelta
from typing import Optional

from alpaca.data.enums import OptionsFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus, ContractType, OrderSide, PositionIntent, TimeInForce
from alpaca.trading.requests import GetOptionContractsRequest, MarketOrderRequest

from src import alpaca_cli
from src.options_strategy import parse_occ_symbol, select_best_candidate

CANDIDATE_FETCH_DTE_MIN = 25
CANDIDATE_FETCH_DTE_MAX = 50

# alpaca-py's REST client (this version) exposes no per-call/per-client HTTP
# timeout, so a dropped connection can hang a request forever with zero CPU
# activity (observed live: a socket sat in CLOSE_WAIT with the process
# stuck). Set a generous process-wide floor so a stalled connection fails
# loudly instead of hanging the whole agent cycle indefinitely.
if socket.getdefaulttimeout() is None:
    socket.setdefaulttimeout(30)


class OptionsClient:
    """Wraps Alpaca's OptionHistoricalDataClient + TradingClient's options capabilities."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
        trading_client: Optional[TradingClient] = None,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.paper = paper if paper is not None else os.getenv("ALPACA_PAPER", "true").lower() == "true"
        self.execution_backend = os.getenv("EXECUTION_BACKEND", "cli").lower()

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Missing Alpaca credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
                "in your .env file (see .env.example)."
            )

        self.trading_client = trading_client or TradingClient(self.api_key, self.secret_key, paper=self.paper)
        self.data_client = OptionHistoricalDataClient(self.api_key, self.secret_key)

    # ------------------------------------------------------------------ #
    # Chain data
    # ------------------------------------------------------------------ #
    def get_option_chain(
        self,
        underlying_symbol: str,
        expiration_gte: date,
        expiration_lte: date,
        option_type: str,
    ) -> list[dict]:
        """side must be 'call' or 'put'. Joins tradable-contract metadata
        (strike/expiration) with live quotes/greeks/IV, keyed by OCC symbol."""
        contract_type = ContractType.CALL if option_type == "call" else ContractType.PUT

        contracts = []
        page_token = None
        while True:
            request = GetOptionContractsRequest(
                underlying_symbols=[underlying_symbol],
                status=AssetStatus.ACTIVE,
                expiration_date_gte=expiration_gte,
                expiration_date_lte=expiration_lte,
                type=contract_type,
                limit=500,
                page_token=page_token,
            )
            response = self.trading_client.get_option_contracts(request)
            contracts.extend(response.option_contracts or [])
            page_token = response.next_page_token
            if not page_token:
                break

        if not contracts:
            return []

        chain_request = OptionChainRequest(
            underlying_symbol=underlying_symbol,
            feed=OptionsFeed.INDICATIVE,
            type=contract_type,
            expiration_date_gte=expiration_gte,
            expiration_date_lte=expiration_lte,
        )
        snapshots = self.data_client.get_option_chain(chain_request)

        today = date.today()
        result = []
        for contract in contracts:
            snapshot = snapshots.get(contract.symbol)
            if snapshot is None:
                continue  # no live quote right now -- not currently tradable

            quote = snapshot.latest_quote
            bid = float(quote.bid_price) if quote and quote.bid_price else None
            ask = float(quote.ask_price) if quote and quote.ask_price else None
            greeks = snapshot.greeks

            result.append({
                "symbol": contract.symbol,
                "underlying_symbol": underlying_symbol,
                "type": option_type,
                "strike": float(contract.strike_price),
                "expiration": contract.expiration_date.isoformat(),
                "dte": (contract.expiration_date - today).days,
                "bid": bid,
                "ask": ask,
                "mid": round((bid + ask) / 2, 4) if bid is not None and ask is not None else None,
                "implied_volatility": snapshot.implied_volatility,
                "delta": greeks.delta if greeks else None,
                "gamma": greeks.gamma if greeks else None,
                "theta": greeks.theta if greeks else None,
                "vega": greeks.vega if greeks else None,
                "open_interest": contract.open_interest,
            })
        return result

    def get_best_covered_call_candidate(self, symbol: str, shares_held: float) -> Optional[dict]:
        """Closest to delta 0.25-0.35, 30-45 DTE, only if 100+ shares are held."""
        if shares_held < 100:
            return None
        today = date.today()
        chain = self.get_option_chain(
            symbol,
            today + timedelta(days=CANDIDATE_FETCH_DTE_MIN),
            today + timedelta(days=CANDIDATE_FETCH_DTE_MAX),
            "call",
        )
        return select_best_candidate(chain)

    def get_best_cash_secured_put_candidate(self, symbol: str, available_cash: float) -> Optional[dict]:
        """Closest to delta 0.25-0.35, 30-45 DTE, only among strikes cash can secure."""
        today = date.today()
        chain = self.get_option_chain(
            symbol,
            today + timedelta(days=CANDIDATE_FETCH_DTE_MIN),
            today + timedelta(days=CANDIDATE_FETCH_DTE_MAX),
            "put",
        )
        affordable = [c for c in chain if c["strike"] * 100 <= available_cash]
        return select_best_candidate(affordable)

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    def get_option_positions(self) -> list[dict]:
        positions = self.trading_client.get_all_positions()
        result = []
        for p in positions:
            if p.asset_class != AssetClass.US_OPTION:
                continue
            meta = parse_occ_symbol(p.symbol)
            result.append({
                "symbol": p.symbol,
                "underlying_symbol": meta["underlying_symbol"],
                "option_type": meta["type"],
                "strike": meta["strike"],
                "expiration": meta["expiration_date"].isoformat(),
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            })
        return result

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def submit_option_order(self, contract_symbol: str, qty: int, side: str, position_intent: str) -> dict:
        """
        In this app, always called with side="sell", position_intent="sell_to_open"
        (opening a short covered call or cash-secured put) -- the signature stays
        generic for testability. Alpaca's OrderRequest requires `side` even when
        `position_intent` is also set, so both must always be passed together.

        Routed through Alpaca's official CLI by default (EXECUTION_BACKEND=cli
        in .env) -- this is the live order-execution mechanism that satisfies
        the hackathon's CLI requirement. Set EXECUTION_BACKEND=sdk to fall back
        to the direct alpaca-py SDK call below.
        """
        if self.execution_backend == "cli":
            return alpaca_cli.submit_option_order(contract_symbol, qty, side, position_intent)

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        order_request = MarketOrderRequest(
            symbol=contract_symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            position_intent=PositionIntent(position_intent),
        )
        order = self.trading_client.submit_order(order_request)
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "qty": order.qty,
            "side": order.side.value,
            "status": order.status.value,
            "position_intent": order.position_intent.value if order.position_intent else None,
        }

    def close_option_position(self, contract_symbol: str) -> dict:
        if self.execution_backend == "cli":
            return alpaca_cli.close_option_position(contract_symbol)

        order = self.trading_client.close_position(contract_symbol)
        return {"symbol": contract_symbol, "status": "closed", "order_id": str(order.id)}
