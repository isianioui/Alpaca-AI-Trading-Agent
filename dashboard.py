"""
Streamlit dashboard for the Alpaca AI Trading Agent.

Presentation layer: pure data-display blocks (stat cards, position cards,
decision cards, narrative card, "How It Decides", footer, status strip) are
hand-authored HTML/CSS/JS rendered via st.components.v1.html (src/dashboard_ui.py
builds the markup) -- real sandboxed iframes, not CSS overrides on Streamlit's
own widget classes. Anything that has to call back into Python (nav switching,
run/dry-run/watchlist controls, filters) stays a native Streamlit widget,
heavily restyled via page-level CSS so it shows none of Streamlit's default
chrome. See src/dashboard_ui.py's module docstring for why the split has to
be drawn this way (iframe JS can't call back into Python).

Single-page app with sidebar view-switching (st.session_state["active_view"]),
not Streamlit's separate pages/ feature, so the sidebar can host both the nav
and the "Run Agent" controls together.

Run with:  streamlit run dashboard.py
"""

from __future__ import annotations

import dataclasses
import html
import json
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src import backtest_engine
from src import dashboard_ui as ui
from src import payoff as payoff_calc
from src import trade_history
from src.alpaca_client import AlpacaClient
from src.cycle_summary import compute_activity_counters, summarize_latest_cycle
from src.exit_engine import ExitLimits, run_exit_engine
from src.logger import load_decisions
from src.options_client import OptionsClient
from src.options_trading_agent import OptionsTradingAgent
from src.risk_gates import compute_gate_stats
from src.risk_manager import OptionsRiskLimits, RiskLimits
from src.trading_agent import TradingAgent

load_dotenv()

st.set_page_config(page_title="QASIX AI Trading Agent", page_icon="◆", layout="wide")

NAVY, NAVY_MID, ICE, GREEN, RED, OFFWHITE, MUTED_ON_DARK, ACCENT, ACCENT2, GRADIENT = (
    ui.NAVY, ui.NAVY_MID, ui.ICE, ui.GREEN, ui.RED, ui.OFFWHITE, ui.MUTED_ON_DARK,
    ui.ACCENT, ui.ACCENT2, ui.GRADIENT,
)

NAV_GROUPS = [
    ("LIVE", [("Overview", "🏠"), ("Positions", "📊"), ("Options", "🧾")]),
    ("RECORDS", [("Decisions", "🧠"), ("Performance", "📈"), ("Backtest", "🧪")]),
    ("ANALYSIS", [("Risk Gates", "🚦"), ("Methodology", "🧭")]),
    ("ABOUT", [("Docs", "📄"), ("Settings", "⚙️")]),
]


def embed(payload: tuple[str, int]) -> None:
    """Renders a (html, height) pair from dashboard_ui through a real
    sandboxed iframe. scrolling=False everywhere -- every dashboard_ui
    renderer sizes its own iframe (capping long lists into an internally
    scrolling, custom-scrollbar panel instead) so the outer iframe never
    needs its own separate scrollbar."""
    html_str, height = payload
    components.html(html_str, height=height, scrolling=False)


def inject_css() -> None:
    """Page-level CSS: loads the brand fonts for the main document (the
    iframinia components load their own copies independently -- iframes are
    separate documents), paints the grid-motif background, and aggressively
    restyles every native Streamlit widget that has to stay native (nav
    buttons, run controls, checkboxes, text inputs, selectboxes, radio)
    so none of Streamlit's default chrome shows through."""
    st.markdown(
        """
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <style>
        html, body, [class*="css"] {{ font-family: 'Inter', -apple-system, sans-serif; }}

        @keyframes fadeInUp {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        @keyframes shimmerText {{
            0%, 100% {{ background-position: 0% center; }}
            50% {{ background-position: 100% center; }}
        }}
        @keyframes navItemIn {{
            from {{ opacity: 0; transform: translateX(-10px); }}
            to {{ opacity: 1; transform: translateX(0); }}
        }}
        @keyframes brandGlow {{
            0%, 100% {{ opacity: 0.55; }}
            50% {{ opacity: 1; }}
        }}

        .stApp {{
            background-color: {NAVY};
            background-image:
                radial-gradient(circle at 12% 8%, rgba(124, 111, 240, 0.16) 0%, transparent 42%),
                radial-gradient(circle at 88% 18%, rgba(34, 211, 238, 0.11) 0%, transparent 40%),
                radial-gradient(circle at 50% 96%, rgba(124, 111, 240, 0.09) 0%, transparent 50%),
                linear-gradient(rgba(169, 180, 245, 0.035) 1px, transparent 1px),
                linear-gradient(90deg, rgba(169, 180, 245, 0.035) 1px, transparent 1px);
            background-size: auto, auto, auto, 26px 26px, 26px 26px;
            background-attachment: fixed;
        }}
        #MainMenu {{visibility: hidden;}}
        footer {{visibility: hidden;}}
        header[data-testid="stHeader"] {{ background: transparent; }}
        iframe {{ border: none !important; }}

        /* ---- Header ---- */
        .app-title {{
            font-size: 2.25rem; font-weight: 800; margin-bottom: 0.15rem; letter-spacing: -0.02em;
            display: inline-block;
            background: {GRADIENT};
            background-size: 200% auto;
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent; color: transparent;
            animation: shimmerText 7s ease-in-out infinite;
        }}
        .app-subtitle {{ font-size: 0.95rem; color: {ICE}; opacity: 0.85; margin-bottom: 1.2rem; }}
        .page-title {{
            font-size: 1.55rem; font-weight: 800; color: {OFFWHITE}; margin-bottom: 0.35rem;
            letter-spacing: -0.01em; position: relative; display: inline-block;
            padding-bottom: 0.4rem; animation: fadeInUp 0.4s ease-out both;
        }}
        .page-title::after {{
            content: ''; position: absolute; left: 0; bottom: 0; height: 3px; width: 46px;
            border-radius: 3px; background: {GRADIENT};
        }}
        .page-subtitle {{ font-size: 0.85rem; color: {MUTED_ON_DARK}; margin-bottom: 1rem; animation: fadeInUp 0.45s ease-out both; }}
        .section-title {{ font-weight: 700; color: {OFFWHITE}; margin: 0.2rem 0 0.6rem 0; }}
        .section-title.lg {{ font-size: 1.3rem; }}
        .section-title.md {{ font-size: 1.05rem; }}
        .section-title.sm {{ font-size: 0.9rem; color: {MUTED_ON_DARK}; text-transform: uppercase; letter-spacing: 0.04em; }}
        .section-divider {{ border: none; border-top: 1px solid rgba(124, 111, 240, 0.16); margin: 1.4rem 0; }}
        .kv-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 0.6rem; margin: 0.6rem 0 1rem 0; }}
        .kv-row {{
            background: rgba(20, 26, 52, 0.6); border: 1px solid rgba(124, 111, 240, 0.14);
            border-radius: 12px; padding: 0.6rem 0.9rem; transition: border-color 0.2s ease, transform 0.2s ease;
        }}
        .kv-row:hover {{ border-color: rgba(124, 111, 240, 0.35); transform: translateY(-1px); }}
        .kv-key {{ font-size: 0.66rem; color: {MUTED_ON_DARK}; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 2px; }}
        .kv-value {{ font-size: 0.92rem; font-weight: 700; color: {OFFWHITE}; font-family: 'JetBrains Mono', monospace; }}

        /* ---- Sidebar brand ---- */
        .brand-block {{ padding: 0.4rem 0.2rem 1rem 0.2rem; margin-bottom: 0.4rem; border-bottom: 1px solid rgba(124,111,240,0.14); }}
        .brand-logo-row {{ display: flex; align-items: center; gap: 0.6rem; }}
        .brand-mark {{
            width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            background: {GRADIENT}; color: #0A0E1E; font-weight: 800; font-size: 1.05rem;
            box-shadow: 0 0 14px rgba(124,111,240,0.55);
            animation: brandGlow 3.2s ease-in-out infinite;
        }}
        .brand-name {{
            font-size: 1.05rem; font-weight: 800; letter-spacing: -0.01em;
            background: {GRADIENT}; background-size: 200% auto;
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent; color: transparent;
            animation: shimmerText 7s ease-in-out infinite;
        }}
        .brand-tag {{ font-size: 0.68rem; color: {MUTED_ON_DARK}; margin-top: 2px; letter-spacing: 0.03em; }}

        /* ================= Native widgets, fully re-skinned ================= */

        /* Buttons -- no default Streamlit shape/shadow/focus ring anywhere */
        div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {{
            border-radius: 11px !important;
            border: 1px solid rgba(124, 111, 240, 0.22) !important;
            background: rgba(20, 26, 52, 0.6) !important;
            color: {OFFWHITE} !important;
            font-weight: 600 !important;
            box-shadow: none !important;
            transition: background 0.18s ease, border-color 0.18s ease, transform 0.15s ease, box-shadow 0.18s ease !important;
        }}
        div[data-testid="stButton"] button:hover {{
            border-color: rgba(124, 111, 240, 0.5) !important;
            background: rgba(30, 36, 68, 0.9) !important;
            transform: translateY(-2px);
            box-shadow: 0 6px 18px rgba(124, 111, 240, 0.18) !important;
        }}
        div[data-testid="stButton"] button:active {{ transform: translateY(0); }}
        div[data-testid="stButton"] button:focus {{ box-shadow: 0 0 0 2px rgba(34, 211, 238, 0.35) !important; }}
        div[data-testid="stButton"] button[kind="primary"] {{
            background: {GRADIENT} !important; border: none !important; color: #0A0E1E !important;
            font-weight: 700 !important; box-shadow: 0 4px 16px rgba(124, 111, 240, 0.35) !important;
        }}
        div[data-testid="stButton"] button[kind="primary"]:hover {{
            filter: brightness(1.1); transform: translateY(-2px);
            box-shadow: 0 6px 22px rgba(124, 111, 240, 0.5) !important;
        }}

        /* Sidebar nav rows */
        section[data-testid="stSidebar"] {{
            background: linear-gradient(180deg, rgba(16, 21, 42, 0.98) 0%, rgba(9, 12, 24, 0.98) 100%) !important;
            border-right: 1px solid rgba(124, 111, 240, 0.14);
            box-shadow: 6px 0 26px rgba(0, 0, 0, 0.28);
        }}
        section[data-testid="stSidebar"] div[data-testid="stButton"] button {{
            width: 100%; text-align: left; justify-content: flex-start;
            padding: 0.6rem 0.9rem !important; font-size: 0.92rem !important;
            background: transparent !important; border: 1px solid transparent !important;
            border-left: 3px solid transparent !important; border-radius: 10px !important;
            box-shadow: none !important;
            opacity: 0; animation: navItemIn 0.4s ease-out forwards;
            transition: background 0.2s ease, border-color 0.2s ease, transform 0.2s ease !important;
        }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(1) button {{ animation-delay: 0.03s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(2) button {{ animation-delay: 0.07s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(3) button {{ animation-delay: 0.11s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(4) button {{ animation-delay: 0.15s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(5) button {{ animation-delay: 0.19s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(6) button {{ animation-delay: 0.23s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(7) button {{ animation-delay: 0.27s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(8) button {{ animation-delay: 0.31s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"]:nth-of-type(n+9) button {{ animation-delay: 0.34s; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"] button p {{ text-align: left; transition: transform 0.2s ease; }}
        section[data-testid="stSidebar"] div[data-testid="stButton"] button:hover {{
            background: rgba(124, 111, 240, 0.09) !important;
            border-left-color: rgba(124, 111, 240, 0.45) !important;
            transform: translateX(3px);
        }}
        section[data-testid="stSidebar"] div[data-testid="stButton"] button:hover p {{ transform: translateX(2px); }}
        section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"] {{
            background: linear-gradient(90deg, rgba(124,111,240,0.18) 0%, rgba(34,211,238,0.08) 100%) !important;
            border-left: 3px solid {ACCENT2} !important;
            color: {OFFWHITE} !important; font-weight: 700 !important;
            box-shadow: inset 0 0 0 1px rgba(124,111,240,0.14), 0 0 16px rgba(124,111,240,0.18) !important;
        }}
        .nav-group-label {{
            font-size: 0.64rem; font-weight: 800; color: {MUTED_ON_DARK};
            text-transform: uppercase; letter-spacing: 0.1em; margin: 1.1rem 0 0.4rem 0.2rem;
            display: flex; align-items: center; gap: 6px;
        }}
        .nav-group-label::before {{
            content: ''; width: 4px; height: 4px; border-radius: 50%; background: {GRADIENT}; flex-shrink: 0;
        }}
        .nav-group-label.first {{ margin-top: 0.1rem; }}

        /* Text inputs */
        div[data-testid="stTextInput"] input {{
            background: rgba(16, 21, 42, 0.7) !important;
            border: 1px solid rgba(124, 111, 240, 0.24) !important;
            border-radius: 10px !important; color: {OFFWHITE} !important;
            font-family: 'JetBrains Mono', monospace !important;
            transition: border-color 0.18s ease, box-shadow 0.18s ease !important;
        }}
        div[data-testid="stTextInput"] input:focus {{
            border-color: {ACCENT2} !important; box-shadow: 0 0 0 2px rgba(34, 211, 238, 0.25) !important;
        }}
        div[data-testid="stTextInput"] label, div[data-testid="stCheckbox"] label p,
        div[data-testid="stSelectbox"] label, div[data-testid="stRadio"] label {{
            color: {ICE} !important; font-size: 0.85rem !important; font-weight: 500 !important;
        }}

        /* Checkboxes */
        div[data-testid="stCheckbox"] label span[data-testid="stMarkdownContainer"] {{ color: {ICE}; }}

        /* Selectbox (filters) */
        div[data-testid="stSelectbox"] > div > div {{
            background: rgba(16, 21, 42, 0.7) !important;
            border: 1px solid rgba(124, 111, 240, 0.24) !important;
            border-radius: 10px !important; color: {OFFWHITE} !important;
            transition: border-color 0.18s ease !important;
        }}
        div[data-testid="stSelectbox"] > div > div:hover {{ border-color: rgba(124, 111, 240, 0.45) !important; }}

        /* Radio -> segmented-control look (equity curve range picker) */
        div[data-testid="stRadio"] > div {{
            background: rgba(16, 21, 42, 0.55); border-radius: 11px; padding: 3px;
            border: 1px solid rgba(124, 111, 240, 0.18); display: inline-flex; gap: 2px;
        }}
        div[data-testid="stRadio"] label {{
            border-radius: 8px !important; padding: 0.25rem 0.7rem !important; margin: 0 !important;
            transition: background 0.18s ease !important;
        }}
        div[data-testid="stRadio"] label:has(input:checked) {{ background: {GRADIENT}; }}
        div[data-testid="stRadio"] label:has(input:checked) p {{ color: #0A0E1E !important; font-weight: 700 !important; }}
        div[data-testid="stRadio"] input {{ display: none; }}

        /* Divider */
        div[data-testid="stSidebar"] hr {{ border-color: rgba(124, 111, 240, 0.16); }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="page-title">{html.escape(title)}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="page-subtitle">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


def section_title(text: str, icon: str = "", level: str = "md") -> None:
    label = f"{icon} {text}".strip()
    st.markdown(f'<div class="section-title {level}">{html.escape(label)}</div>', unsafe_allow_html=True)


def section_divider() -> None:
    st.markdown('<hr class="section-divider" />', unsafe_allow_html=True)


def kv_grid(pairs: list[tuple[str, str]]) -> None:
    rows = "".join(
        f'<div class="kv-row"><div class="kv-key">{html.escape(k)}</div>'
        f'<div class="kv-value">{html.escape(v)}</div></div>'
        for k, v in pairs
    )
    st.markdown(f'<div class="kv-grid">{rows}</div>', unsafe_allow_html=True)


@st.cache_resource
def get_client() -> AlpacaClient:
    return AlpacaClient()


@st.cache_resource
def get_options_client() -> OptionsClient:
    return OptionsClient()


# --------------------------------------------------------------------------- #
# Data -> card-ready dict helpers (dashboard.py owns data-fetching/shaping;
# src/dashboard_ui.py owns turning already-shaped dicts into HTML).
# --------------------------------------------------------------------------- #
def find_opening_decision(symbol: str, contract_symbol: str | None = None) -> dict | None:
    """Best-effort lookup of the decision record that opened this position --
    used for the position card's 'view reasoning' affordance. No real routing,
    just a match on symbol/contract against the most recent opening order in
    the decision log (mirrors trade_history._find_entry_timestamp's logic)."""
    match_key = contract_symbol or symbol
    for record in reversed(load_decisions()):
        if record.get("status") != "ok" or not record.get("order"):
            continue
        action = (record.get("llm_action") or "").lower()
        if action in ("sell", "close_position") or record.get("trigger") == "exit_engine":
            continue
        record_key = record.get("contract_symbol") or record.get("symbol")
        if record_key == match_key:
            return record
    return None


def _prep_stock_position(p: dict) -> dict:
    opening = find_opening_decision(p["symbol"])
    return {
        "symbol": p["symbol"],
        "qty": p["qty"],
        "entry": p["avg_entry_price"],
        "current": p["current_price"],
        "market_value": p["market_value"],
        "upl": p["unrealized_pl"],
        "uplpc": p["unrealized_plpc"],
        "opened": opening.get("timestamp", "")[:10] if opening else None,
        "reasoning": opening.get("reasoning") if opening else None,
        "confidence": opening.get("confidence", 0) if opening else 0,
        "risk_note": opening.get("risk_note") if opening else None,
    }


def _prep_option_row(p: dict, stock_positions_by_symbol: dict, client: AlpacaClient) -> dict:
    """Shapes one open option position into everything the dense positions
    table needs -- payoff math comes from src/payoff.py's pure functions
    (unit-tested in tests/test_payoff.py), fed with the exact same strike
    and contract count the OptionsRiskManager used to gate this trade at
    entry, plus the position's actual live avg_entry_price (premium) --
    never a new/invented risk number. Missing live data (an underlying
    quote, or a covered call's stock cost basis) degrades to an honest
    'pending' state in the row rather than a fabricated figure."""
    underlying = p["underlying_symbol"]
    option_type = p["option_type"]
    strategy = "covered_call" if option_type == "call" else "cash_secured_put"
    strategy_label = "Covered Call" if option_type == "call" else "Cash-Secured Put"
    note = (
        f"wins if price stays below ${p['strike']:,.2f}" if option_type == "call"
        else f"wins if price stays above ${p['strike']:,.2f}"
    )

    opening = find_opening_decision(underlying, contract_symbol=p["symbol"])
    stock_pos = stock_positions_by_symbol.get(underlying)

    current_price = None
    if stock_pos is not None:
        current_price = stock_pos["current_price"]
    else:
        try:
            quote = client.get_latest_quote(underlying)
            current_price = (quote["bid"] + quote["ask"]) / 2
        except Exception:
            current_price = None

    exp_date = date.fromisoformat(p["expiration"])
    dte = (exp_date - date.today()).days

    premium = p["avg_entry_price"]
    contracts = abs(p["qty"])
    collected = premium * 100 * contracts

    cost_basis = stock_pos["avg_entry_price"] if (strategy == "covered_call" and stock_pos is not None) else None
    can_compute_payoff = current_price is not None and (strategy == "cash_secured_put" or cost_basis is not None)

    row = {
        "symbol": underlying,
        "contract_symbol": p["symbol"],
        "status_label": "FILLED",
        "strategy_label": strategy_label,
        "note": note,
        "strike": p["strike"],
        "option_type": option_type,
        "dte": dte,
        "expiration": p["expiration"],
        "collected": collected,
        "unrealized": p["unrealized_pl"],
        "reasoning": opening.get("reasoning") if opening else None,
        "confidence": opening.get("confidence", 0) if opening else 0,
        "risk_note": opening.get("risk_note") if opening else None,
        "current_price": current_price,
        "room_dollar": None,
        "room_pct": None,
        "max_loss": None,
        "breakeven": None,
        "payoff_points": None,
    }

    if current_price is not None:
        safe = current_price < p["strike"] if option_type == "call" else current_price > p["strike"]
        distance = abs(current_price - p["strike"])
        row["room_dollar"] = distance if safe else -distance
        row["room_pct"] = (row["room_dollar"] / p["strike"]) if p["strike"] else 0.0

    if can_compute_payoff:
        row["max_loss"] = payoff_calc.max_loss(strategy, p["strike"], premium, contracts, cost_basis=cost_basis)
        row["breakeven"] = payoff_calc.breakeven(strategy, p["strike"], premium, cost_basis=cost_basis)
        row["payoff_points"] = payoff_calc.payoff_points(
            strategy, p["strike"], premium, contracts, cost_basis=cost_basis)

    return row


def _prep_decision_record(d: dict) -> dict:
    action = (d.get("llm_action", "hold") or "hold").upper()
    risk_approved = bool(d.get("risk_approved"))
    dry_run = bool(d.get("dry_run"))
    order_placed = d.get("order") is not None

    if order_placed:
        status_class, status_label = "executed", "EXECUTED"
    elif risk_approved and dry_run:
        status_class, status_label = "approved-dry", "APPROVED (DRY RUN — NO ORDER PLACED)"
    elif risk_approved:
        status_class, status_label = "approved-dry", "APPROVED (NO ORDER PLACED)"
    else:
        status_class, status_label = "blocked", "BLOCKED BY RISK MANAGER"

    contract = d.get("contract_symbol")
    contract_suffix = f" · {contract}" if contract else ""

    if d.get("trigger") == "exit_engine":
        meta_line = "Deterministic exit-engine rule (no LLM call)"
    else:
        meta_line = f"Confidence: {d.get('confidence', 0) or 0:.2f}"

    reasoning = str(d.get("reasoning") or "—")
    reasoning_short = reasoning if len(reasoning) <= 220 else reasoning[:217] + "…"

    candidates = {}
    if d.get("covered_call_candidate"):
        candidates["open_covered_call"] = d["covered_call_candidate"]
    if d.get("cash_secured_put_candidate"):
        candidates["open_cash_secured_put"] = d["cash_secured_put_candidate"]

    return {
        "status_class": status_class, "status_label": status_label,
        "symbol": d.get("symbol", ""), "action": action, "contract_suffix": contract_suffix,
        "timestamp": str(d.get("timestamp", ""))[:19], "meta_line": meta_line,
        "reasoning_short": reasoning_short, "reasoning_full": d.get("reasoning", "—"),
        "risk_note": d.get("risk_note", "—"), "risk_reason": d.get("risk_reason", "—"),
        "order_str": str(d["order"]) if d.get("order") else None,
        "indicators": d.get("indicators"),
        "candidates": candidates,
    }


# --------------------------------------------------------------------------- #
# Shared building blocks
# --------------------------------------------------------------------------- #
def render_account_panel(client: AlpacaClient) -> None:
    account = client.get_account()
    pnl = account.daily_pnl_pct
    cards = [
        {"label": "Equity", "display": f"${account.equity:,.2f}", "raw": account.equity, "format": "currency", "tone": "neutral"},
        {"label": "Cash", "display": f"${account.cash:,.2f}", "raw": account.cash, "format": "currency", "tone": "neutral"},
        {"label": "Buying Power", "display": f"${account.buying_power:,.2f}", "raw": account.buying_power, "format": "currency", "tone": "neutral"},
        {"label": "Daily P&L", "display": f"{pnl:+.2%}", "raw": pnl * 100, "format": "percent",
         "tone": "positive" if pnl >= 0 else "negative"},
    ]
    embed(ui.render_stat_cards(cards))

    with st.expander("Account details"):
        pairs = [("Account Number", account.account_number), ("Status", account.status),
                 ("Currency", account.currency), ("Portfolio Value", f"${account.portfolio_value:,.2f}")]
        if account.long_market_value is not None:
            pairs.append(("Long Market Value", f"${account.long_market_value:,.2f}"))
        if account.short_market_value is not None:
            pairs.append(("Short Market Value", f"${account.short_market_value:,.2f}"))
        if account.maintenance_margin is not None:
            pairs.append(("Maintenance Margin", f"${account.maintenance_margin:,.2f}"))
        if account.options_buying_power is not None:
            pairs.append(("Options Buying Power", f"${account.options_buying_power:,.2f}"))
        if account.options_trading_level is not None:
            pairs.append(("Options Trading Level", str(account.options_trading_level)))
        if account.daytrade_count is not None:
            pairs.append(("Day Trade Count", str(account.daytrade_count)))
        if account.pattern_day_trader is not None:
            pairs.append(("Pattern Day Trader", "Yes" if account.pattern_day_trader else "No"))
        if account.created_at is not None:
            pairs.append(("Account Created", account.created_at.strftime("%Y-%m-%d")))
        kv_grid([(k, str(v)) for k, v in pairs if v is not None and v != ""])


def render_status_strip(client: AlpacaClient) -> None:
    try:
        client.get_account()
        api_ok = True
    except Exception:
        api_ok = False
    try:
        client.get_latest_quote("SPY")
        data_ok = True
    except Exception:
        data_ok = False
    try:
        market_open = client.get_market_clock()["is_open"]
    except Exception:
        market_open = None

    embed(ui.render_status_strip(api_ok, data_ok, market_open, client.execution_backend, client.paper))


def render_performance_panel() -> None:
    trades = trade_history.load_trade_history()
    if not trades:
        embed(ui.render_inline_alert(
            "No closed trades yet — performance will populate once a position closes "
            "(via an LLM sell/close decision or the Exit Engine's stop-loss / take-profit rules)."
        ))
        return

    stats = trade_history.compute_performance_stats(trades)
    win_rate_str = f"{stats['win_rate']:.0%}" if stats["win_rate"] is not None else "—"
    cards = [
        {"label": "Total Realized P&L", "display": f"${stats['total_realized_pnl']:,.2f}",
         "raw": stats["total_realized_pnl"], "format": "currency",
         "tone": "positive" if stats["total_realized_pnl"] >= 0 else "negative"},
        {"label": "Win Rate", "display": win_rate_str,
         "raw": (stats["win_rate"] * 100 if stats["win_rate"] is not None else None), "format": "integer", "tone": "neutral"},
        {"label": "Winners / Losers", "display": f"{stats['winning_trades']} / {stats['losing_trades']}",
         "raw": None, "format": "plain", "tone": "neutral"},
        {"label": "Avg P&L / Trade", "display": f"${stats['avg_pnl']:,.2f}", "raw": stats["avg_pnl"],
         "format": "currency", "tone": "positive" if stats["avg_pnl"] >= 0 else "negative"},
    ]
    embed(ui.render_stat_cards(cards))

    section_divider()
    section_title("Trade History", "📜", level="md")
    df = pd.DataFrame(list(reversed(trades)))  # newest first
    df["realized_pnl_pct"] = (df["realized_pnl_pct"] * 100).round(2)
    df = df.rename(columns={
        "symbol": "Symbol", "contract_symbol": "Contract", "asset_class": "Asset Class",
        "qty": "Qty", "entry_price": "Entry Price", "exit_price": "Exit Price",
        "realized_pnl": "Realized P&L ($)", "realized_pnl_pct": "Realized P&L (%)",
        "reason": "Reason", "trigger": "Trigger", "entry_time": "Entry Time", "exit_time": "Exit Time",
    })
    st.caption("Raw trade log (secondary detail view):")
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_narrative_card() -> None:
    decisions = load_decisions()
    summary = summarize_latest_cycle(decisions)
    if summary is None:
        embed(ui.render_narrative_card(None, None, None))
        return
    kind_label = {"stock": "Stocks", "option": "Options", "exit_engine": "Exit Engine"}.get(summary.kind, summary.kind)
    embed(ui.render_narrative_card(kind_label, summary.timestamp[:19], summary.narrative))


def render_activity_counters(client: AlpacaClient, open_positions_count: int) -> None:
    decisions = load_decisions()
    trades = trade_history.load_trade_history()
    try:
        created_at = client.get_account().created_at
    except Exception:
        created_at = None
    counters = compute_activity_counters(decisions, trades, open_positions_count, created_at)

    chips = [
        {"label": "Days Live", "display": "—" if counters.days_live is None else str(counters.days_live),
         "raw": counters.days_live, "format": "integer"},
        {"label": "Cycles Run", "display": str(counters.cycles_run), "raw": counters.cycles_run, "format": "integer"},
        {"label": "Trades Filled", "display": str(counters.trades_filled), "raw": counters.trades_filled, "format": "integer"},
        {"label": "Open Positions", "display": str(counters.open_positions), "raw": counters.open_positions, "format": "integer"},
        {"label": "Refused by Risk", "display": str(counters.refused_by_risk), "raw": counters.refused_by_risk, "format": "integer"},
    ]
    embed(ui.render_counter_chips(chips))


def render_equity_curve(client: AlpacaClient) -> None:
    range_label = st.radio(
        "Range", ["1D", "1W", "1M", "ALL"], index=2, horizontal=True,
        key="equity_curve_range", label_visibility="collapsed",
    )
    period_map = {"1D": "1D", "1W": "1W", "1M": "1M", "ALL": "all"}

    try:
        df = client.get_portfolio_history(period=period_map[range_label])
    except Exception as e:
        embed(ui.render_inline_alert(f"Could not load portfolio history: {html.escape(str(e))}", "error"))
        return

    if df.empty or len(df) < 2:
        embed(ui.render_inline_alert(
            "Not enough equity history yet for this range — it will build up as the agent runs "
            "over the competition period."
        ))
        if not df.empty:
            embed(ui.render_stat_cards([{
                "label": "Equity (latest)", "display": f"${df['equity'].iloc[-1]:,.2f}",
                "raw": df["equity"].iloc[-1], "format": "currency", "tone": "neutral",
            }]))
        return

    current_equity = df["equity"].iloc[-1]
    start_equity = df["equity"].iloc[0]
    change_dollar = current_equity - start_equity
    change_pct = (change_dollar / start_equity) if start_equity else 0.0
    tone = "positive" if change_dollar >= 0 else "negative"

    cards = [
        {"label": "Equity (Now)", "display": f"${current_equity:,.2f}", "raw": current_equity,
         "format": "currency", "tone": "neutral"},
        {"label": f"Change ({range_label})",
         "display": f"{'+' if change_dollar >= 0 else ''}${change_dollar:,.2f} ({change_pct:+.2%})",
         "raw": None, "format": "plain", "tone": tone},
    ]
    embed(ui.render_stat_cards(cards))

    fig = go.Figure(go.Scatter(
        x=df["timestamp"], y=df["equity"], mode="lines",
        line=dict(color=GREEN, width=2.2),
        hovertemplate="%{x|%b %d, %H:%M}<br>$%{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        height=280,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=ICE, family="Inter"),
        xaxis=dict(gridcolor="rgba(169, 180, 245, 0.10)", linecolor="rgba(124, 111, 240, 0.25)"),
        yaxis=dict(gridcolor="rgba(169, 180, 245, 0.10)", linecolor="rgba(124, 111, 240, 0.25)", tickprefix="$"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    if df["equity"].nunique() <= 1:
        st.caption("Equity has been flat so far — no realized/unrealized P&L has moved it yet. "
                   "This will start to show real movement once positions open and close.")


def render_price_chart(client: AlpacaClient, symbol: str) -> None:
    bars = client.get_bars(symbol, lookback_days=90)
    if bars.empty:
        embed(ui.render_inline_alert(f"No bar data for {html.escape(symbol)}.", "error"))
        return
    fig = go.Figure(data=[go.Candlestick(
        x=bars.index, open=bars["open"], high=bars["high"],
        low=bars["low"], close=bars["close"],
        increasing_line_color=GREEN, increasing_fillcolor=GREEN,
        decreasing_line_color=RED, decreasing_fillcolor=RED,
    )])
    fig.update_layout(
        title=f"{symbol} — last 90 days",
        height=380,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=ICE, family="Inter"),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(gridcolor="rgba(169, 180, 245, 0.12)", linecolor="rgba(124, 111, 240, 0.25)"),
        yaxis=dict(gridcolor="rgba(169, 180, 245, 0.12)", linecolor="rgba(124, 111, 240, 0.25)"),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_options_decision_log() -> None:
    section_title("Options Decision Log", "🧠", level="sm")
    decisions = [d for d in load_decisions() if d.get("asset_class") == "option" and d.get("status") == "ok"]
    if not decisions:
        embed(ui.render_inline_alert(
            "No options decisions logged yet. Run <code>python main.py options-run</code> "
            "or click 'Run options agent cycle now' above."
        ))
        return
    decisions = list(reversed(decisions))[:30]
    embed(ui.render_decision_cards([_prep_decision_record(d) for d in decisions]))


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def render_view_overview(client: AlpacaClient, open_positions_count: int) -> None:
    page_header("Overview", "Account state, the latest cycle, and equity over time — glanceable in a few seconds.")
    render_narrative_card()
    render_activity_counters(client, open_positions_count)
    section_divider()
    section_title("Account", "💼", level="lg")
    render_account_panel(client)
    section_divider()
    section_title("Equity Curve", "📈", level="md")
    render_equity_curve(client)


def render_view_positions(stock_positions: list[dict], option_positions: list[dict], client: AlpacaClient) -> None:
    page_header("Positions", "Every open position, priced and sized from live Alpaca data.")

    section_title(f"Stock Positions ({len(stock_positions)})", "📈", level="md")
    embed(ui.render_stock_position_cards([_prep_stock_position(p) for p in stock_positions]))

    section_divider()
    section_title(f"Open Option Positions ({len(option_positions)})", "🧾", level="md")
    stock_positions_by_symbol = {p["symbol"]: p for p in stock_positions}
    embed(ui.render_option_positions_table(
        [_prep_option_row(p, stock_positions_by_symbol, client) for p in option_positions]))

    section_divider()
    section_title("Price Chart", "📊", level="md")
    symbol = st.text_input("Symbol", value="AAPL", key="positions_chart_symbol").upper()
    if symbol:
        render_price_chart(client, symbol)


def render_view_options(
    client: AlpacaClient, options_client: OptionsClient,
    stock_positions: list[dict], option_positions: list[dict],
) -> None:
    page_header("Options", "Covered calls & cash-secured puts — the only two strategies this agent ever proposes.")

    section_title(f"Open Option Positions ({len(option_positions)})", "🧾", level="md")
    stock_positions_by_symbol = {p["symbol"]: p for p in stock_positions}
    embed(ui.render_option_positions_table(
        [_prep_option_row(p, stock_positions_by_symbol, client) for p in option_positions]))

    section_divider()
    section_title("Run Options Agent", "⚙️", level="md")
    st.caption("Only two defined-risk strategies are ever proposed: covered call, cash-secured put.")
    options_watchlist_input = st.text_input("Options Watchlist", value="AAPL,MSFT,TSLA,NVDA,SPY",
                                              key="options_watchlist_input")
    options_dry_run = st.checkbox("Dry run (decide only, no orders)", value=True, key="options_dry_run")
    if st.button("▶️ Run options agent cycle now", type="primary", key="options_run_button"):
        symbols = [s.strip().upper() for s in options_watchlist_input.split(",") if s.strip()]
        with st.spinner("Agent is analyzing option chains and consulting GPT-OSS-120B (Groq)..."):
            options_agent = OptionsTradingAgent(
                alpaca_client=client, options_client=options_client, dry_run=options_dry_run,
            )
            options_agent.run_cycle(symbols)
        st.session_state["_flash"] = ("success", "Options cycle complete — see the decision log below.")
        st.rerun()

    section_divider()
    render_options_decision_log()


def _short_error_reason(reason: str) -> str:
    """Error records store the full exception text (often a raw Groq API
    error blob with URLs and JSON). Show just the leading '{code} {STATUS}.'
    prefix these consistently start with, falling back to a plain truncation
    for anything that doesn't match that shape."""
    match = re.match(r"^(\d{3}\s+[A-Z_]+)\.", reason)
    if match:
        return match.group(1)
    return reason if len(reason) <= 90 else reason[:87] + "…"


def _decision_bucket(d: dict) -> str:
    """Classifies one decision record into the same buckets the filter pills
    count -- kept as a single source of truth so the pill counts and the
    actual filtering never drift apart."""
    if d.get("status") == "error":
        return "errors"
    if d.get("trigger") == "exit_engine":
        return "exits"
    order_placed = d.get("order") is not None
    approved = bool(d.get("risk_approved"))
    if order_placed:
        return "executed"
    if approved:
        return "dry_run"
    return "blocked"


def render_view_decision_history() -> None:
    page_header("Decisions", "Every decision the agent has ever logged — approved, rejected, executed, or held. "
                              "Showing what the agent chose NOT to do is as important as showing what it did.")

    all_decisions = [d for d in load_decisions() if d.get("record_type") != "exit_engine_summary"]
    if not all_decisions:
        embed(ui.render_inline_alert("No decisions logged yet. Run an agent cycle from the sidebar to start building history."))
        return

    symbols = sorted({d.get("symbol", "") for d in all_decisions if d.get("symbol")})
    symbol_filter = st.selectbox("Symbol", ["All"] + symbols, key="dh_symbol_filter")

    buckets = [_decision_bucket(d) for d in all_decisions]
    counts = {
        "all": len(all_decisions),
        "executed": buckets.count("executed"),
        "dry_run": buckets.count("dry_run"),
        "blocked": buckets.count("blocked"),
        "exits": buckets.count("exits"),
        "errors": buckets.count("errors"),
    }
    pill_defs = [
        ("all", f"ALL · {counts['all']}"),
        ("executed", f"EXECUTED · {counts['executed']}"),
        ("dry_run", f"DRY RUN · {counts['dry_run']}"),
        ("blocked", f"BLOCKED · {counts['blocked']}"),
        ("exits", f"EXITS · {counts['exits']}"),
        ("errors", f"ERRORS · {counts['errors']}"),
    ]
    st.session_state.setdefault("dh_pill_filter", "all")
    pill_cols = st.columns(len(pill_defs))
    for col, (key, label) in zip(pill_cols, pill_defs):
        active = st.session_state["dh_pill_filter"] == key
        if col.button(label, key=f"dh_pill_{key}", type="primary" if active else "secondary",
                      use_container_width=True):
            st.session_state["dh_pill_filter"] = key
    pill_filter = st.session_state["dh_pill_filter"]

    filtered = [
        d for d in all_decisions
        if (symbol_filter == "All" or d.get("symbol") == symbol_filter)
        and (pill_filter == "all" or _decision_bucket(d) == pill_filter)
    ]
    filtered = list(reversed(filtered))  # newest first

    if not filtered:
        embed(ui.render_inline_alert("No decisions match the current filters."))
        return

    limit_key = "dh_row_limit"
    st.session_state.setdefault(limit_key, 30)
    limit = st.session_state[limit_key]

    ok_records = [d for d in filtered[:limit] if d.get("status") == "ok"]
    error_records = [d for d in filtered[:limit] if d.get("status") == "error"]

    section_title(f"{len(filtered)} matching record(s)", level="sm")
    if error_records:
        with st.expander(f"{len(error_records)} error record(s) in this page (e.g. LLM rate limits)"):
            for e in error_records:
                st.caption(f"⚠ {e.get('symbol', '?')} — {_short_error_reason(e.get('reason', 'unknown error'))} "
                           f"({str(e.get('timestamp', ''))[:19]})")

    embed(ui.render_decision_table([_prep_decision_record(d) for d in ok_records]))

    if limit < len(filtered):
        if st.button(f"Load 30 more (showing {min(limit, len(filtered))} of {len(filtered)})", key="dh_load_more"):
            st.session_state[limit_key] += 30
            st.rerun()


def render_view_strategy() -> None:
    page_header("Methodology", "How this agent actually decides — the real pipeline, step by step.")
    embed(ui.render_how_it_decides())


def render_view_risk_gates() -> None:
    page_header(
        "Risk Gate Catalog",
        "Every hard-coded, non-LLM check a proposed trade has to survive before it's ever sized "
        "or sent to Alpaca — named, numbered, and counted against every decision this agent has "
        "actually logged.",
    )
    decisions = load_decisions()
    stock_stats, options_stats = compute_gate_stats(decisions)

    if stock_stats.evaluated + options_stats.evaluated == 0:
        embed(ui.render_inline_alert(
            "No non-hold decisions logged yet — run an agent cycle from the sidebar to start "
            "populating live pass/fail counts for each gate below."
        ))

    embed(ui.render_gate_catalog(
        stock_stats.gates, stock_stats.blocked_counts, stock_stats.evaluated, stock_stats.approved,
        options_stats.gates, options_stats.blocked_counts, options_stats.evaluated, options_stats.approved,
    ))

    excluded = stock_stats.holds_excluded + options_stats.holds_excluded
    errors = stock_stats.errors_excluded + options_stats.errors_excluded
    if excluded or errors:
        st.caption(
            f"Excluded from the counts above: {excluded} hold/no-candidate recommendation(s) that "
            f"never reached these gates, and {errors} error record(s)."
        )


def render_view_performance() -> None:
    page_header("Performance", "Realized P&L from every position that has actually closed.")
    render_performance_panel()


def _backtest_cache_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "backtest_results.json"


_BACKTEST_MAX_CALLS = 300  # hard cap on LLM calls per run -- protects free-tier quota/rate limits
_BACKTEST_CADENCE_OPTIONS = {"Daily": 1, "Every 3 trading days": 3, "Weekly": 5}


def render_view_backtest(client: AlpacaClient) -> None:
    page_header(
        "Backtest",
        "Replays the live decision pipeline — same indicators, same Groq call, same risk-manager "
        "thresholds — over past Alpaca daily bars, so the strategy has some evidence behind it "
        "before the paper account has real closed trades to show.",
    )
    embed(ui.render_backtest_banner(backtest_engine.BACKTEST_DISCLAIMER))

    section_divider()
    section_title("Configure Run", "🧪", level="md")

    default_watchlist = [s.strip().upper() for s in
                          os.getenv("WATCHLIST", "AAPL,MSFT,TSLA,NVDA,SPY").split(",") if s.strip()]
    col1, col2, col3 = st.columns(3)
    with col1:
        symbols = st.multiselect("Symbols", options=default_watchlist,
                                  default=default_watchlist[:2], key="bt_symbols")
    with col2:
        lookback_months = st.select_slider("Lookback (months)", options=[1, 2, 3, 4, 5, 6],
                                            value=3, key="bt_lookback")
    with col3:
        cadence_label = st.selectbox("Decision cadence", list(_BACKTEST_CADENCE_OPTIONS.keys()),
                                      index=1, key="bt_cadence")
    cadence_days = _BACKTEST_CADENCE_OPTIONS[cadence_label]

    est_calls = (backtest_engine.estimate_decision_count(len(symbols), lookback_months, cadence_days)
                 if symbols else 0)
    over_cap = est_calls > _BACKTEST_MAX_CALLS

    if not symbols:
        st.caption("Select at least one symbol to run a backtest.")
    else:
        est_minutes_low, est_minutes_high = est_calls * 0.6 / 60, est_calls * 1.5 / 60
        st.caption(
            f"Estimated ~{est_calls} live Groq calls (the exact same decision call the live agent "
            f"makes) — roughly {est_minutes_low:.0f}–{est_minutes_high:.0f} min and real API quota. "
            f"Capped at {_BACKTEST_MAX_CALLS} calls per run."
        )
    if over_cap:
        embed(ui.render_inline_alert(
            f"This configuration is estimated at {est_calls} calls, above the {_BACKTEST_MAX_CALLS}-call "
            f"cap for a single run. Reduce symbols, shorten the lookback, or widen the cadence.", "error",
        ))

    run_clicked = st.button("▶️ Run Backtest", type="primary",
                             disabled=(not symbols or over_cap), key="bt_run_button")

    cache_path = _backtest_cache_path()

    if run_clicked:
        progress_placeholder = st.empty()
        status_placeholder = st.empty()
        progress_bar = progress_placeholder.progress(0)

        def _progress(step: int, total: int, sym: str, dt) -> None:
            progress_bar.progress(min(100, int((step / max(1, total)) * 100)))
            status_placeholder.caption(f"Evaluating {sym} — {dt} ({step + 1}/{total})")

        try:
            result = backtest_engine.run_backtest(
                client=client, symbols=symbols, lookback_months=lookback_months,
                cadence_days=cadence_days, progress_cb=_progress,
            )
        except Exception as e:
            progress_placeholder.empty()
            status_placeholder.empty()
            embed(ui.render_inline_alert(f"Backtest failed: {html.escape(str(e))}", "error"))
            return

        progress_placeholder.empty()
        status_placeholder.empty()

        result_dict = dataclasses.asdict(result)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(result_dict, f, indent=2)
        st.session_state["_backtest_result"] = result_dict
        st.session_state["_flash"] = ("success", "Backtest complete — see results below.")
        st.rerun()

    result_dict = st.session_state.get("_backtest_result")
    if result_dict is None and cache_path.exists():
        with open(cache_path) as f:
            result_dict = json.load(f)
        st.session_state["_backtest_result"] = result_dict

    section_divider()

    if result_dict is None:
        embed(ui.render_inline_alert(
            "No backtest has been run yet. Configure symbols above and click 'Run Backtest' — this "
            "makes real Groq calls and can take a few minutes."
        ))
        return

    generated_at = str(result_dict.get("generated_at", ""))[:19].replace("T", " ")
    section_title(
        f"Results — {', '.join(result_dict['symbols'])} · {result_dict['start_date']} to "
        f"{result_dict['end_date']} · generated {generated_at} UTC", level="sm",
    )

    win_rate = result_dict.get("win_rate")
    sharpe = result_dict.get("sharpe_ratio")
    total_return = result_dict["total_return_pct"]
    cards = [
        {"label": "Total Return", "display": f"{total_return:+.2f}%", "raw": total_return,
         "format": "plain", "tone": "positive" if total_return >= 0 else "negative"},
        {"label": "Sharpe Ratio", "display": f"{sharpe:.2f}" if sharpe is not None else "—",
         "raw": None, "format": "plain", "tone": "neutral"},
        {"label": "Win Rate", "display": f"{win_rate:.0%}" if win_rate is not None else "—",
         "raw": None, "format": "plain", "tone": "neutral"},
        {"label": "Max Drawdown", "display": f"{result_dict['max_drawdown_pct']:.2f}%",
         "raw": None, "format": "plain", "tone": "negative"},
        {"label": "Trades (W/L)", "display": f"{result_dict['winning_trades']} / {result_dict['losing_trades']}",
         "raw": None, "format": "plain", "tone": "neutral"},
        {"label": "LLM Errors", "display": str(result_dict["llm_errors"]), "raw": None,
         "format": "plain", "tone": "neutral" if result_dict["llm_errors"] == 0 else "negative"},
    ]
    embed(ui.render_stat_cards(cards))

    section_divider()
    section_title("Simulated Equity Curve", "📈", level="md")
    curve = result_dict.get("equity_curve") or []
    if len(curve) >= 2:
        curve_df = pd.DataFrame(curve)
        fig = go.Figure(go.Scatter(
            x=curve_df["date"], y=curve_df["equity"], mode="lines",
            line=dict(color=ACCENT2, width=2.2),
            hovertemplate="%{x}<br>$%{y:,.2f}<extra></extra>",
        ))
        fig.update_layout(
            height=280,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=ICE, family="Inter"),
            xaxis=dict(gridcolor="rgba(169, 180, 245, 0.10)", linecolor="rgba(124, 111, 240, 0.25)"),
            yaxis=dict(gridcolor="rgba(169, 180, 245, 0.10)", linecolor="rgba(124, 111, 240, 0.25)", tickprefix="$"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        embed(ui.render_inline_alert("Not enough simulated decision points to plot an equity curve."))

    section_divider()
    section_title("Simulated Trade Log", "📜", level="md")
    embed(ui.render_backtest_trades(result_dict.get("trades") or []))


def render_view_docs(client: AlpacaClient) -> None:
    page_header("Docs", "What this project is, and where to find it.")

    st.markdown(
        "An autonomous, explainable paper-trading agent built for the Alpaca AI Trading Agents "
        "Hackathon (lablab.ai). GPT-OSS-120B (via Groq) reasons over live Alpaca market data and technical "
        "indicators to propose stock buy/sell/hold calls and defined-risk options strategies "
        "(covered calls, cash-secured puts). It never sizes or executes a trade on its own — a "
        "fully deterministic risk manager and exit engine independently gate every decision, and "
        "every decision (approved or rejected) is logged and shown on this dashboard."
    )

    deployed_url = os.getenv("DASHBOARD_URL", "").strip()
    links = [("GitHub Repository", "https://github.com/isianioui/Alpaca-AI-Trading-Agent")]
    if deployed_url:
        links.append(("Deployed Dashboard", deployed_url))
    for label, url in links:
        st.markdown(f"- **{label}:** [{url}]({url})")
    if not deployed_url:
        st.caption("Not yet deployed — set DASHBOARD_URL in .env once live on Streamlit Community "
                   "Cloud and this link will appear automatically.")

    section_divider()
    section_title("Currently Configured Risk Limits", "🛡️", level="md")
    limits = RiskLimits()
    opt_limits = OptionsRiskLimits.from_env()
    exit_limits = ExitLimits.from_env()
    st.markdown(
        f"Stocks: max **{limits.max_position_pct:.0%}** of equity per position, max "
        f"**{limits.max_open_positions}** open positions, **{limits.max_daily_loss_pct:.0%}** daily "
        f"drawdown circuit breaker. Options: max **{opt_limits.max_options_collateral_pct:.0%}** of "
        f"equity as collateral, max **{opt_limits.max_open_option_positions}** open option positions. "
        f"Exits: stop-loss at **{exit_limits.stop_loss_pct:.0%}**, take-profit at "
        f"**{exit_limits.take_profit_pct:.0%}**. Full breakdown on the Settings page."
    )


def render_view_settings(client: AlpacaClient, options_client: OptionsClient) -> None:
    page_header("Settings", "Read-only — current configuration, pulled live from environment and code defaults.")

    section_title("Watchlists", "📋", level="md")
    kv_grid([
        ("Stock Watchlist (WATCHLIST)", os.getenv("WATCHLIST", "AAPL,MSFT,TSLA,NVDA,SPY (default)")),
        ("Options Watchlist (OPTIONS_WATCHLIST)",
         os.getenv("OPTIONS_WATCHLIST", os.getenv("WATCHLIST", "AAPL,MSFT,TSLA,NVDA,SPY (default)"))),
    ])

    section_divider()
    section_title("Stock Risk Manager", "🛡️", level="md")
    limits = RiskLimits()
    kv_grid([
        ("Max Position Size", f"{limits.max_position_pct:.0%} of equity"),
        ("Max Open Positions", str(limits.max_open_positions)),
        ("Daily Loss Circuit Breaker", f"{limits.max_daily_loss_pct:.0%}"),
        ("Min Confidence to Act", f"{limits.min_confidence_to_act:.2f}"),
    ])
    st.caption(
        "⚠️ These are RiskLimits' hardcoded dataclass defaults — MAX_POSITION_PCT / "
        "MAX_OPEN_POSITIONS / MAX_DAILY_LOSS_PCT are documented in .env.example but "
        "RiskManager doesn't currently read them from the environment (unlike the options risk "
        "manager and exit engine below, which do). Flagging this as-found rather than silently "
        "wiring it up, since risk manager logic was explicitly out of scope for this pass."
    )

    section_divider()
    section_title("Options Risk Manager", "🧾", level="md")
    opt_limits = OptionsRiskLimits.from_env()
    kv_grid([
        ("Max Collateral (MAX_OPTIONS_COLLATERAL_PCT)", f"{opt_limits.max_options_collateral_pct:.0%} of equity"),
        ("Max Open Option Positions (MAX_OPEN_OPTION_POSITIONS)", str(opt_limits.max_open_option_positions)),
        ("Min Confidence to Act", f"{opt_limits.min_confidence_to_act:.2f}"),
    ])

    section_divider()
    section_title("Exit Engine", "🚪", level="md")
    exit_limits = ExitLimits.from_env()
    kv_grid([
        ("Stop Loss (STOP_LOSS_PCT)", f"{exit_limits.stop_loss_pct:.0%}"),
        ("Take Profit (TAKE_PROFIT_PCT)", f"{exit_limits.take_profit_pct:.0%}"),
        ("Options Profit Target (OPTIONS_PROFIT_TARGET_PCT)", f"{exit_limits.options_profit_target_pct:.0%}"),
        ("Options Stop Multiple (OPTIONS_STOP_MULTIPLE)", f"{exit_limits.options_stop_multiple:.1f}x"),
    ])

    section_divider()
    section_title("Execution", "⚙️", level="md")
    kv_grid([
        ("Execution Backend", client.execution_backend.upper()),
        ("Paper Trading", "Yes" if client.paper else "NO — LIVE"),
        ("Groq Model (GROQ_MODEL)", os.getenv("GROQ_MODEL", "openai/gpt-oss-120b (default)")),
    ])


def render_sidebar_brand() -> None:
    st.sidebar.markdown(
        '<div class="brand-block">'
        '<div class="brand-logo-row"><span class="brand-mark">Q</span>'
        '<div><div class="brand-name">QASIX AI</div>'
        '<div class="brand-tag">Autonomous Trading Agent</div></div></div>'
        '</div>',
        unsafe_allow_html=True,
    )


def render_sidebar_nav(positions_badge_count: int) -> None:
    st.session_state.setdefault("active_view", "Overview")
    first_group = True
    for group_label, items in NAV_GROUPS:
        cls = "nav-group-label first" if first_group else "nav-group-label"
        st.sidebar.markdown(f'<div class="{cls}">{group_label}</div>', unsafe_allow_html=True)
        first_group = False
        for name, icon in items:
            label = f"{icon}  {name}"
            if name == "Positions" and positions_badge_count:
                label += f"  ·  {positions_badge_count}"
            active = st.session_state["active_view"] == name
            if st.sidebar.button(label, key=f"nav_{name}", type="primary" if active else "secondary",
                                  use_container_width=True):
                st.session_state["active_view"] = name


def render_sidebar_run_controls(client: AlpacaClient, options_client: OptionsClient) -> None:
    st.sidebar.divider()
    st.sidebar.header("⚙️ Run Agent (Stocks)")
    watchlist_input = st.sidebar.text_input("Watchlist", value="AAPL,MSFT,TSLA,NVDA,SPY")
    dry_run = st.sidebar.checkbox("Dry run (decide only, no orders)", value=True)
    if st.sidebar.button("▶️ Run agent cycle now", type="primary"):
        symbols = [s.strip().upper() for s in watchlist_input.split(",") if s.strip()]
        with st.spinner("Agent is analyzing the market and consulting GPT-OSS-120B (Groq)..."):
            agent = TradingAgent(alpaca_client=client, dry_run=dry_run)
            agent.run_cycle(symbols)
        st.session_state["_flash"] = ("success", "Cycle complete.")
        st.rerun()

    st.sidebar.divider()
    st.sidebar.header("🛡️ Exit Engine")
    st.sidebar.caption("Deterministic stop-loss / take-profit sweep over every open stock + option "
                        "position. No LLM call.")
    exit_dry_run = st.sidebar.checkbox("Dry run (evaluate only, no close orders)", value=True, key="exit_dry_run")
    if st.sidebar.button("▶️ Run Exit Engine now", type="primary", key="exit_engine_button"):
        with st.spinner("Evaluating open positions against exit thresholds..."):
            st.session_state["last_exit_engine_results"] = run_exit_engine(
                client, options_client, dry_run=exit_dry_run)
        st.rerun()

    last_results = st.session_state.get("last_exit_engine_results")
    if last_results is not None:
        if not last_results:
            st.sidebar.caption("Last run: no open positions to evaluate.")
        else:
            closes = [r for r in last_results if r["action"] == "CLOSE"]
            st.sidebar.caption(f"Last run: {len(last_results)} position(s) evaluated, "
                                f"{len(closes)} CLOSE decision(s).")
            for r in last_results:
                icon = "🔻" if r["action"] == "CLOSE" else "·"
                st.sidebar.caption(f"{icon} **{r['symbol']}** ({r['asset_class']}): {r['reason']}")

    st.sidebar.divider()
    st.sidebar.caption("Built for the Alpaca AI Trading Agents Hackathon (lablab.ai). "
                        "Runs on Alpaca paper trading — no real money at risk.")


def main() -> None:
    inject_css()

    st.markdown('<div class="app-title">◆ QASIX AI Trading Agent</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="app-subtitle">An autonomous, GPT-OSS-120B-powered paper trading agent '
        'built on Alpaca\'s Trading API.</div>',
        unsafe_allow_html=True,
    )

    try:
        client = get_client()
        options_client = get_options_client()
    except ValueError as e:
        embed(ui.render_inline_alert(
            f"{html.escape(str(e))} Copy <code>.env.example</code> to <code>.env</code> and fill in "
            f"your Alpaca + Groq API keys.", "error",
        ))
        return

    flash = st.session_state.pop("_flash", None)
    if flash:
        embed(ui.render_inline_alert(flash[1], flash[0]))

    render_status_strip(client)

    try:
        stock_positions = client.get_positions()
    except Exception:
        stock_positions = []
    try:
        option_positions = options_client.get_option_positions()
    except Exception:
        option_positions = []
    open_positions_count = len(stock_positions) + len(option_positions)

    with st.sidebar:
        render_sidebar_brand()
        render_sidebar_nav(open_positions_count)
        render_sidebar_run_controls(client, options_client)

    view = st.session_state["active_view"]
    if view == "Overview":
        render_view_overview(client, open_positions_count)
    elif view == "Positions":
        render_view_positions(stock_positions, option_positions, client)
    elif view == "Options":
        render_view_options(client, options_client, stock_positions, option_positions)
    elif view == "Decisions":
        render_view_decision_history()
    elif view == "Performance":
        render_view_performance()
    elif view == "Backtest":
        render_view_backtest(client)
    elif view == "Risk Gates":
        render_view_risk_gates()
    elif view == "Methodology":
        render_view_strategy()
    elif view == "Docs":
        render_view_docs(client)
    elif view == "Settings":
        render_view_settings(client, options_client)

    section_divider()
    embed(ui.render_footer(client.paper))


if __name__ == "__main__":
    main()
