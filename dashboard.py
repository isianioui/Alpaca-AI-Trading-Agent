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
from src.options_client import OptionsClient
from src.options_trading_agent import OptionsTradingAgent
from src.trading_agent import TradingAgent

load_dotenv()

st.set_page_config(page_title="Alpaca AI Trading Agent", page_icon="🦙", layout="wide")

st.title("🦙 Alpaca AI Trading Agent")
st.caption("An autonomous, Gemini-powered paper trading agent built on Alpaca's Trading API.")


@st.cache_resource
def get_client() -> AlpacaClient:
    return AlpacaClient()


@st.cache_resource
def get_options_client() -> OptionsClient:
    return OptionsClient()


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


def render_options_positions_panel(client: OptionsClient) -> None:
    positions = client.get_option_positions()
    st.subheader("🧾 Open Option Positions")
    if not positions:
        st.info("No open option positions.")
        return
    df = pd.DataFrame(positions)
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_options_decision_log() -> None:
    st.subheader("🧠 Options Decision Log")
    decisions = load_decisions()
    decisions = [d for d in decisions if d.get("asset_class") == "option"]
    if not decisions:
        st.info("No options decisions logged yet. Run `python main.py options-run` or "
                 "click 'Run options agent cycle now' above.")
        return

    decisions = list(reversed(decisions))  # newest first

    for d in decisions[:30]:
        if d.get("status") != "ok":
            continue
        action = d.get("llm_action", "hold").upper()
        color = {"OPEN_COVERED_CALL": "🟢", "OPEN_CASH_SECURED_PUT": "🟢",
                 "CLOSE_POSITION": "🔴", "HOLD": "⚪"}.get(action, "⚪")
        approved = "✅ Executed" if d.get("risk_approved") else "🚫 Blocked by risk manager"

        with st.expander(f"{color} {d['symbol']} — {action} ({d.get('contract_symbol') or '—'}) "
                          f"(confidence {d.get('confidence', 0):.2f}) · {approved} · "
                          f"{d.get('timestamp', '')[:19]}"):
            c1, c2 = st.columns([2, 1])
            with c1:
                st.markdown(f"**Reasoning:** {d.get('reasoning', '—')}")
                st.markdown(f"**Risk note:** {d.get('risk_note', '—')}")
                st.markdown(f"**Risk manager verdict:** {d.get('risk_reason', '—')}")
                if d.get("order"):
                    st.markdown(f"**Order placed:** `{d['order']}`")
            with c2:
                st.json({
                    "covered_call_candidate": d.get("covered_call_candidate"),
                    "cash_secured_put_candidate": d.get("cash_secured_put_candidate"),
                })
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
        options_client = get_options_client()
    except ValueError as e:
        st.error(str(e))
        st.info("Copy `.env.example` to `.env` and fill in your Alpaca + Google Gemini API keys.")
        return

    tab_stocks, tab_options = st.tabs(["📈 Stocks", "🧾 Options"])

    with tab_stocks:
        render_account_panel(client)
        st.divider()

        left, right = st.columns([1, 1])
        with left:
            render_positions_panel(client)
        with right:
            st.subheader("📈 Price Chart")
            symbol = st.text_input("Symbol", value="AAPL", key="stock_chart_symbol").upper()
            if symbol:
                render_price_chart(client, symbol)

        st.divider()
        render_decision_log()

    with tab_options:
        render_options_positions_panel(options_client)
        st.divider()

        st.subheader("⚙️ Run Options Agent")
        st.caption("Only two defined-risk strategies are ever proposed: covered call, cash-secured put.")
        options_watchlist_input = st.text_input("Options Watchlist", value="AAPL,MSFT,TSLA,NVDA,SPY",
                                                  key="options_watchlist_input")
        options_dry_run = st.checkbox("Dry run (decide only, no orders)", value=True, key="options_dry_run")
        if st.button("▶️ Run options agent cycle now", type="primary", key="options_run_button"):
            symbols = [s.strip().upper() for s in options_watchlist_input.split(",") if s.strip()]
            with st.spinner("Agent is analyzing option chains and consulting Gemini..."):
                options_agent = OptionsTradingAgent(
                    alpaca_client=client, options_client=options_client, dry_run=options_dry_run,
                )
                options_agent.run_cycle(symbols)
            st.success("Options cycle complete — see the decision log below.")
            st.rerun()

        st.divider()
        render_options_decision_log()

    with st.sidebar:
        st.header("⚙️ Run Agent (Stocks)")
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


if __name__ == "__main__":
    main()
