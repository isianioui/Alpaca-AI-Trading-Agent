"""
Thin wrapper around Alpaca's official Python SDK (alpaca-py).

Centralizes every call the agent makes to Alpaca's Trading API and
Market Data API so the rest of the codebase never talks to Alpaca
directly. Defaults to PAPER TRADING — no real money is ever at risk
unless ALPACA_PAPER is explicitly set to false in the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import GetPortfolioHistoryRequest, MarketOrderRequest

from src import alpaca_cli


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    last_equity: float
    created_at: Optional[datetime] = None
    account_number: Optional[str] = None
    status: Optional[str] = None
    currency: Optional[str] = None
    long_market_value: Optional[float] = None
    short_market_value: Optional[float] = None
    maintenance_margin: Optional[float] = None
    options_buying_power: Optional[float] = None
    options_trading_level: Optional[int] = None
    daytrade_count: Optional[int] = None
    pattern_day_trader: Optional[bool] = None

    @property
    def daily_pnl_pct(self) -> float:
        if self.last_equity == 0:
            return 0.0
        return (self.equity - self.last_equity) / self.last_equity


class AlpacaClient:
    """Wraps Alpaca's TradingClient + StockHistoricalDataClient."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
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

        self.trading_client = TradingClient(self.api_key, self.secret_key, paper=self.paper)
        self.data_client = StockHistoricalDataClient(self.api_key, self.secret_key)

    # ------------------------------------------------------------------ #
    # Account / portfolio
    # ------------------------------------------------------------------ #
    def get_account(self) -> AccountSnapshot:
        acct = self.trading_client.get_account()
        return AccountSnapshot(
            equity=float(acct.equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            portfolio_value=float(acct.portfolio_value),
            last_equity=float(acct.last_equity),
            created_at=acct.created_at,
            account_number=acct.account_number,
            status=acct.status.value if acct.status else None,
            currency=acct.currency,
            long_market_value=float(acct.long_market_value) if acct.long_market_value is not None else None,
            short_market_value=float(acct.short_market_value) if acct.short_market_value is not None else None,
            maintenance_margin=float(acct.maintenance_margin) if acct.maintenance_margin is not None else None,
            options_buying_power=(
                float(acct.options_buying_power) if acct.options_buying_power is not None else None
            ),
            options_trading_level=acct.options_trading_level,
            daytrade_count=acct.daytrade_count,
            pattern_day_trader=acct.pattern_day_trader,
        )

    def get_portfolio_history(self, period: str = "1M", timeframe: Optional[str] = None) -> pd.DataFrame:
        """Real equity-over-time series from Alpaca's own portfolio history
        endpoint. Alpaca pads the response with equity=0 rows for any part
        of the requested period that predates the account's existence/first
        funding -- those are dropped rather than plotted as a fake crash
        to zero."""
        request = GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
        hist = self.trading_client.get_portfolio_history(request)
        if not hist.timestamp:
            return pd.DataFrame(columns=["timestamp", "equity", "profit_loss", "profit_loss_pct"])
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(hist.timestamp, unit="s", utc=True),
            "equity": hist.equity,
            "profit_loss": hist.profit_loss,
            "profit_loss_pct": hist.profit_loss_pct,
        })
        df = df[df["equity"] > 0].reset_index(drop=True)
        return df

    def get_positions(self) -> list[dict]:
        positions = self.trading_client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
        ]

    def get_open_orders(self) -> list[dict]:
        orders = self.trading_client.get_orders()
        return [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "qty": o.qty,
                "side": o.side.value,
                "status": o.status.value,
                "submitted_at": str(o.submitted_at),
            }
            for o in orders
        ]

    # ------------------------------------------------------------------ #
    # Market data
    # ------------------------------------------------------------------ #
    def get_bars(self, symbol: str, lookback_days: int = 60, timeframe: TimeFrame = TimeFrame.Day) -> pd.DataFrame:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=datetime.now() - timedelta(days=lookback_days),
        )
        bars = self.data_client.get_stock_bars(request)
        df = bars.df
        if df.empty:
            return df
        # multi-index (symbol, timestamp) -> flatten to plain timestamp index
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        return df

    def get_latest_quote(self, symbol: str) -> dict:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = self.data_client.get_stock_latest_quote(request)[symbol]
        return {
            "symbol": symbol,
            "bid": float(quote.bid_price),
            "ask": float(quote.ask_price),
            "timestamp": str(quote.timestamp),
        }

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def submit_market_order(self, symbol: str, qty: float, side: str) -> dict:
        """side must be 'buy' or 'sell'.

        Routed through Alpaca's official CLI by default (EXECUTION_BACKEND=cli
        in .env) -- this is the live order-execution mechanism that satisfies
        the hackathon's CLI requirement. Set EXECUTION_BACKEND=sdk to fall back
        to the direct alpaca-py SDK call below.
        """
        if self.execution_backend == "cli":
            return alpaca_cli.submit_market_order(symbol, qty, side)

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        order_request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        order = self.trading_client.submit_order(order_request)
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "qty": order.qty,
            "side": order.side.value,
            "status": order.status.value,
        }

    def close_position(self, symbol: str) -> dict:
        if self.execution_backend == "cli":
            return alpaca_cli.close_position(symbol)

        order = self.trading_client.close_position(symbol)
        return {"symbol": symbol, "status": "closed", "order_id": str(order.id)}
