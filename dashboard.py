"""
Streamlit dashboard for the Alpaca AI Trading Agent.

Three panels:
  1. Account overview (equity, cash, daily P&L)
  2. Open positions table
  3. Decision log — every symbol the agent looked at, what Gemini
     decided, why, and whether the risk manager approved it. This is
     the "explainability" view that makes the agent's behavior legible
     to a judge watching the demo instead of a black box.

Run with:  streamlit run dashboard.py
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from src.alpaca_client import AlpacaClient
from src.logger import load_decisions
from src.trading_agent import TradingAgent

load_dotenv()

st.set_page_config(page_title="Alpaca AI Trading Agent", page_icon="🦙", layout="wide")

st.title("🦙 Alpaca AI Trading Agent")
st.caption("An autonomous, Gemini-powered paper trading agent built on Alpaca's Trading API.")


@st.cache_resource
def get_client() -> AlpacaClient:
    return AlpacaClient()


def render_account_panel(client: AlpacaClient) -> None:
    account = client.get_account()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Equity", f"${account.equity:,.2f}")
    col2.metric("Cash", f"${account.cash:,.2f}")
    col3.metric("Buying Power", f"${account.buying_power:,.2f}")
    col4.metric("Daily P&L", f"{account.daily_pnl_pct:.2%}",
                delta=f"{account.daily_pnl_pct:.2%}")


def render_positions_panel(client: AlpacaClient) -> None:
    positions = client.get_positions()
    st.subheader("📊 Open Positions")
    if not positions:
        st.info("No open positions.")
        return
    df = pd.DataFrame(positions)
    df["unrealized_plpc"] = (df["unrealized_plpc"] * 100).round(2)
    df = df.rename(columns={
        "symbol": "Symbol", "qty": "Qty", "avg_entry_price": "Entry Price",
        "current_price": "Current Price", "market_value": "Market Value",
        "unrealized_pl": "Unrealized P&L ($)", "unrealized_plpc": "Unrealized P&L (%)",
    })
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_decision_log() -> None:
    st.subheader("🧠 Agent Decision Log")
    decisions = load_decisions()
    if not decisions:
        st.info("No decisions logged yet. Run `python main.py run` or click 'Run agent cycle now' below.")
        return

    decisions = list(reversed(decisions))  # newest first

    for d in decisions[:30]:
        if d.get("status") != "ok":
            continue
        action = d.get("llm_action", "hold").upper()
        color = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(action, "⚪")
        approved = "✅ Executed" if d.get("risk_approved") else "🚫 Blocked by risk manager"

        with st.expander(f"{color} {d['symbol']} — {action} (confidence {d.get('confidence', 0):.2f}) "
                          f"· {approved} · {d.get('timestamp', '')[:19]}"):
            c1, c2 = st.columns([2, 1])
            with c1:
                st.markdown(f"**Reasoning:** {d.get('reasoning', '—')}")
                st.markdown(f"**Risk note:** {d.get('risk_note', '—')}")
                st.markdown(f"**Risk manager verdict:** {d.get('risk_reason', '—')}")
                if d.get("order"):
                    st.markdown(f"**Order placed:** `{d['order']}`")
            with c2:
                st.json(d.get("indicators", {}))


def render_price_chart(client: AlpacaClient, symbol: str) -> None:
    bars = client.get_bars(symbol, lookback_days=90)
    if bars.empty:
        st.warning(f"No bar data for {symbol}.")
        return
    fig = go.Figure(data=[go.Candlestick(
        x=bars.index, open=bars["open"], high=bars["high"],
        low=bars["low"], close=bars["close"],
    )])
    fig.update_layout(title=f"{symbol} — last 90 days", height=400,
                       xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    try:
        client = get_client()
    except ValueError as e:
        st.error(str(e))
        st.info("Copy `.env.example` to `.env` and fill in your Alpaca + Google Gemini API keys.")
        return

    render_account_panel(client)
    st.divider()

    left, right = st.columns([1, 1])
    with left:
        render_positions_panel(client)
    with right:
        st.subheader("📈 Price Chart")
        symbol = st.text_input("Symbol", value="AAPL").upper()
        if symbol:
            render_price_chart(client, symbol)

    st.divider()

    with st.sidebar:
        st.header("⚙️ Run Agent")
        watchlist_input = st.text_input("Watchlist", value="AAPL,MSFT,TSLA,NVDA,SPY")
        dry_run = st.checkbox("Dry run (decide only, no orders)", value=True)
        if st.button("▶️ Run agent cycle now", type="primary"):
            symbols = [s.strip().upper() for s in watchlist_input.split(",") if s.strip()]
            with st.spinner("Agent is analyzing the market and consulting Gemini..."):
                agent = TradingAgent(alpaca_client=client, dry_run=dry_run)
                agent.run_cycle(symbols)
            st.success("Cycle complete — see the decision log below.")
            st.rerun()

        st.divider()
        st.caption("Built for the Alpaca AI Trading Agents Hackathon (lablab.ai). "
                   "Runs on Alpaca paper trading — no real money at risk.")

    render_decision_log()


if __name__ == "__main__":
    main()
