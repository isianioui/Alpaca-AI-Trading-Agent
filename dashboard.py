"""
Streamlit dashboard for the Alpaca AI Trading Agent.

Single-page app with sidebar view-switching (st.session_state["active_view"]),
not Streamlit's separate pages/ feature, so the sidebar can host both the nav
and the "Run Agent" controls together.

Views:
  LIVE     — Overview, Positions, Options
  RECORDS  — Decision History, Performance, Strategy
  ABOUT    — Docs, Settings

Run with:  streamlit run dashboard.py
"""

from __future__ import annotations

import html
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from src import trade_history
from src.alpaca_client import AlpacaClient
from src.cycle_summary import compute_activity_counters, summarize_latest_cycle
from src.exit_engine import ExitLimits, run_exit_engine
from src.logger import load_decisions
from src.options_client import OptionsClient
from src.options_trading_agent import OptionsTradingAgent
from src.risk_manager import OptionsRiskLimits, RiskLimits
from src.trading_agent import TradingAgent

load_dotenv()

st.set_page_config(page_title="Alpaca AI Trading Agent", page_icon="🦙", layout="wide")

# ---------------------------------------------------------------------------
# Brand: "Ledger Navy" — matches the pitch deck palette.
# ---------------------------------------------------------------------------
NAVY = "#162447"
NAVY_MID = "#1E2761"
ICE = "#CADCFC"
GREEN = "#1FCB8F"
RED = "#E8615A"
OFFWHITE = "#F5F7FB"
MUTED_ON_DARK = "#9FB0D0"

NAV_GROUPS = [
    ("LIVE", [("Overview", "🏠"), ("Positions", "📊"), ("Options", "🧾")]),
    ("RECORDS", [("Decision History", "🧠"), ("Performance", "📈"), ("Strategy", "🧭")]),
    ("ABOUT", [("Docs", "📄"), ("Settings", "⚙️")]),
]


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: {NAVY};
        }}
        #MainMenu {{visibility: hidden;}}
        footer {{visibility: hidden;}}

        /* ---- Header ---- */
        .app-title {{
            font-size: 2.2rem;
            font-weight: 800;
            color: {OFFWHITE};
            margin-bottom: 0.1rem;
            letter-spacing: -0.02em;
        }}
        .app-subtitle {{
            font-size: 0.95rem;
            color: {ICE};
            opacity: 0.85;
            margin-bottom: 1.4rem;
        }}

        /* ---- Page header (per-view) ---- */
        .page-title {{
            font-size: 1.55rem;
            font-weight: 800;
            color: {OFFWHITE};
            margin-bottom: 0.15rem;
            letter-spacing: -0.01em;
        }}
        .page-subtitle {{
            font-size: 0.85rem;
            color: {MUTED_ON_DARK};
            margin-bottom: 1.2rem;
        }}

        /* ---- Section titles (visual hierarchy: lg > md > sm) ---- */
        .section-title {{
            font-weight: 700;
            color: {OFFWHITE};
            margin: 0.2rem 0 0.8rem 0;
        }}
        .section-title.lg {{ font-size: 1.35rem; }}
        .section-title.md {{ font-size: 1.1rem; }}
        .section-title.sm {{
            font-size: 0.95rem;
            color: {MUTED_ON_DARK};
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}

        .section-divider {{
            border: none;
            border-top: 1px solid rgba(202, 220, 252, 0.14);
            margin: 1.6rem 0;
        }}

        /* ---- Metric / stat-callout cards ---- */
        .metric-card {{
            background: {NAVY_MID};
            border: 1px solid rgba(202, 220, 252, 0.12);
            border-radius: 14px;
            padding: 1rem 1.1rem;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
            height: 100%;
        }}
        .metric-label {{
            font-size: 0.72rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: {MUTED_ON_DARK};
            margin-bottom: 0.35rem;
        }}
        .metric-value {{
            font-size: 1.65rem;
            font-weight: 800;
            color: {OFFWHITE};
            line-height: 1.15;
        }}
        .metric-value.positive {{ color: {GREEN}; }}
        .metric-value.negative {{ color: {RED}; }}

        /* ---- Brand alert / empty state (replaces st.info) ---- */
        .brand-alert {{
            background: {NAVY_MID};
            border: 1px solid rgba(202, 220, 252, 0.16);
            border-left: 3px solid {ICE};
            border-radius: 10px;
            padding: 0.85rem 1.1rem;
            color: {ICE};
            font-size: 0.92rem;
        }}
        .brand-alert code {{
            background: rgba(202, 220, 252, 0.12);
            padding: 0.05rem 0.35rem;
            border-radius: 4px;
            color: {OFFWHITE};
        }}

        /* ---- Decision log cards ---- */
        .decision-card {{
            background: {NAVY_MID};
            border-radius: 12px;
            border: 1px solid rgba(202, 220, 252, 0.10);
            border-left: 4px solid {MUTED_ON_DARK};
            padding: 0.85rem 1.1rem 0.7rem 1.1rem;
            margin-top: 0.6rem;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.2);
        }}
        .decision-card.executed {{ border-left-color: {GREEN}; }}
        .decision-card.blocked {{ border-left-color: {RED}; }}
        .decision-card.approved-dry {{ border-left-color: {ICE}; }}

        .decision-top {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 0.45rem;
        }}
        .decision-badge {{
            display: inline-block;
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            padding: 0.15rem 0.55rem;
            border-radius: 999px;
        }}
        .decision-badge.executed {{
            background: rgba(31, 203, 143, 0.15);
            color: {GREEN};
            border: 1px solid rgba(31, 203, 143, 0.45);
        }}
        .decision-badge.blocked {{
            background: rgba(232, 97, 90, 0.15);
            color: {RED};
            border: 1px solid rgba(232, 97, 90, 0.45);
        }}
        .decision-badge.approved-dry {{
            background: rgba(202, 220, 252, 0.14);
            color: {ICE};
            border: 1px solid rgba(202, 220, 252, 0.4);
        }}
        .decision-meta {{
            font-size: 0.76rem;
            color: {MUTED_ON_DARK};
        }}
        .decision-header {{
            font-size: 1.05rem;
            font-weight: 800;
            color: {OFFWHITE};
            margin-bottom: 0.15rem;
        }}
        .decision-quote {{
            font-style: italic;
            color: {ICE};
            font-size: 0.88rem;
            margin-top: 0.5rem;
            padding-left: 0.65rem;
            border-left: 2px solid rgba(202, 220, 252, 0.25);
            opacity: 0.95;
        }}

        /* Tone down the native expander that holds the drill-down detail
           so it reads as part of the card above rather than a separate
           default Streamlit component. */
        div[data-testid="stExpander"] {{
            border: 1px solid rgba(202, 220, 252, 0.08) !important;
            border-top: none !important;
            border-radius: 0 0 12px 12px !important;
            background: rgba(30, 39, 97, 0.35) !important;
            margin-top: -0.6rem;
            margin-bottom: 0.7rem;
            box-shadow: none !important;
        }}
        div[data-testid="stExpander"] summary {{
            font-size: 0.82rem;
            color: {MUTED_ON_DARK};
        }}

        /* ---- Tabs (still used inside a couple of views) ---- */
        button[data-baseweb="tab"] {{
            font-size: 1rem;
            font-weight: 700;
        }}
        div[data-baseweb="tab-highlight"] {{
            background-color: {GREEN} !important;
        }}

        /* ---- Sidebar: nav + CTA ---- */
        section[data-testid="stSidebar"] button[kind="primary"] {{
            width: 100%;
            padding: 0.55rem 0.9rem;
            font-weight: 700;
            border-radius: 8px;
        }}
        section[data-testid="stSidebar"] button[kind="secondary"] {{
            width: 100%;
            padding: 0.55rem 0.9rem;
            font-weight: 600;
            border-radius: 8px;
            background: transparent;
        }}
        section[data-testid="stSidebar"] div[data-testid="stButton"] button {{
            justify-content: flex-start;
            text-align: left;
        }}
        section[data-testid="stSidebar"] div[data-testid="stButton"] button p {{
            text-align: left;
        }}
        section[data-testid="stSidebar"] .stTextInput label,
        section[data-testid="stSidebar"] .stCheckbox label {{
            color: {ICE};
        }}
        .nav-group-label {{
            font-size: 0.66rem;
            font-weight: 800;
            color: {MUTED_ON_DARK};
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin: 1rem 0 0.3rem 0.15rem;
        }}
        .nav-group-label.first {{ margin-top: 0.2rem; }}

        /* ---- System status strip ---- */
        .status-strip {{
            display: flex;
            gap: 0.6rem;
            flex-wrap: wrap;
            margin-bottom: 1.2rem;
        }}
        .status-badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            font-size: 0.76rem;
            font-weight: 600;
            padding: 0.32rem 0.75rem;
            border-radius: 999px;
            background: {NAVY_MID};
            border: 1px solid rgba(202, 220, 252, 0.14);
            color: {OFFWHITE};
        }}
        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            flex-shrink: 0;
        }}
        .status-dot.ok {{ background: {GREEN}; }}
        .status-dot.bad {{ background: {RED}; }}
        .status-badge.paper {{
            background: rgba(31, 203, 143, 0.12);
            border-color: rgba(31, 203, 143, 0.4);
            color: {GREEN};
            font-weight: 800;
        }}
        .status-badge.live-warning {{
            background: rgba(232, 97, 90, 0.18);
            border-color: rgba(232, 97, 90, 0.55);
            color: {RED};
            font-weight: 800;
        }}

        /* ---- Latest Cycle narrative card ---- */
        .narrative-card {{
            background: {NAVY_MID};
            border: 1px solid rgba(202, 220, 252, 0.12);
            border-radius: 14px;
            padding: 1.1rem 1.3rem;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
            margin-bottom: 1rem;
        }}
        .narrative-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.5rem;
        }}
        .narrative-label {{
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: {MUTED_ON_DARK};
        }}
        .narrative-text {{
            font-size: 0.98rem;
            color: {OFFWHITE};
            line-height: 1.55;
        }}

        /* ---- Activity counter chips ---- */
        .counter-row {{
            display: flex;
            gap: 0.6rem;
            flex-wrap: wrap;
            margin: 0.2rem 0 1.2rem 0;
        }}
        .counter-chip {{
            background: rgba(30, 39, 97, 0.55);
            border: 1px solid rgba(202, 220, 252, 0.10);
            border-radius: 10px;
            padding: 0.5rem 0.95rem;
            min-width: 108px;
        }}
        .counter-value {{
            font-size: 1.15rem;
            font-weight: 800;
            color: {OFFWHITE};
            line-height: 1.2;
        }}
        .counter-label {{
            font-size: 0.66rem;
            color: {MUTED_ON_DARK};
            text-transform: uppercase;
            letter-spacing: 0.04em;
            margin-top: 1px;
        }}

        /* ---- Position cards ---- */
        .position-card {{
            background: {NAVY_MID};
            border: 1px solid rgba(202, 220, 252, 0.12);
            border-radius: 14px;
            padding: 1rem 1.2rem;
            margin-bottom: 0.5rem;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.2);
        }}
        .position-top {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-bottom: 0.7rem;
        }}
        .position-symbol {{
            font-size: 1.15rem;
            font-weight: 800;
            color: {OFFWHITE};
        }}
        .position-sub {{
            font-size: 0.78rem;
            color: {MUTED_ON_DARK};
            margin-top: 2px;
        }}
        .position-badges {{
            display: flex;
            gap: 0.4rem;
            align-items: center;
            flex-wrap: wrap;
        }}
        .position-badge {{
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            padding: 0.18rem 0.6rem;
            border-radius: 999px;
            white-space: nowrap;
        }}
        .position-badge.long {{
            background: rgba(31, 203, 143, 0.15);
            color: {GREEN};
            border: 1px solid rgba(31, 203, 143, 0.4);
        }}
        .position-badge.sell {{
            background: rgba(202, 220, 252, 0.14);
            color: {ICE};
            border: 1px solid rgba(202, 220, 252, 0.4);
        }}
        .position-badge.strategy {{
            background: rgba(30, 39, 97, 0.8);
            color: {ICE};
            border: 1px solid rgba(202, 220, 252, 0.18);
        }}
        .position-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 0.7rem 0.9rem;
        }}
        .position-stat-label {{
            font-size: 0.64rem;
            color: {MUTED_ON_DARK};
            text-transform: uppercase;
            letter-spacing: 0.04em;
            margin-bottom: 2px;
        }}
        .position-stat-value {{
            font-size: 0.94rem;
            font-weight: 700;
            color: {OFFWHITE};
        }}
        .position-stat-value.positive {{ color: {GREEN}; }}
        .position-stat-value.negative {{ color: {RED}; }}

        /* ---- Key/value grid (Docs, Settings) ---- */
        .kv-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 0.6rem;
            margin: 0.6rem 0 1.1rem 0;
        }}
        .kv-row {{
            background: rgba(30, 39, 97, 0.5);
            border: 1px solid rgba(202, 220, 252, 0.10);
            border-radius: 10px;
            padding: 0.6rem 0.9rem;
        }}
        .kv-key {{
            font-size: 0.66rem;
            color: {MUTED_ON_DARK};
            text-transform: uppercase;
            letter-spacing: 0.04em;
            margin-bottom: 2px;
        }}
        .kv-value {{
            font-size: 0.92rem;
            font-weight: 700;
            color: {OFFWHITE};
        }}

        /* ---- How It Decides explainer ---- */
        .explainer-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 0.9rem;
            margin-top: 0.6rem;
        }}
        .explainer-step {{
            background: {NAVY_MID};
            border: 1px solid rgba(202, 220, 252, 0.10);
            border-radius: 12px;
            padding: 1rem 1.1rem;
            height: 100%;
        }}
        .explainer-step .step-num {{
            font-size: 0.76rem;
            font-weight: 800;
            color: {GREEN};
            letter-spacing: 0.06em;
            margin-bottom: 0.35rem;
        }}
        .explainer-step .step-title {{
            font-size: 0.95rem;
            font-weight: 800;
            color: {OFFWHITE};
            margin-bottom: 0.4rem;
        }}
        .explainer-step .step-body {{
            font-size: 0.85rem;
            color: {ICE};
            line-height: 1.5;
            opacity: 0.92;
        }}

        /* ---- Footer ---- */
        .app-footer {{
            margin-top: 2.5rem;
            padding-top: 1.2rem;
            border-top: 1px solid rgba(202, 220, 252, 0.12);
            font-size: 0.78rem;
            color: {MUTED_ON_DARK};
            text-align: center;
            line-height: 1.7;
        }}
        .app-footer a {{
            color: {ICE};
            text-decoration: none;
        }}
        .app-footer a:hover {{
            text-decoration: underline;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def section_title(text: str, icon: str = "", level: str = "md") -> None:
    label = f"{icon} {text}".strip()
    st.markdown(f'<div class="section-title {level}">{html.escape(label)}</div>', unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="page-title">{html.escape(title)}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="page-subtitle">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


def section_divider() -> None:
    st.markdown('<hr class="section-divider" />', unsafe_allow_html=True)


def brand_alert(message_html: str) -> None:
    """Renders a navy/ice empty-state card in place of st.info(). Caller-provided
    HTML must already be trusted (static copy) or pre-escaped."""
    st.markdown(f'<div class="brand-alert">{message_html}</div>', unsafe_allow_html=True)


def metric_card_html(label: str, value: str, tone: str = "neutral") -> str:
    arrow = {"positive": "▲ ", "negative": "▼ "}.get(tone, "")
    value_class = f"metric-value {tone}" if tone in ("positive", "negative") else "metric-value"
    return (
        '<div class="metric-card">'
        f'<div class="metric-label">{html.escape(label)}</div>'
        f'<div class="{value_class}">{arrow}{html.escape(value)}</div>'
        "</div>"
    )


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
# Shared building blocks
# --------------------------------------------------------------------------- #
def render_account_panel(client: AlpacaClient) -> None:
    account = client.get_account()
    pnl = account.daily_pnl_pct
    tone = "positive" if pnl >= 0 else "negative"

    col1, col2, col3, col4 = st.columns(4)
    col1.markdown(metric_card_html("Equity", f"${account.equity:,.2f}"), unsafe_allow_html=True)
    col2.markdown(metric_card_html("Cash", f"${account.cash:,.2f}"), unsafe_allow_html=True)
    col3.markdown(metric_card_html("Buying Power", f"${account.buying_power:,.2f}"), unsafe_allow_html=True)
    col4.markdown(metric_card_html("Daily P&L", f"{pnl:.2%}", tone=tone), unsafe_allow_html=True)

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
        # Only genuinely-populated fields are shown -- Alpaca returns None for
        # several of these on a fresh paper account (e.g. daytrade_count),
        # and a blank/"None" row would look broken rather than honest.
        kv_grid([(k, str(v)) for k, v in pairs if v is not None and v != ""])


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


def _position_stats_html(stats: list[tuple[str, str, str]]) -> str:
    cells = "".join(
        f'<div><div class="position-stat-label">{html.escape(label)}</div>'
        f'<div class="position-stat-value {tone}">{html.escape(value)}</div></div>'
        for label, value, tone in stats
    )
    return f'<div class="position-stats">{cells}</div>'


def render_stock_position_card(position: dict) -> None:
    symbol = position["symbol"]
    qty = position["qty"]
    entry = position["avg_entry_price"]
    current = position["current_price"]
    market_value = position["market_value"]
    upl = position["unrealized_pl"]
    uplpc = position["unrealized_plpc"]
    tone = "positive" if upl >= 0 else "negative"
    cost_basis = entry * qty

    opening = find_opening_decision(symbol)
    opened_str = opening.get("timestamp", "")[:10] if opening else "—"

    stats = [
        ("Cost Basis", f"${cost_basis:,.2f}", ""),
        ("Current Price", f"${current:,.2f}", ""),
        ("Market Value", f"${market_value:,.2f}", ""),
        ("Unrealized P&L", f"${upl:,.2f} ({uplpc:+.2%})", tone),
        ("Opened", opened_str, ""),
    ]
    card_html = f"""
    <div class="position-card">
      <div class="position-top">
        <div>
          <span class="position-symbol">{html.escape(symbol)}</span>
          <div class="position-sub">{qty:g} shares @ avg ${entry:,.2f}</div>
        </div>
        <div class="position-badges">
          <span class="position-badge long">LONG</span>
        </div>
      </div>
      {_position_stats_html(stats)}
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    if opening:
        with st.expander(f"View reasoning that opened this {symbol} position"):
            st.markdown(f"**Reasoning:** {opening.get('reasoning', '—')}")
            st.markdown(f"**Confidence:** {opening.get('confidence', 0):.2f}")
            st.markdown(f"**Risk note:** {opening.get('risk_note', '—')}")


def render_option_position_card(position: dict) -> None:
    contract_symbol = position["symbol"]
    underlying = position["underlying_symbol"]
    option_type = position["option_type"]
    strike = position["strike"]
    expiration = position["expiration"]
    qty = position["qty"]
    entry = position["avg_entry_price"]
    market_value = position["market_value"]
    upl = position["unrealized_pl"]
    uplpc = position.get("unrealized_plpc", 0.0)
    tone = "positive" if upl >= 0 else "negative"

    strategy_label = "Covered Call" if option_type == "call" else "Cash-Secured Put"
    credit_received = entry * 100 * abs(qty)
    cost_to_close = abs(market_value)

    stats = [
        ("Strike", f"${strike:,.2f}", ""),
        ("Expiration", expiration, ""),
        ("Credit Received", f"${credit_received:,.2f}", ""),
        ("Cost to Close (now)", f"${cost_to_close:,.2f}", ""),
        ("Unrealized P&L", f"${upl:,.2f} ({uplpc:+.2%})", tone),
    ]
    # Collateral is only a real, computed number for cash-secured puts (this
    # is the same formula options_trading_agent.py sums for the collateral
    # cap). A covered call's "backing" is the 100 shares themselves, not a
    # cash figure -- showing a fabricated dollar "max loss" for it would
    # misrepresent a strategy this app doesn't actually size that way.
    if option_type == "put":
        stats.insert(3, ("Collateral Committed", f"${strike * 100 * abs(qty):,.2f}", ""))
    else:
        stats.insert(3, ("Backed By", f"{int(100 * abs(qty))} shares", ""))

    opening = find_opening_decision(underlying, contract_symbol=contract_symbol)
    opened_str = opening.get("timestamp", "")[:10] if opening else "—"
    stats.append(("Opened", opened_str, ""))

    card_html = f"""
    <div class="position-card">
      <div class="position-top">
        <div>
          <span class="position-symbol">{html.escape(underlying)} {html.escape(option_type.upper())}</span>
          <div class="position-sub">{html.escape(contract_symbol)} · {abs(qty):g} contract(s)</div>
        </div>
        <div class="position-badges">
          <span class="position-badge sell">SELL (SHORT)</span>
          <span class="position-badge strategy">{html.escape(strategy_label)}</span>
        </div>
      </div>
      {_position_stats_html(stats)}
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    if opening:
        with st.expander(f"View reasoning that opened this {underlying} {strategy_label.lower()}"):
            st.markdown(f"**Reasoning:** {opening.get('reasoning', '—')}")
            st.markdown(f"**Confidence:** {opening.get('confidence', 0):.2f}")
            st.markdown(f"**Risk note:** {opening.get('risk_note', '—')}")


def render_decision_entry(d: dict) -> None:
    action = d.get("llm_action", "hold").upper()
    risk_approved = bool(d.get("risk_approved"))
    dry_run = bool(d.get("dry_run"))
    order_placed = d.get("order") is not None

    # "Executed" must mean a real order was placed -- risk_approved alone only
    # means the risk manager would have allowed it. In dry-run mode (or any
    # approved-but-unexecuted edge case) no order is ever submitted, so the
    # badge has to reflect order_placed, not just risk_approved.
    if order_placed:
        status_class, status_label = "executed", "EXECUTED"
    elif risk_approved and dry_run:
        status_class, status_label = "approved-dry", "APPROVED (DRY RUN — NO ORDER PLACED)"
    elif risk_approved:
        status_class, status_label = "approved-dry", "APPROVED (NO ORDER PLACED)"
    else:
        status_class, status_label = "blocked", "BLOCKED BY RISK MANAGER"

    symbol = html.escape(str(d.get("symbol", "")))
    contract = d.get("contract_symbol")
    contract_suffix = f" · {html.escape(str(contract))}" if contract else ""
    timestamp = html.escape(str(d.get("timestamp", ""))[:19])
    is_exit_engine = d.get("trigger") == "exit_engine"

    # Exit-engine closes are a deterministic rule, not an LLM call -- showing
    # a fabricated "confidence" score for those would misrepresent them.
    if is_exit_engine:
        meta_line = "Deterministic exit-engine rule (no LLM call)"
    else:
        confidence = d.get("confidence", 0) or 0
        meta_line = f"Confidence: {confidence:.2f}"

    reasoning = str(d.get("reasoning") or "—")
    reasoning_short = reasoning if len(reasoning) <= 220 else reasoning[:217] + "…"

    card_html = f"""
    <div class="decision-card {status_class}">
      <div class="decision-top">
        <span class="decision-badge {status_class}">{status_label}</span>
        <span class="decision-meta">{timestamp}</span>
      </div>
      <div class="decision-header">{symbol} — {html.escape(action)}{contract_suffix}</div>
      <div class="decision-meta">{html.escape(meta_line)}</div>
      <div class="decision-quote">&ldquo;{html.escape(reasoning_short)}&rdquo;</div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    with st.expander("View full reasoning, risk notes & indicators"):
        c1, c2 = st.columns([2, 1])
        with c1:
            st.markdown(f"**Reasoning:** {d.get('reasoning', '—')}")
            st.markdown(f"**Risk note:** {d.get('risk_note', '—')}")
            st.markdown(f"**Risk manager verdict:** {d.get('risk_reason', '—')}")
            if d.get("order"):
                st.markdown(f"**Order placed:** `{d['order']}`")
        with c2:
            if "covered_call_candidate" in d or "cash_secured_put_candidate" in d:
                st.json({
                    "covered_call_candidate": d.get("covered_call_candidate"),
                    "cash_secured_put_candidate": d.get("cash_secured_put_candidate"),
                })
            st.json(d.get("indicators", {}))


def render_options_decision_log() -> None:
    section_title("Options Decision Log", "🧠", level="sm")
    decisions = load_decisions()
    decisions = [d for d in decisions if d.get("asset_class") == "option"]
    if not decisions:
        brand_alert(
            "No options decisions logged yet. Run <code>python main.py options-run</code> "
            "or click 'Run options agent cycle now' above."
        )
        return

    decisions = list(reversed(decisions))  # newest first
    for d in decisions[:30]:
        if d.get("status") != "ok":
            continue
        render_decision_entry(d)


def render_price_chart(client: AlpacaClient, symbol: str) -> None:
    bars = client.get_bars(symbol, lookback_days=90)
    if bars.empty:
        st.warning(f"No bar data for {symbol}.")
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
        font=dict(color=ICE),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(gridcolor="rgba(202, 220, 252, 0.10)", linecolor="rgba(202, 220, 252, 0.15)"),
        yaxis=dict(gridcolor="rgba(202, 220, 252, 0.10)", linecolor="rgba(202, 220, 252, 0.15)"),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_status_strip(client: AlpacaClient) -> None:
    """Live connectivity badges -- each check is a real lightweight API call,
    not an assumption based on credentials merely being present."""
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

    backend = client.execution_backend.upper()
    is_paper = client.paper
    paper_class = "paper" if is_paper else "live-warning"
    paper_label = "PAPER ONLY" if is_paper else "⚠ LIVE TRADING — REAL MONEY"

    html_str = f"""
    <div class="status-strip">
      <span class="status-badge">
        <span class="status-dot {'ok' if api_ok else 'bad'}"></span>
        Alpaca API {'Connected' if api_ok else 'Unreachable'}
      </span>
      <span class="status-badge">
        <span class="status-dot {'ok' if data_ok else 'bad'}"></span>
        Market Data {'Reachable' if data_ok else 'Unreachable'}
      </span>
      <span class="status-badge">Execution: {html.escape(backend)}</span>
      <span class="status-badge {paper_class}">{html.escape(paper_label)}</span>
    </div>
    """
    st.markdown(html_str, unsafe_allow_html=True)


def render_performance_panel() -> None:
    trades = trade_history.load_trade_history()
    if not trades:
        brand_alert(
            "No closed trades yet — performance will populate once a position closes "
            "(via an LLM sell/close decision or the Exit Engine's stop-loss / take-profit rules)."
        )
        return

    stats = trade_history.compute_performance_stats(trades)
    pnl_tone = "positive" if stats["total_realized_pnl"] >= 0 else "negative"
    win_rate_str = f"{stats['win_rate']:.0%}" if stats["win_rate"] is not None else "—"

    col1, col2, col3, col4 = st.columns(4)
    col1.markdown(metric_card_html("Total Realized P&L", f"${stats['total_realized_pnl']:,.2f}", tone=pnl_tone),
                  unsafe_allow_html=True)
    col2.markdown(metric_card_html("Win Rate", win_rate_str), unsafe_allow_html=True)
    col3.markdown(metric_card_html("Winners / Losers", f"{stats['winning_trades']} / {stats['losing_trades']}"),
                  unsafe_allow_html=True)
    avg_tone = "positive" if stats["avg_pnl"] >= 0 else "negative"
    col4.markdown(metric_card_html("Avg P&L / Trade", f"${stats['avg_pnl']:,.2f}", tone=avg_tone),
                  unsafe_allow_html=True)

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
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_narrative_card() -> None:
    decisions = load_decisions()
    summary = summarize_latest_cycle(decisions)
    if summary is None:
        brand_alert("No cycles run yet — click 'Run agent cycle now' in the sidebar to start.")
        return

    kind_label = {"stock": "Stocks", "option": "Options", "exit_engine": "Exit Engine"}.get(summary.kind, summary.kind)
    timestamp = html.escape(summary.timestamp[:19])
    html_str = f"""
    <div class="narrative-card">
      <div class="narrative-top">
        <span class="narrative-label">Latest Cycle · {html.escape(kind_label)}</span>
        <span class="narrative-label">{timestamp}</span>
      </div>
      <div class="narrative-text">{html.escape(summary.narrative)}</div>
    </div>
    """
    st.markdown(html_str, unsafe_allow_html=True)


def render_activity_counters(client: AlpacaClient, open_positions_count: int) -> None:
    decisions = load_decisions()
    trades = trade_history.load_trade_history()

    try:
        created_at = client.get_account().created_at
    except Exception:
        created_at = None

    counters = compute_activity_counters(decisions, trades, open_positions_count, created_at)

    chips = [
        ("Days Live", "—" if counters.days_live is None else str(counters.days_live)),
        ("Cycles Run", str(counters.cycles_run)),
        ("Trades Filled", str(counters.trades_filled)),
        ("Open Positions", str(counters.open_positions)),
        ("Refused by Risk", str(counters.refused_by_risk)),
    ]
    chip_html = "".join(
        f'<div class="counter-chip"><div class="counter-value">{html.escape(v)}</div>'
        f'<div class="counter-label">{html.escape(l)}</div></div>'
        for l, v in chips
    )
    st.markdown(f'<div class="counter-row">{chip_html}</div>', unsafe_allow_html=True)


def render_equity_curve(client: AlpacaClient) -> None:
    range_label = st.radio(
        "Range", ["1D", "1W", "1M", "ALL"], index=2, horizontal=True,
        key="equity_curve_range", label_visibility="collapsed",
    )
    period_map = {"1D": "1D", "1W": "1W", "1M": "1M", "ALL": "all"}

    try:
        df = client.get_portfolio_history(period=period_map[range_label])
    except Exception as e:
        st.warning(f"Could not load portfolio history: {e}")
        return

    if df.empty or len(df) < 2:
        brand_alert(
            "Not enough equity history yet for this range — it will build up as the agent runs "
            "over the competition period."
        )
        if not df.empty:
            st.markdown(metric_card_html("Equity (latest)", f"${df['equity'].iloc[-1]:,.2f}"),
                        unsafe_allow_html=True)
        return

    current_equity = df["equity"].iloc[-1]
    start_equity = df["equity"].iloc[0]
    change_dollar = current_equity - start_equity
    change_pct = (change_dollar / start_equity) if start_equity else 0.0
    tone = "positive" if change_dollar >= 0 else "negative"

    c1, c2 = st.columns(2)
    c1.markdown(metric_card_html("Equity (Now)", f"${current_equity:,.2f}"), unsafe_allow_html=True)
    c2.markdown(
        metric_card_html(f"Change ({range_label})",
                          f"{'+' if change_dollar >= 0 else ''}${change_dollar:,.2f} ({change_pct:+.2%})",
                          tone=tone),
        unsafe_allow_html=True,
    )

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
        font=dict(color=ICE),
        xaxis=dict(gridcolor="rgba(202, 220, 252, 0.08)", linecolor="rgba(202, 220, 252, 0.15)"),
        yaxis=dict(gridcolor="rgba(202, 220, 252, 0.08)", linecolor="rgba(202, 220, 252, 0.15)", tickprefix="$"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    if df["equity"].nunique() <= 1:
        st.caption("Equity has been flat so far — no realized/unrealized P&L has moved it yet. "
                   "This will start to show real movement once positions open and close.")


def render_how_it_decides() -> None:
    steps = [
        ("01", "GATHER",
         "Pull live prices and technical indicators from Alpaca's Trading and Market Data APIs — "
         "for both stocks and live options chains (strikes, expirations, greeks, quotes)."),
        ("02", "REASON",
         "Gemini evaluates the data and proposes a structured buy/sell/hold (or options open/close) "
         "decision with a confidence score and a written rationale. It never sees or controls "
         "position sizing."),
        ("03", "GATE",
         "A fully deterministic risk manager — no LLM involved — independently re-verifies "
         "eligibility and either sizes the trade within hard limits or rejects it outright. A "
         "separate exit engine applies the same discipline to closing positions via stop-loss / "
         "take-profit rules."),
        ("04", "PUBLISH",
         "Every decision — approved or rejected, executed or not — is logged with its full "
         "reasoning and shown on this dashboard. Nothing is hidden after the fact."),
    ]
    cards = "".join(
        f'<div class="explainer-step">'
        f'<div class="step-num">{html.escape(n)}</div>'
        f'<div class="step-title">{html.escape(t)}</div>'
        f'<div class="step-body">{html.escape(b)}</div>'
        f'</div>'
        for n, t, b in steps
    )
    st.markdown(f'<div class="explainer-grid">{cards}</div>', unsafe_allow_html=True)


def render_footer() -> None:
    st.markdown(
        """
        <div class="app-footer">
          An autonomous, explainable paper-trading agent — Gemini reasons, a deterministic risk gate decides.<br/>
          Built for the Alpaca AI Trading Agents Hackathon
          (<a href="https://lablab.ai" target="_blank">lablab.ai</a>)
          · <a href="https://github.com/isianioui/Alpaca-AI-Trading-Agent" target="_blank">GitHub repo</a><br/>
          Paper trading only — no real capital at risk.
        </div>
        """,
        unsafe_allow_html=True,
    )


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
    if not stock_positions:
        brand_alert("No open stock positions.")
    else:
        for p in stock_positions:
            render_stock_position_card(p)

    section_divider()
    section_title(f"Option Positions ({len(option_positions)})", "🧾", level="md")
    if not option_positions:
        brand_alert("No open option positions.")
    else:
        for p in option_positions:
            render_option_position_card(p)

    section_divider()
    section_title("Price Chart", "📊", level="md")
    symbol = st.text_input("Symbol", value="AAPL", key="positions_chart_symbol").upper()
    if symbol:
        render_price_chart(client, symbol)


def render_view_options(client: AlpacaClient, options_client: OptionsClient, option_positions: list[dict]) -> None:
    page_header("Options", "Covered calls & cash-secured puts — the only two strategies this agent ever proposes.")

    section_title(f"Open Option Positions ({len(option_positions)})", "🧾", level="md")
    if not option_positions:
        brand_alert("No open option positions.")
    else:
        for p in option_positions:
            render_option_position_card(p)

    section_divider()
    section_title("Run Options Agent", "⚙️", level="md")
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

    section_divider()
    render_options_decision_log()


def render_view_decision_history() -> None:
    page_header("Decision History", "Every decision the agent has ever logged — approved, rejected, executed, or held.")

    all_decisions = [d for d in load_decisions() if d.get("record_type") != "exit_engine_summary"]
    if not all_decisions:
        brand_alert("No decisions logged yet. Run an agent cycle from the sidebar to start building history.")
        return

    symbols = sorted({d.get("symbol", "") for d in all_decisions if d.get("symbol")})
    c1, c2, c3 = st.columns([1.4, 1.2, 1.2])
    with c1:
        symbol_filter = st.selectbox("Symbol", ["All"] + symbols, key="dh_symbol_filter")
    with c2:
        status_filter = st.selectbox(
            "Outcome", ["All", "Executed", "Approved (dry run)", "Blocked", "Errors"], key="dh_status_filter")
    with c3:
        class_filter = st.selectbox("Type", ["All", "Stocks", "Options", "Exit Engine"], key="dh_class_filter")

    filtered = []
    for d in all_decisions:
        if symbol_filter != "All" and d.get("symbol") != symbol_filter:
            continue
        if class_filter == "Stocks" and (d.get("asset_class") == "option" or d.get("trigger") == "exit_engine"):
            continue
        if class_filter == "Options" and d.get("asset_class") != "option":
            continue
        if class_filter == "Exit Engine" and d.get("trigger") != "exit_engine":
            continue
        if status_filter == "Errors" and d.get("status") != "error":
            continue
        if status_filter != "Errors" and d.get("status") != "ok":
            continue
        if status_filter in ("Executed", "Approved (dry run)", "Blocked"):
            order_placed = d.get("order") is not None
            approved = bool(d.get("risk_approved"))
            if status_filter == "Executed" and not order_placed:
                continue
            if status_filter == "Approved (dry run)" and not (approved and not order_placed):
                continue
            if status_filter == "Blocked" and approved:
                continue
        filtered.append(d)

    filtered = list(reversed(filtered))  # newest first
    section_title(f"{len(filtered)} matching record(s)", level="sm")

    if not filtered:
        brand_alert("No decisions match the current filters.")
        return

    limit_key = "dh_row_limit"
    st.session_state.setdefault(limit_key, 30)
    limit = st.session_state[limit_key]

    for d in filtered[:limit]:
        if d.get("status") == "error":
            st.warning(f"**{d.get('symbol', '?')}** — error: {d.get('reason', 'unknown error')} "
                       f"({str(d.get('timestamp', ''))[:19]})")
            continue
        render_decision_entry(d)

    if limit < len(filtered):
        if st.button(f"Load 30 more (showing {min(limit, len(filtered))} of {len(filtered)})", key="dh_load_more"):
            st.session_state[limit_key] += 30
            st.rerun()


def render_view_strategy() -> None:
    page_header("Strategy", "How this agent actually decides — the real pipeline, step by step.")
    render_how_it_decides()


def render_view_performance() -> None:
    page_header("Performance", "Realized P&L from every position that has actually closed.")
    render_performance_panel()


def render_view_docs(client: AlpacaClient) -> None:
    page_header("Docs", "What this project is, and where to find it.")

    st.markdown(
        "An autonomous, explainable paper-trading agent built for the Alpaca AI Trading Agents "
        "Hackathon (lablab.ai). Gemini reasons over live Alpaca market data and technical "
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
        ("Gemini Model (GEMINI_MODEL)", os.getenv("GEMINI_MODEL", "gemini-2.5-flash (default)")),
    ])


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
        with st.spinner("Agent is analyzing the market and consulting Gemini..."):
            agent = TradingAgent(alpaca_client=client, dry_run=dry_run)
            agent.run_cycle(symbols)
        st.sidebar.success("Cycle complete.")
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

    st.markdown('<div class="app-title">🦙 Alpaca AI Trading Agent</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="app-subtitle">An autonomous, Gemini-powered paper trading agent '
        'built on Alpaca\'s Trading API.</div>',
        unsafe_allow_html=True,
    )

    try:
        client = get_client()
        options_client = get_options_client()
    except ValueError as e:
        st.error(str(e))
        st.info("Copy `.env.example` to `.env` and fill in your Alpaca + Google Gemini API keys.")
        return

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
        render_sidebar_nav(open_positions_count)
        render_sidebar_run_controls(client, options_client)

    view = st.session_state["active_view"]
    if view == "Overview":
        render_view_overview(client, open_positions_count)
    elif view == "Positions":
        render_view_positions(stock_positions, option_positions, client)
    elif view == "Options":
        render_view_options(client, options_client, option_positions)
    elif view == "Decision History":
        render_view_decision_history()
    elif view == "Performance":
        render_view_performance()
    elif view == "Strategy":
        render_view_strategy()
    elif view == "Docs":
        render_view_docs(client)
    elif view == "Settings":
        render_view_settings(client, options_client)

    render_footer()


if __name__ == "__main__":
    main()
