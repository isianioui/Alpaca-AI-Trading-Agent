"""
Custom-coded HTML/CSS/JS presentation layer for the dashboard, rendered via
st.components.v1.html (real sandboxed iframes) rather than CSS overrides on
Streamlit's own widget classes.

Every function here takes plain data (dicts/lists already resolved by
dashboard.py -- no log-scanning or API calls happen in this module) and
returns an (html, height) tuple: a complete, self-contained HTML document
string plus the pixel height dashboard.py should pass to
st.components.v1.html(..., height=height, scrolling=False).

Why an explicit height: st.components.v1.html has no auto-resize bridge
(that's only wired up for full custom components declared via
components.declare_component) -- the iframe is a fixed-size box, so height
has to be computed from the data being rendered (row/card counts) rather
than guessed. Every render_* function below sizes for the worst case it can
produce (e.g. every expandable detail panel open) so nothing clips; the
outer Streamlit page scrolls normally around a correctly-sized iframe
rather than the iframe scrolling internally.

Iframe bodies are transparent (not painted navy) so they sit seamlessly on
the app's own navy background with no visible seam -- individual cards
carry their own navy-mid surface color.
"""

from __future__ import annotations

import html as _html
from typing import Optional

NAVY = "#0A0E1E"
NAVY_MID = "#141A34"
ICE = "#A9B4F5"
GREEN = "#34D399"
RED = "#FB7185"
OFFWHITE = "#F3F5FF"
MUTED_ON_DARK = "#7C86AC"
ACCENT = "#7C6FF0"
ACCENT2 = "#22D3EE"
GRADIENT = f"linear-gradient(135deg, {ACCENT} 0%, {ACCENT2} 100%)"

FONT_LINKS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
"""

BASE_STYLE = f"""
* {{ box-sizing: border-box; }}
html, body {{
  margin: 0; padding: 0; background: transparent;
  font-family: 'Inter', -apple-system, sans-serif;
  color: {OFFWHITE};
}}
.num {{ font-family: 'JetBrains Mono', 'Courier New', monospace; font-variant-numeric: tabular-nums; }}
::-webkit-scrollbar {{ width: 8px; height: 8px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: rgba(124, 111, 240, 0.30); border-radius: 8px; }}
::-webkit-scrollbar-thumb:hover {{ background: rgba(124, 111, 240, 0.5); }}
* {{ scrollbar-width: thin; scrollbar-color: rgba(124, 111, 240, 0.30) transparent; }}

.grid-motif {{
  background-image:
    linear-gradient(rgba(169, 180, 245, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(169, 180, 245, 0.045) 1px, transparent 1px);
  background-size: 22px 22px;
}}

@keyframes fadeInUp {{
  from {{ opacity: 0; transform: translateY(8px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
@keyframes glowPulse {{
  0%, 100% {{ box-shadow: 0 0 0 rgba(124, 111, 240, 0); }}
  50% {{ box-shadow: 0 0 14px rgba(124, 111, 240, 0.35); }}
}}
.animate-in {{ animation: fadeInUp 0.45s ease-out both; }}

.tone-positive {{ color: {GREEN}; }}
.tone-negative {{ color: {RED}; }}
.tone-neutral {{ color: {OFFWHITE}; }}
"""

COUNT_UP_JS = """
<script>
function fmtStat(v, format) {
  if (format === 'currency') {
    var sign = v < 0 ? '-' : '';
    return sign + '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  }
  if (format === 'percent') return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  if (format === 'integer') return Math.round(v).toLocaleString('en-US');
  return String(v);
}
function animateStat(el) {
  var target = parseFloat(el.getAttribute('data-target'));
  var format = el.getAttribute('data-format') || 'plain';
  if (isNaN(target)) return;
  var duration = 850;
  var t0 = null;
  function step(ts) {
    if (!t0) t0 = ts;
    var p = Math.min((ts - t0) / duration, 1);
    var eased = 1 - Math.pow(1 - p, 3);
    el.textContent = fmtStat(target * eased, format);
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
document.querySelectorAll('.stat-value[data-target]').forEach(animateStat);
</script>
"""

TOGGLE_JS = """
<script>
function toggleCard(id) {
  var detail = document.getElementById(id);
  var trigger = document.getElementById(id + '-trigger');
  if (!detail) return;
  var isOpen = detail.classList.toggle('open');
  if (trigger) trigger.classList.toggle('open', isOpen);
}
</script>
"""


def _esc(value) -> str:
    if value is None:
        return ""
    return _html.escape(str(value))


def _doc(body: str, extra_style: str = "", extra_script: str = "") -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
{FONT_LINKS}
<style>{BASE_STYLE}{extra_style}</style>
</head><body>
{body}
{extra_script}
</body></html>"""


def _empty_state(message: str) -> str:
    return (
        f'<div class="brand-alert animate-in" style="background:{NAVY_MID};'
        f'border:1px solid rgba(124,111,240,0.18);border-left:3px solid {ICE};'
        f'border-radius:10px;padding:0.9rem 1.1rem;color:{ICE};font-size:0.92rem;">'
        f'{message}</div>'
    )


# --------------------------------------------------------------------------- #
# Stat cards (primary account metrics + activity counter chips)
# --------------------------------------------------------------------------- #
_STAT_CARD_STYLE = f"""
.stat-row {{ display: flex; gap: 0.85rem; flex-wrap: wrap; }}
.stat-card {{
  position: relative; overflow: hidden;
  flex: 1 1 160px; min-width: 150px;
  background: linear-gradient(160deg, rgba(20,26,52,0.92) 0%, rgba(14,18,38,0.75) 100%);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(124, 111, 240, 0.16);
  border-radius: 16px; padding: 1rem 1.15rem;
  box-shadow: 0 4px 18px rgba(0,0,0,0.32), inset 0 1px 0 rgba(255,255,255,0.03);
  transition: transform 0.25s cubic-bezier(.2,.8,.2,1), box-shadow 0.25s ease, border-color 0.25s ease;
}}
.stat-card::before {{
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: {GRADIENT}; opacity: 0.9;
}}
.stat-card:hover {{
  transform: translateY(-3px);
  border-color: rgba(124, 111, 240, 0.4);
  box-shadow: 0 12px 28px rgba(0,0,0,0.4), 0 0 0 1px rgba(124, 111, 240, 0.12);
}}
.stat-label {{
  font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: {MUTED_ON_DARK}; margin-bottom: 0.4rem;
}}
.stat-value {{ font-size: 1.55rem; font-weight: 700; line-height: 1.15; }}
.chip-row {{ display: flex; gap: 0.6rem; flex-wrap: wrap; }}
.chip {{
  position: relative; overflow: hidden;
  background: rgba(20, 26, 52, 0.65);
  border: 1px solid rgba(124, 111, 240, 0.14);
  border-radius: 12px; padding: 0.55rem 1rem; min-width: 108px;
  transition: transform 0.2s ease, border-color 0.2s ease;
}}
.chip:hover {{ transform: translateY(-2px); border-color: rgba(124, 111, 240, 0.35); }}
.chip .stat-label {{ font-size: 0.64rem; margin-bottom: 2px; }}
.chip .stat-value {{ font-size: 1.1rem; }}
"""


def render_stat_cards(cards: list[dict]) -> tuple[str, int]:
    """cards: [{"label", "display", "raw", "format", "tone"}]"""
    items = []
    for c in cards:
        tone = c.get("tone", "neutral")
        raw = c.get("raw")
        target_attr = f' data-target="{raw}" data-format="{c.get("format", "plain")}"' if raw is not None else ""
        items.append(
            f'<div class="stat-card animate-in">'
            f'<div class="stat-label">{_esc(c["label"])}</div>'
            f'<div class="stat-value num tone-{tone}"{target_attr}>{_esc(c["display"])}</div>'
            f'</div>'
        )
    body = f'<div class="stat-row">{"".join(items)}</div>'
    return _doc(body, _STAT_CARD_STYLE, COUNT_UP_JS), 145


def render_counter_chips(chips: list[dict]) -> tuple[str, int]:
    """chips: [{"label", "display", "raw", "format"}]. data-target sits on the
    value div itself; the div's initial textContent is the pre-formatted
    display string, so it's still correct even if JS somehow doesn't run."""
    items = []
    for c in chips:
        raw = c.get("raw")
        target_attr = f' data-target="{raw}" data-format="{c.get("format", "integer")}"' if raw is not None else ""
        items.append(
            f'<div class="chip animate-in">'
            f'<div class="stat-label">{_esc(c["label"])}</div>'
            f'<div class="stat-value num"{target_attr}>{_esc(c["display"])}</div>'
            f'</div>'
        )
    body = f'<div class="chip-row">{"".join(items)}</div>'
    return _doc(body, _STAT_CARD_STYLE, COUNT_UP_JS), 92


# --------------------------------------------------------------------------- #
# Status strip (with a pulsing "live" dot for market state)
# --------------------------------------------------------------------------- #
_STATUS_STYLE = f"""
.status-row {{ display: flex; gap: 0.6rem; flex-wrap: wrap; align-items: center; }}
.status-pill {{
  display: inline-flex; align-items: center; gap: 0.45rem;
  font-size: 0.76rem; font-weight: 600; padding: 0.34rem 0.8rem;
  border-radius: 999px; background: rgba(20, 26, 52, 0.7);
  border: 1px solid rgba(124, 111, 240, 0.18); color: {OFFWHITE};
  transition: border-color 0.2s ease, transform 0.2s ease;
}}
.status-pill:hover {{ border-color: rgba(124, 111, 240, 0.4); transform: translateY(-1px); }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
.dot.ok {{ background: {GREEN}; }}
.dot.bad {{ background: {RED}; }}
.dot.idle {{ background: {MUTED_ON_DARK}; }}
.dot.live {{
  background: {GREEN};
  animation: pulse-ring 1.8s cubic-bezier(0.4,0,0.6,1) infinite;
}}
@keyframes pulse-ring {{
  0% {{ box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55); }}
  70% {{ box-shadow: 0 0 0 7px rgba(52, 211, 153, 0); }}
  100% {{ box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }}
}}
.status-pill.paper {{ background: rgba(52,211,153,0.12); border-color: rgba(52,211,153,0.4); color: {GREEN}; font-weight: 800; }}
.status-pill.live-warning {{ background: rgba(251,113,133,0.18); border-color: rgba(251,113,133,0.55); color: {RED}; font-weight: 800; }}
"""


def render_status_strip(
    api_ok: bool, data_ok: bool, market_open: Optional[bool], backend: str, is_paper: bool,
) -> tuple[str, int]:
    pills = [
        f'<span class="status-pill"><span class="dot {"ok" if api_ok else "bad"}"></span>'
        f'Alpaca API {"Connected" if api_ok else "Unreachable"}</span>',
        f'<span class="status-pill"><span class="dot {"ok" if data_ok else "bad"}"></span>'
        f'Market Data {"Reachable" if data_ok else "Unreachable"}</span>',
    ]
    if market_open is not None:
        dot_class = "live" if market_open else "idle"
        pills.append(
            f'<span class="status-pill"><span class="dot {dot_class}"></span>'
            f'Market {"Open" if market_open else "Closed"}</span>'
        )
    pills.append(f'<span class="status-pill">Execution: {_esc(backend.upper())}</span>')
    paper_class = "paper" if is_paper else "live-warning"
    paper_label = "PAPER ONLY" if is_paper else "⚠ LIVE TRADING — REAL MONEY"
    pills.append(f'<span class="status-pill {paper_class}">{_esc(paper_label)}</span>')

    body = f'<div class="status-row animate-in">{"".join(pills)}</div>'
    return _doc(body, _STATUS_STYLE), 48


# --------------------------------------------------------------------------- #
# Latest Cycle narrative card
# --------------------------------------------------------------------------- #
_NARRATIVE_STYLE = f"""
.narrative-card {{
  position: relative; overflow: hidden;
  background: linear-gradient(160deg, rgba(20,26,52,0.92) 0%, rgba(14,18,38,0.75) 100%);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(124, 111, 240, 0.16);
  border-radius: 16px; padding: 1.1rem 1.3rem;
  box-shadow: 0 4px 18px rgba(0,0,0,0.32), inset 0 1px 0 rgba(255,255,255,0.03);
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}}
.narrative-card::before {{
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: {GRADIENT}; opacity: 0.9;
}}
.narrative-card:hover {{ border-color: rgba(124, 111, 240, 0.35); box-shadow: 0 10px 26px rgba(0,0,0,0.4); }}
.narrative-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; gap: 0.3rem; }}
.narrative-label {{ font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: {MUTED_ON_DARK}; }}
.narrative-label.ts {{ font-family: 'JetBrains Mono', monospace; }}
.narrative-text {{ font-size: 0.98rem; color: {OFFWHITE}; line-height: 1.55; }}
"""


def render_narrative_card(kind_label: Optional[str], timestamp: Optional[str], narrative: Optional[str]) -> tuple[str, int]:
    if narrative is None:
        body = _empty_state("No cycles run yet — click 'Run agent cycle now' in the sidebar to start.")
        return _doc(body, _NARRATIVE_STYLE), 70

    body = f"""
    <div class="narrative-card animate-in">
      <div class="narrative-top">
        <span class="narrative-label">Latest Cycle &middot; {_esc(kind_label)}</span>
        <span class="narrative-label ts">{_esc(timestamp)}</span>
      </div>
      <div class="narrative-text">{_esc(narrative)}</div>
    </div>
    """
    lines = max(1, len(narrative) // 90 + 1)
    return _doc(body, _NARRATIVE_STYLE), 78 + lines * 22


# --------------------------------------------------------------------------- #
# Position cards
# --------------------------------------------------------------------------- #
_POSITION_STYLE = f"""
.position-card {{
  background: linear-gradient(160deg, rgba(20,26,52,0.9) 0%, rgba(14,18,38,0.7) 100%);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(124, 111, 240, 0.14);
  border-left: 4px solid {MUTED_ON_DARK};
  border-radius: 16px; padding: 1rem 1.2rem; margin-bottom: 0.7rem;
  box-shadow: 0 3px 14px rgba(0,0,0,0.26);
  transition: transform 0.22s cubic-bezier(.2,.8,.2,1), box-shadow 0.22s ease, border-color 0.22s ease;
}}
.position-card:hover {{ transform: translateY(-3px); border-color: rgba(124,111,240,0.3); box-shadow: 0 10px 26px rgba(0,0,0,0.36), 0 0 20px rgba(124,111,240,0.10); }}
.position-card.long {{ border-left-color: {GREEN}; }}
.position-card.sell {{ border-left-color: {ICE}; }}
.position-top {{ display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.7rem; }}
.position-symbol {{ font-size: 1.12rem; font-weight: 800; color: {OFFWHITE}; }}
.position-sub {{ font-size: 0.78rem; color: {MUTED_ON_DARK}; margin-top: 2px; font-family: 'JetBrains Mono', monospace; }}
.position-badges {{ display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap; }}
.position-badge {{ font-size: 0.66rem; font-weight: 800; letter-spacing: 0.05em; text-transform: uppercase; padding: 0.18rem 0.6rem; border-radius: 999px; white-space: nowrap; }}
.position-badge.long {{ background: rgba(52,211,153,0.15); color: {GREEN}; border: 1px solid rgba(52,211,153,0.4); }}
.position-badge.sell {{ background: rgba(169,180,245,0.14); color: {ICE}; border: 1px solid rgba(169,180,245,0.4); }}
.position-badge.strategy {{ background: rgba(20,26,52,0.9); color: {ICE}; border: 1px solid rgba(124,111,240,0.2); }}
.position-stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 0.7rem 0.9rem; }}
.position-stat-label {{ font-size: 0.63rem; color: {MUTED_ON_DARK}; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 2px; }}
.position-stat-value {{ font-size: 0.94rem; font-weight: 700; }}
.card-toggle {{
  margin-top: 0.7rem; font-size: 0.78rem; color: {ACCENT2}; cursor: pointer;
  user-select: none; opacity: 0.85; transition: opacity 0.15s ease, transform 0.15s ease;
}}
.card-toggle:hover {{ opacity: 1; transform: translateX(2px); }}
.card-toggle::before {{ content: '\\25B8  '; display: inline-block; transition: transform 0.2s ease; }}
.card-toggle.open::before {{ transform: rotate(90deg); }}
.card-detail {{
  max-height: 0; overflow: hidden; transition: max-height 0.3s ease;
  font-size: 0.85rem; color: {ICE}; line-height: 1.55;
}}
.card-detail.open {{ max-height: 3000px; padding-top: 0.6rem; }}
.card-detail b {{ color: {OFFWHITE}; }}
.scroll-panel {{ overflow-y: auto; padding-right: 6px; }}
"""

_POSITION_CARD_H = 158    # one collapsed card, incl. margin
_POSITION_DETAIL_H = 122  # extra height one expanded detail panel adds
_POSITION_CAP = 620       # hard cap -- beyond this, the panel scrolls internally


def render_stock_position_cards(positions: list[dict]) -> tuple[str, int]:
    """positions: list of dicts with symbol, qty, entry, current, market_value,
    upl, uplpc, opened, reasoning, confidence, risk_note (reasoning may be None
    if no matching opening decision was found in the log).

    Sizing: the panel is tall enough to show every card collapsed AND one
    card fully expanded without scrolling (the common case -- a viewer
    expands one card at a time), capped at _POSITION_CAP; beyond that it
    scrolls internally (custom-styled scrollbar) rather than ever clipping
    an expanded card or leaving a large dead-space gap when collapsed.
    """
    if not positions:
        body = _empty_state("No open stock positions.")
        return _doc(body, _POSITION_STYLE), 70

    cards = []
    for i, p in enumerate(positions):
        tone = "positive" if p["upl"] >= 0 else "negative"
        card_id = f"stock-{i}"
        has_reasoning = bool(p.get("reasoning"))

        stats = [
            ("Cost Basis", f"${p['entry'] * p['qty']:,.2f}", ""),
            ("Current Price", f"${p['current']:,.2f}", ""),
            ("Market Value", f"${p['market_value']:,.2f}", ""),
            ("Unrealized P&L", f"${p['upl']:,.2f} ({p['uplpc']:+.2%})", f"tone-{tone}"),
            ("Opened", p.get("opened") or "—", ""),
        ]
        stat_html = "".join(
            f'<div><div class="position-stat-label">{_esc(l)}</div>'
            f'<div class="position-stat-value num {t}">{_esc(v)}</div></div>'
            for l, v, t in stats
        )

        toggle_html = ""
        if has_reasoning:
            toggle_html = (
                f'<div class="card-toggle" id="{card_id}-trigger" onclick="toggleCard(\'{card_id}\')">'
                f'View reasoning that opened this position</div>'
                f'<div class="card-detail" id="{card_id}">'
                f'<div><b>Reasoning:</b> {_esc(p["reasoning"])}</div>'
                f'<div style="margin-top:4px;"><b>Confidence:</b> {p.get("confidence", 0):.2f}</div>'
                f'<div style="margin-top:4px;"><b>Risk note:</b> {_esc(p.get("risk_note") or "—")}</div>'
                f'</div>'
            )

        cards.append(f"""
        <div class="position-card long animate-in" style="animation-delay:{i * 50}ms">
          <div class="position-top">
            <div>
              <span class="position-symbol">{_esc(p['symbol'])}</span>
              <div class="position-sub">{p['qty']:g} shares @ avg ${p['entry']:,.2f}</div>
            </div>
            <div class="position-badges"><span class="position-badge long">LONG</span></div>
          </div>
          <div class="position-stats">{stat_html}</div>
          {toggle_html}
        </div>
        """)

    collapsed_total = len(positions) * _POSITION_CARD_H
    container_h = min(_POSITION_CAP, collapsed_total + _POSITION_DETAIL_H)
    body = f'<div class="scroll-panel" style="max-height:{container_h}px;">{"".join(cards)}</div>'
    return _doc(body, _POSITION_STYLE, TOGGLE_JS), container_h + 16


# --------------------------------------------------------------------------- #
# Open options positions -- dense trading-blotter table with an inline SVG
# payoff-at-expiry sparkline per row. Replaces the old card layout: this
# project only ever writes single-leg, short options (covered call,
# cash-secured put), so the table is shaped around exactly those two rows
# of math, not a generic multi-leg spread format.
# --------------------------------------------------------------------------- #
def _interp_pnl(points: list[tuple[float, float]], x: float) -> float:
    """Linear interpolation so the 'now' marker on the sparkline sits at the
    live underlying price even when that price doesn't land exactly on one
    of payoff_points()'s grid points."""
    pts = sorted(points)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return pts[-1][1]


def _svg_pts(pts: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def _build_payoff_svg(
    points: list[tuple[float, float]], current_price: float, breakeven_price: float,
    w: int = 172, h: int = 52, pad: float = 5,
) -> str:
    """Compact sparkline-style payoff-at-expiry chart: profit/loss shaded
    green/red on either side of the breakeven crossing, a dashed zero-line,
    and a 'now' marker at the live underlying price."""
    prices = [p for p, _ in points]
    pnls = [v for _, v in points]
    pmin, pmax = min(prices), max(prices)
    vmin, vmax = min(pnls), max(pnls)
    if vmin == vmax:
        vmin, vmax = vmin - 1, vmax + 1
    if pmin == pmax:
        pmin, pmax = pmin - 1, pmax + 1

    def xf(p: float) -> float:
        return pad + (p - pmin) / (pmax - pmin) * (w - 2 * pad)

    def yf(v: float) -> float:
        return h - pad - (v - vmin) / (vmax - vmin) * (h - 2 * pad)

    baseline_y = yf(0)
    curve = [(xf(p), yf(v)) for p, v in points]
    neg = [(xf(p), yf(v)) for p, v in points if v <= 0]
    pos = [(xf(p), yf(v)) for p, v in points if v >= 0]

    parts = [
        f'<line x1="{pad}" y1="{baseline_y:.1f}" x2="{w - pad}" y2="{baseline_y:.1f}" '
        f'stroke="rgba(169,180,245,0.28)" stroke-width="1" stroke-dasharray="2,2"/>'
    ]
    if len(neg) >= 2:
        poly = [(neg[0][0], baseline_y)] + neg + [(neg[-1][0], baseline_y)]
        parts.append(f'<polygon points="{_svg_pts(poly)}" fill="rgba(251,113,133,0.30)"/>')
    if len(pos) >= 2:
        poly = [(pos[0][0], baseline_y)] + pos + [(pos[-1][0], baseline_y)]
        parts.append(f'<polygon points="{_svg_pts(poly)}" fill="rgba(52,211,153,0.26)"/>')
    parts.append(
        f'<polyline points="{_svg_pts(curve)}" fill="none" stroke="{OFFWHITE}" '
        f'stroke-width="1.5" stroke-linejoin="round"/>'
    )

    now_price = max(pmin, min(pmax, current_price))
    now_x = xf(now_price)
    now_y = yf(_interp_pnl(points, now_price))
    parts.append(
        f'<line x1="{now_x:.1f}" y1="{pad}" x2="{now_x:.1f}" y2="{h - pad}" '
        f'stroke="{ICE}" stroke-width="1" stroke-dasharray="2,2"/>'
    )
    parts.append(f'<circle cx="{now_x:.1f}" cy="{now_y:.1f}" r="2.3" fill="{ICE}"/>')

    if pmin <= breakeven_price <= pmax:
        be_x = xf(breakeven_price)
        parts.append(
            f'<line x1="{be_x:.1f}" y1="{baseline_y - 3:.1f}" x2="{be_x:.1f}" y2="{baseline_y + 3:.1f}" '
            f'stroke="{MUTED_ON_DARK}" stroke-width="1.5"/>'
        )

    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" class="payoff-spark">{"".join(parts)}</svg>'


_OPT_TABLE_STYLE = f"""
.opt-table {{ font-size: 0.8rem; min-width: 1040px; }}
.opt-head, .opt-row {{
  display: grid;
  grid-template-columns: 2fr 116px 128px 110px 100px 104px 108px 188px;
  gap: 0.7rem; align-items: center;
}}
.opt-head {{
  padding: 0 0.9rem 0.55rem 0.9rem; color: {MUTED_ON_DARK};
  font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700;
  border-bottom: 1px solid rgba(124,111,240,0.16); position: sticky; top: 0;
  background: {NAVY}; z-index: 1;
}}
.opt-row {{
  padding: 0.7rem 0.9rem; border-radius: 12px; margin-bottom: 6px;
  background: rgba(20, 26, 52, 0.55);
  border: 1px solid rgba(124,111,240,0.10); border-left: 3px solid {ICE};
  box-shadow: 0 2px 10px rgba(0,0,0,0.2);
  transition: transform 0.18s ease, border-color 0.18s ease;
}}
.opt-row:hover {{ transform: translateY(-2px); }}
.opt-cell.position {{ display: flex; flex-direction: column; gap: 3px; min-width: 0; }}
.opt-symbol-row {{ display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap; }}
.opt-symbol {{ font-weight: 800; color: {OFFWHITE}; font-size: 0.94rem; }}
.opt-badge {{ font-size: 0.58rem; font-weight: 800; letter-spacing: 0.05em; text-transform: uppercase; padding: 0.1rem 0.45rem; border-radius: 999px; white-space: nowrap; }}
.opt-badge.filled {{ background: rgba(52,211,153,0.15); color: {GREEN}; border: 1px solid rgba(52,211,153,0.4); }}
.opt-strategy {{ font-size: 0.74rem; color: {MUTED_ON_DARK}; }}
.opt-note {{ font-size: 0.72rem; color: {ICE}; opacity: 0.9; }}
.opt-cell.strike {{ display: flex; flex-direction: column; gap: 4px; }}
.opt-sell-badge {{
  display: inline-block; font-size: 0.56rem; font-weight: 800; letter-spacing: 0.04em;
  text-transform: uppercase; padding: 0.08rem 0.4rem; border-radius: 999px; width: fit-content;
  background: rgba(169,180,245,0.14); color: {ICE}; border: 1px solid rgba(169,180,245,0.4);
}}
.opt-cell.room {{ display: flex; flex-direction: column; gap: 2px; }}
.opt-cell.room .pct {{ font-size: 0.7rem; color: {MUTED_ON_DARK}; }}
.opt-cell.expires {{ display: flex; flex-direction: column; gap: 2px; }}
.opt-cell.expires .date {{ font-size: 0.7rem; color: {MUTED_ON_DARK}; }}
.opt-cell.num {{ font-weight: 700; }}
.opt-toggle-row {{
  padding: 0.3rem 0.9rem 0; cursor: pointer; font-size: 0.74rem; color: {ACCENT2};
  opacity: 0.85; user-select: none; transition: opacity 0.15s ease;
}}
.opt-toggle-row:hover {{ opacity: 1; }}
.opt-toggle-row::before {{ content: '\\25B8  AI reasoning that opened this position'; display: inline-block; }}
.opt-toggle-row.open::before {{ content: '\\25BE  AI reasoning that opened this position'; }}
.opt-detail {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease; font-size: 0.82rem; color: {ICE}; line-height: 1.55; margin: 0 0.9rem 0.4rem 0.9rem; }}
.opt-detail.open {{ max-height: 2000px; padding-top: 0.5rem; }}
.opt-detail b {{ color: {OFFWHITE}; }}
.payoff-spark {{ display: block; }}
.opt-pending {{ font-size: 0.72rem; color: {MUTED_ON_DARK}; font-style: italic; }}
"""

_OPT_ROW_H = 112
_OPT_DETAIL_H = 130
_OPT_TABLE_CAP = 780


def render_option_positions_table(rows: list[dict]) -> tuple[str, int]:
    """rows: list of dicts, one per open option position -- all math (payoff
    points, max loss, room to strike) is computed by dashboard.py from live
    Alpaca data and src/payoff.py's pure functions; this function only lays
    out already-shaped values, never computes risk figures itself:

      symbol, contract_symbol, status_label, strategy_label, note, strike,
      option_type, dte, expiration, collected, unrealized, reasoning,
      confidence, risk_note, current_price, room_dollar, room_pct,
      max_loss, breakeven, payoff_points (list[(price, pnl)] or None if the
      underlying price / cost basis needed to compute it isn't available --
      rendered as an honest 'pending' note, never a fabricated chart).
    """
    if not rows:
        body = _empty_state("No open option positions.")
        return _doc(body, _OPT_TABLE_STYLE), 70

    header = (
        '<div class="opt-head"><div>POSITION</div><div>STRIKE</div>'
        '<div>ROOM TO STRIKE</div><div>EXPIRES</div><div>COLLECTED</div>'
        '<div>MAX LOSS</div><div>UNREALIZED</div><div>PAYOFF AT EXPIRY</div></div>'
    )
    blocks = [header]
    for i, r in enumerate(rows):
        row_id = f"opt-row-{i}"
        tone = "positive" if r["unrealized"] >= 0 else "negative"
        unrealized_sign = "-" if r["unrealized"] < 0 else ""

        if r.get("room_dollar") is not None:
            room_tone = "positive" if r["room_dollar"] >= 0 else "negative"
            room_html = (
                f'<div class="num tone-{room_tone}">{"+" if r["room_dollar"] >= 0 else "-"}'
                f'${abs(r["room_dollar"]):,.2f}</div><div class="pct">{r["room_pct"]:+.1%}</div>'
            )
        else:
            room_html = '<div class="opt-pending">pending live quote</div>'

        if r.get("max_loss") is not None:
            max_loss_html = f'<div class="num">${r["max_loss"]:,.2f}</div>'
        else:
            max_loss_html = '<div class="opt-pending">pending</div>'

        if r.get("payoff_points"):
            payoff_html = _build_payoff_svg(r["payoff_points"], r["current_price"], r["breakeven"])
        else:
            payoff_html = '<div class="opt-pending">Not enough live data to chart yet.</div>'

        has_reasoning = bool(r.get("reasoning"))
        toggle_html = ""
        detail_html = ""
        if has_reasoning:
            toggle_html = f'<div class="opt-toggle-row" id="{row_id}-trigger" onclick="toggleCard(\'{row_id}\')"></div>'
            detail_html = (
                f'<div class="opt-detail" id="{row_id}">'
                f'<div><b>Reasoning:</b> {_esc(r["reasoning"])}</div>'
                f'<div style="margin-top:4px;"><b>Confidence:</b> {r.get("confidence", 0):.2f}</div>'
                f'<div style="margin-top:4px;"><b>Risk note:</b> {_esc(r.get("risk_note") or "—")}</div>'
                f'</div>'
            )

        blocks.append(f"""
        <div class="opt-row animate-in" style="animation-delay:{min(i, 10) * 40}ms">
          <div class="opt-cell position">
            <div class="opt-symbol-row">
              <span class="opt-symbol">{_esc(r['symbol'])}</span>
              <span class="opt-badge filled">{_esc(r['status_label'])}</span>
            </div>
            <div class="opt-strategy">{_esc(r['strategy_label'])} &middot; {_esc(r['contract_symbol'])}</div>
            <div class="opt-note">{_esc(r['note'])}</div>
          </div>
          <div class="opt-cell strike">
            <div class="num">${r['strike']:,.2f} {_esc(r['option_type'].upper())}</div>
            <span class="opt-sell-badge">SELL</span>
          </div>
          <div class="opt-cell room">{room_html}</div>
          <div class="opt-cell expires">
            <div class="num">{r['dte']}d</div>
            <div class="date">{_esc(r['expiration'])}</div>
          </div>
          <div class="opt-cell num">${r['collected']:,.2f}</div>
          <div class="opt-cell maxloss">{max_loss_html}</div>
          <div class="opt-cell num tone-{tone}">{unrealized_sign}${abs(r['unrealized']):,.2f}</div>
          <div class="opt-cell payoff">{payoff_html}</div>
        </div>
        {toggle_html}
        {detail_html}
        """)

    collapsed_total = 46 + len(rows) * _OPT_ROW_H
    container_h = min(_OPT_TABLE_CAP, collapsed_total + _OPT_DETAIL_H)
    body = (
        f'<div class="opt-table scroll-panel" style="max-height:{container_h}px; overflow-x:auto;">'
        f'{"".join(blocks)}</div>'
    )
    return _doc(body, _OPT_TABLE_STYLE, TOGGLE_JS), container_h + 16


# --------------------------------------------------------------------------- #
# Decision cards
# --------------------------------------------------------------------------- #
_DECISION_STYLE = f"""
.decision-card {{
  background: linear-gradient(160deg, rgba(20,26,52,0.9) 0%, rgba(14,18,38,0.7) 100%);
  backdrop-filter: blur(8px);
  border-radius: 14px; border: 1px solid rgba(124,111,240,0.12);
  border-left: 4px solid {MUTED_ON_DARK}; padding: 0.85rem 1.1rem 0.75rem 1.1rem;
  margin-bottom: 0.6rem; box-shadow: 0 3px 12px rgba(0,0,0,0.22);
  transition: transform 0.2s cubic-bezier(.2,.8,.2,1), box-shadow 0.2s ease, border-color 0.2s ease;
}}
.decision-card:hover {{ transform: translateY(-2px); border-color: rgba(124,111,240,0.3); box-shadow: 0 9px 22px rgba(0,0,0,0.32), 0 0 18px rgba(124,111,240,0.08); }}
.decision-card.executed {{ border-left-color: {GREEN}; }}
.decision-card.blocked {{ border-left-color: {RED}; }}
.decision-card.approved-dry {{ border-left-color: {ICE}; }}
.decision-top {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.45rem; flex-wrap: wrap; gap: 0.3rem; }}
.decision-badge {{ display: inline-block; font-size: 0.66rem; font-weight: 800; letter-spacing: 0.05em; text-transform: uppercase; padding: 0.15rem 0.55rem; border-radius: 999px; }}
.decision-badge.executed {{ background: rgba(52,211,153,0.15); color: {GREEN}; border: 1px solid rgba(52,211,153,0.45); }}
.decision-badge.blocked {{ background: rgba(251,113,133,0.15); color: {RED}; border: 1px solid rgba(251,113,133,0.45); }}
.decision-badge.approved-dry {{ background: rgba(169,180,245,0.14); color: {ICE}; border: 1px solid rgba(169,180,245,0.4); }}
.decision-meta {{ font-size: 0.75rem; color: {MUTED_ON_DARK}; font-family: 'JetBrains Mono', monospace; }}
.decision-header {{ font-size: 1.02rem; font-weight: 800; color: {OFFWHITE}; margin-bottom: 0.15rem; }}
.decision-quote {{ font-style: italic; color: {ICE}; font-size: 0.87rem; margin-top: 0.5rem; padding-left: 0.65rem; border-left: 2px solid rgba(124,111,240,0.3); opacity: 0.95; }}
.card-toggle {{ margin-top: 0.6rem; font-size: 0.76rem; color: {ACCENT2}; cursor: pointer; user-select: none; opacity: 0.85; }}
.card-toggle:hover {{ opacity: 1; }}
.card-toggle::before {{ content: '\\25B8  '; display: inline-block; transition: transform 0.2s ease; }}
.card-toggle.open::before {{ transform: rotate(90deg); }}
.card-detail {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease; font-size: 0.83rem; color: {ICE}; line-height: 1.55; }}
.card-detail.open {{ max-height: 3000px; padding-top: 0.55rem; }}
.card-detail b {{ color: {OFFWHITE}; }}
.card-detail pre {{ white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.18); padding: 0.5rem 0.65rem; border-radius: 6px; font-family: 'JetBrains Mono', monospace; font-size: 0.76rem; color: {ICE}; }}
.scroll-panel {{ overflow-y: auto; padding-right: 6px; }}
"""


_DECISION_CARD_H = 178
_DECISION_DETAIL_H = 260
_DECISION_CAP = 720  # beyond this, the panel scrolls internally (custom scrollbar)


# --------------------------------------------------------------------------- #
# Indicator grid + option-candidate mini-card -- replaces the raw indented-JSON
# <pre> dump previously shown under "Full reasoning" / "AI reasoning" toggles.
# Same underlying numbers (src.indicators.build_feature_snapshot's output and
# src.options_client's candidate dicts, verbatim), just laid out as labeled,
# semantically-colored tiles instead of a JSON blob a reader has to parse by hand.
# --------------------------------------------------------------------------- #
_INDICATOR_PANEL_STYLE = f"""
.ind-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 0.5rem; margin-top: 0.5rem; }}
.ind-tile {{
  background: rgba(20,26,52,0.55); border: 1px solid rgba(124,111,240,0.14);
  border-radius: 10px; padding: 0.5rem 0.65rem;
}}
.ind-label {{ font-size: 0.58rem; color: {MUTED_ON_DARK}; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 3px; }}
.ind-value {{ font-size: 0.88rem; font-weight: 700; }}
.ind-gauge-track {{
  height: 5px; border-radius: 3px; margin-top: 7px; position: relative;
  background: linear-gradient(90deg, rgba(52,211,153,0.35) 0%, rgba(124,111,240,0.22) 30%, rgba(124,111,240,0.22) 70%, rgba(251,113,133,0.35) 100%);
}}
.ind-gauge-marker {{
  position: absolute; top: -3px; width: 2px; height: 11px; border-radius: 1px;
  background: {OFFWHITE}; box-shadow: 0 0 4px rgba(0,0,0,0.6);
}}
.ind-range-track {{ height: 5px; border-radius: 3px; margin-top: 7px; background: rgba(124,111,240,0.18); position: relative; }}
.ind-range-fill {{ position: absolute; top: 0; left: 0; height: 100%; border-radius: 3px; background: {GRADIENT}; }}
.ind-range-marker {{
  position: absolute; top: -3px; width: 2px; height: 11px; border-radius: 1px;
  background: {OFFWHITE}; box-shadow: 0 0 4px rgba(0,0,0,0.6);
}}
.candidate-card {{
  background: rgba(20,26,52,0.6); border: 1px solid rgba(124,111,240,0.18);
  border-radius: 12px; padding: 0.7rem 0.9rem; margin-top: 0.6rem;
}}
.candidate-title {{
  font-size: 0.66rem; font-weight: 800; color: {ACCENT2}; text-transform: uppercase;
  letter-spacing: 0.05em; margin-bottom: 0.55rem; display: flex; align-items: center; gap: 6px;
}}
.candidate-title::before {{ content: ''; width: 5px; height: 5px; border-radius: 50%; background: {GRADIENT}; }}
.candidate-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(82px, 1fr)); gap: 0.5rem 0.6rem; }}
.candidate-cell .l {{ font-size: 0.56rem; color: {MUTED_ON_DARK}; text-transform: uppercase; letter-spacing: 0.03em; }}
.candidate-cell .v {{ font-size: 0.82rem; font-weight: 700; color: {OFFWHITE}; font-family: 'JetBrains Mono', monospace; }}
.verdict-box {{
  display: flex; align-items: flex-start; gap: 0.55rem; margin-top: 0.5rem;
  padding: 0.55rem 0.75rem; border-radius: 10px; font-size: 0.83rem; line-height: 1.5;
}}
.verdict-box.pass {{ background: rgba(52,211,153,0.10); border: 1px solid rgba(52,211,153,0.35); }}
.verdict-box.fail {{ background: rgba(251,113,133,0.10); border: 1px solid rgba(251,113,133,0.35); }}
.verdict-icon {{ font-size: 1rem; line-height: 1.3; flex-shrink: 0; }}
.verdict-box.pass .verdict-icon {{ color: {GREEN}; }}
.verdict-box.fail .verdict-icon {{ color: {RED}; }}
.verdict-label {{ font-size: 0.62rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 2px; }}
.verdict-box.pass .verdict-label {{ color: {GREEN}; }}
.verdict-box.fail .verdict-label {{ color: {RED}; }}
.verdict-text {{ color: {OFFWHITE}; }}
"""


def _render_verdict_box(status_class: str, risk_reason: str) -> str:
    """Replaces the old plain '<b>Risk manager verdict:</b> ...' line with a
    colored pass/fail callout so the verdict reads at a glance instead of
    blending into the surrounding prose."""
    passed = status_class in ("executed", "approved-dry")
    tone = "pass" if passed else "fail"
    icon = "&#10003;" if passed else "&#10005;"
    label = "Risk Manager · Approved" if passed else "Risk Manager · Blocked"
    return (
        f'<div class="verdict-box {tone}"><span class="verdict-icon">{icon}</span>'
        f'<div><div class="verdict-label">{label}</div>'
        f'<div class="verdict-text">{_esc(risk_reason)}</div></div></div>'
    )


def _fmt_num(v, decimals: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def _ind_tile(label: str, value: str, tone: str = "neutral", extra: str = "") -> str:
    return (
        f'<div class="ind-tile"><div class="ind-label">{_esc(label)}</div>'
        f'<div class="ind-value num tone-{tone}">{value}</div>{extra}</div>'
    )


def _render_indicator_grid(indicators: dict) -> str:
    """indicators: the exact dict src.indicators.build_feature_snapshot()
    produces (last_close, day_change_pct, sma_20, sma_50, price_vs_sma20_pct,
    rsi_14, macd_histogram, annualized_volatility_pct, 52w_high, 52w_low).
    Missing keys degrade to an em-dash tile rather than being fabricated."""
    if not indicators:
        return ""

    tiles = [_ind_tile("Last Close", "$" + _fmt_num(indicators.get("last_close")))]

    day_chg = indicators.get("day_change_pct")
    tiles.append(_ind_tile(
        "Day Change", ("+" if (day_chg or 0) >= 0 else "") + _fmt_num(day_chg, 2, "%"),
        "positive" if (day_chg or 0) >= 0 else "negative",
    ))

    tiles.append(_ind_tile("SMA 20", "$" + _fmt_num(indicators.get("sma_20"))))
    tiles.append(_ind_tile("SMA 50", "$" + _fmt_num(indicators.get("sma_50"))))

    vs_sma = indicators.get("price_vs_sma20_pct")
    tiles.append(_ind_tile(
        "Price vs SMA20", ("+" if (vs_sma or 0) >= 0 else "") + _fmt_num(vs_sma, 2, "%"),
        "positive" if (vs_sma or 0) >= 0 else "negative",
    ))

    rsi = indicators.get("rsi_14")
    if rsi is not None:
        rsi_tone = "positive" if rsi < 30 else ("negative" if rsi > 70 else "neutral")
        rsi_zone = "oversold" if rsi < 30 else ("overbought" if rsi > 70 else "neutral")
        marker_pct = max(0.0, min(100.0, rsi))
        gauge = (
            f'<div class="ind-gauge-track"><div class="ind-gauge-marker" '
            f'style="left:calc({marker_pct:.1f}% - 1px);"></div></div>'
        )
        tiles.append(_ind_tile("RSI (14)", f"{_fmt_num(rsi)} · {rsi_zone}", rsi_tone, gauge))
    else:
        tiles.append(_ind_tile("RSI (14)", "—"))

    macd_h = indicators.get("macd_histogram")
    tiles.append(_ind_tile(
        "MACD Histogram", _fmt_num(macd_h, 4),
        "positive" if (macd_h or 0) >= 0 else "negative",
    ))

    vol = indicators.get("annualized_volatility_pct")
    tiles.append(_ind_tile("Ann. Volatility", _fmt_num(vol, 1, "%")))

    lo, hi, last = indicators.get("52w_low"), indicators.get("52w_high"), indicators.get("last_close")
    if lo is not None and hi is not None and last is not None and hi > lo:
        pos_pct = max(0.0, min(100.0, (last - lo) / (hi - lo) * 100))
        range_bar = (
            f'<div class="ind-range-track"><div class="ind-range-fill" style="width:{pos_pct:.1f}%;"></div>'
            f'<div class="ind-range-marker" style="left:calc({pos_pct:.1f}% - 1px);"></div></div>'
        )
        tiles.append(_ind_tile("52W Range", f"${_fmt_num(lo)} – ${_fmt_num(hi)}", "neutral", range_bar))

    return f'<div class="ind-grid">{"".join(tiles)}</div>'


_CANDIDATE_LABELS = {"open_covered_call": "Covered Call Candidate", "open_cash_secured_put": "Cash-Secured Put Candidate"}


def _render_candidate_card(candidate: dict, kind_label: str) -> str:
    """candidate: one raw dict from OptionsClient.get_best_covered_call_candidate
    / get_best_cash_secured_put_candidate (symbol, strike, expiration, dte, bid,
    ask, mid, implied_volatility, delta/gamma/theta/vega, open_interest)."""
    if not candidate:
        return ""
    cells = [
        ("Contract", candidate.get("symbol", "—")),
        ("Strike", "$" + _fmt_num(candidate.get("strike"))),
        ("Expires", f"{candidate.get('expiration', '—')} ({candidate.get('dte', '—')}d)"),
        ("Bid", "$" + _fmt_num(candidate.get("bid"))),
        ("Ask", "$" + _fmt_num(candidate.get("ask"))),
        ("Mid", "$" + _fmt_num(candidate.get("mid"))),
        ("IV", _fmt_num((candidate.get("implied_volatility") or 0) * 100, 1, "%")),
        ("Delta", _fmt_num(candidate.get("delta"), 3)),
        ("Gamma", _fmt_num(candidate.get("gamma"), 4)),
        ("Theta", _fmt_num(candidate.get("theta"), 3)),
        ("Vega", _fmt_num(candidate.get("vega"), 3)),
        ("Open Int.", str(candidate.get("open_interest", "—"))),
    ]
    grid = "".join(
        f'<div class="candidate-cell"><div class="l">{_esc(l)}</div><div class="v">{_esc(v)}</div></div>'
        for l, v in cells
    )
    return (
        f'<div class="candidate-card"><div class="candidate-title">{_esc(kind_label)}</div>'
        f'<div class="candidate-grid">{grid}</div></div>'
    )


def _render_decision_extras(d: dict) -> str:
    """Builds the indicator grid + (for options records) any candidate
    card(s) for one already-prepped decision record. d may carry
    "indicators" (dict) and/or "candidates" (dict of action -> candidate
    dict, options records only) -- both optional, both degrade to nothing
    rather than a fabricated placeholder when absent."""
    parts = []
    if d.get("indicators"):
        parts.append(_render_indicator_grid(d["indicators"]))
    for action, candidate in (d.get("candidates") or {}).items():
        if candidate:
            parts.append(_render_candidate_card(candidate, _CANDIDATE_LABELS.get(action, "Candidate")))
    return "".join(p for p in parts if p)


def render_decision_cards(records: list[dict]) -> tuple[str, int]:
    """records: list of dicts with status_class, status_label, symbol, action,
    contract_suffix, timestamp, meta_line, reasoning_short, reasoning_full,
    risk_note, risk_reason, order_str (or None), indicators (dict or None),
    candidates (dict of action -> option-candidate dict, options records only).

    Same scroll-panel sizing strategy as the position card renderers -- this
    is the explicit "long decision history list" case the custom-scrollbar
    requirement calls out, so it's capped and internally scrollable rather
    than growing the iframe unboundedly for a long, paginated list.
    """
    if not records:
        body = _empty_state("No decisions match the current filters.")
        return _doc(body, _DECISION_STYLE), 70

    cards = []
    for i, d in enumerate(records):
        card_id = f"dec-{i}"
        order_line = f'<div style="margin-top:6px;"><b>Order placed:</b> <code>{_esc(d["order_str"])}</code></div>' if d.get("order_str") else ""
        verdict_box = _render_verdict_box(d["status_class"], d.get("risk_reason", "—"))
        extras = _render_decision_extras(d)

        cards.append(f"""
        <div class="decision-card {d['status_class']} animate-in" style="animation-delay:{min(i, 20) * 30}ms">
          <div class="decision-top">
            <span class="decision-badge {d['status_class']}">{_esc(d['status_label'])}</span>
            <span class="decision-meta">{_esc(d['timestamp'])}</span>
          </div>
          <div class="decision-header">{_esc(d['symbol'])} &mdash; {_esc(d['action'])}{_esc(d.get('contract_suffix', ''))}</div>
          <div class="decision-meta">{_esc(d['meta_line'])}</div>
          <div class="decision-quote">&ldquo;{_esc(d['reasoning_short'])}&rdquo;</div>
          <div class="card-toggle" id="{card_id}-trigger" onclick="toggleCard('{card_id}')">Full reasoning, risk notes &amp; indicators</div>
          <div class="card-detail" id="{card_id}">
            <div><b>Reasoning:</b> {_esc(d.get('reasoning_full', '—'))}</div>
            <div style="margin-top:4px;"><b>Risk note:</b> {_esc(d.get('risk_note', '—'))}</div>
            {verdict_box}
            {order_line}
            {extras}
          </div>
        </div>
        """)

    collapsed_total = len(records) * _DECISION_CARD_H
    container_h = min(_DECISION_CAP, collapsed_total + _DECISION_DETAIL_H)
    body = f'<div class="scroll-panel" style="max-height:{container_h}px;">{"".join(cards)}</div>'
    return _doc(body, _DECISION_STYLE + _INDICATOR_PANEL_STYLE, TOGGLE_JS), container_h + 16


# --------------------------------------------------------------------------- #
# Decision table -- dense, monospace, terminal-style rows (Decisions page)
# --------------------------------------------------------------------------- #
_TABLE_STYLE = f"""
.dec-table {{ font-size: 0.82rem; }}
.dec-head, .dec-row {{
  display: grid;
  grid-template-columns: 128px 1fr 64px 200px 2fr;
  gap: 0.8rem; align-items: center;
}}
.dec-head {{
  padding: 0 0.9rem 0.5rem 0.9rem; color: {MUTED_ON_DARK};
  font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700;
  border-bottom: 1px solid rgba(124,111,240,0.16); position: sticky; top: 0;
  background: {NAVY}; z-index: 1;
}}
.dec-row {{
  padding: 0.55rem 0.9rem; border-radius: 10px; cursor: pointer;
  border-left: 3px solid transparent;
  transition: background 0.15s ease, transform 0.15s ease;
}}
.dec-row:hover {{ background: rgba(124, 111, 240, 0.06); transform: translateX(2px); }}
.dec-row.executed {{ border-left-color: {GREEN}; }}
.dec-row.blocked {{ border-left-color: {RED}; }}
.dec-row.approved-dry {{ border-left-color: {ICE}; }}
.dec-cell.time {{ font-family: 'JetBrains Mono', monospace; color: {MUTED_ON_DARK}; font-size: 0.76rem; }}
.dec-cell.symbol b {{ color: {OFFWHITE}; font-weight: 800; }}
.dec-cell.symbol span {{ color: {MUTED_ON_DARK}; font-size: 0.78rem; margin-left: 4px; }}
.dec-cell.conf {{ font-family: 'JetBrains Mono', monospace; color: {ICE}; }}
.dec-cell.reasoning {{ color: {ICE}; opacity: 0.85; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.dec-badge {{ display: inline-block; font-size: 0.62rem; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase; padding: 0.12rem 0.5rem; border-radius: 999px; white-space: nowrap; }}
.dec-badge.executed {{ background: rgba(52,211,153,0.15); color: {GREEN}; border: 1px solid rgba(52,211,153,0.45); }}
.dec-badge.blocked {{ background: rgba(251,113,133,0.15); color: {RED}; border: 1px solid rgba(251,113,133,0.45); }}
.dec-badge.approved-dry {{ background: rgba(169,180,245,0.14); color: {ICE}; border: 1px solid rgba(169,180,245,0.4); }}
.dec-detail {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease; font-size: 0.83rem; color: {ICE}; line-height: 1.6; margin: 0 0.9rem; }}
.dec-detail.open {{ max-height: 3000px; padding: 0.6rem 0 0.9rem 0; border-bottom: 1px solid rgba(124,111,240,0.1); }}
.dec-detail b {{ color: {OFFWHITE}; }}
.dec-detail pre {{ white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.2); padding: 0.5rem 0.65rem; border-radius: 6px; font-family: 'JetBrains Mono', monospace; font-size: 0.76rem; color: {ICE}; margin-top: 0.4rem; }}
.scroll-panel {{ overflow-y: auto; padding-right: 6px; }}
"""

_TABLE_ROW_H = 42
_TABLE_DETAIL_H = 260
_TABLE_CAP = 640


def render_decision_table(records: list[dict]) -> tuple[str, int]:
    """Dense, terminal-style table -- one line per decision, click a row to
    expand its full reasoning/risk-note/indicators below it. Same fields as
    render_decision_cards, just laid out for scanning many rows at once
    (the Decisions page's full log) instead of a handful of prominent cards."""
    if not records:
        body = _empty_state("No decisions match the current filters.")
        return _doc(body, _TABLE_STYLE), 70

    header = (
        '<div class="dec-head"><div>TIME</div><div>SYMBOL / ACTION</div>'
        '<div>CONF</div><div>OUTCOME</div><div>REASONING</div></div>'
    )
    rows = [header]
    for i, d in enumerate(records):
        row_id = f"tbl-{i}"
        conf_display = "—" if "Confidence" not in d.get("meta_line", "") else d["meta_line"].split(":")[-1].strip()
        order_line = f'<div style="margin-top:6px;"><b>Order placed:</b> <code>{_esc(d["order_str"])}</code></div>' if d.get("order_str") else ""
        verdict_box = _render_verdict_box(d["status_class"], d.get("risk_reason", "—"))
        extras = _render_decision_extras(d)

        rows.append(f"""
        <div class="dec-row {d['status_class']}" onclick="toggleCard('{row_id}')">
          <div class="dec-cell time">{_esc(d['timestamp'])}</div>
          <div class="dec-cell symbol"><b>{_esc(d['symbol'])}</b> <span>{_esc(d['action'])}{_esc(d.get('contract_suffix', ''))}</span></div>
          <div class="dec-cell conf">{_esc(conf_display)}</div>
          <div class="dec-cell outcome"><span class="dec-badge {d['status_class']}">{_esc(d['status_label'])}</span></div>
          <div class="dec-cell reasoning">{_esc(d['reasoning_short'])}</div>
        </div>
        <div class="dec-detail" id="{row_id}">
          <div><b>Reasoning:</b> {_esc(d.get('reasoning_full', '—'))}</div>
          <div style="margin-top:4px;"><b>Risk note:</b> {_esc(d.get('risk_note', '—'))}</div>
          {verdict_box}
          {order_line}
          {extras}
        </div>
        """)

    collapsed_total = 44 + len(records) * _TABLE_ROW_H
    container_h = min(_TABLE_CAP, collapsed_total + _TABLE_DETAIL_H)
    body = f'<div class="dec-table scroll-panel" style="max-height:{container_h}px;">{"".join(rows)}</div>'
    return _doc(body, _TABLE_STYLE + _INDICATOR_PANEL_STYLE, TOGGLE_JS), container_h + 16


# --------------------------------------------------------------------------- #
# How It Decides (static explainer)
# --------------------------------------------------------------------------- #
_EXPLAINER_STYLE = f"""
.explainer-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.9rem; }}
.explainer-step {{
  background: linear-gradient(160deg, rgba(20,26,52,0.9) 0%, rgba(14,18,38,0.7) 100%);
  border: 1px solid rgba(124,111,240,0.14); border-radius: 14px;
  padding: 1.1rem 1.2rem; position: relative; overflow: hidden;
  transition: transform 0.22s ease, border-color 0.22s ease;
}}
.explainer-step:hover {{ transform: translateY(-3px); border-color: rgba(124,111,240,0.35); }}
.explainer-step::after {{
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: {GRADIENT};
}}
.step-num {{ font-size: 0.78rem; font-weight: 800; color: {ACCENT2}; letter-spacing: 0.06em; margin-bottom: 0.4rem; font-family: 'JetBrains Mono', monospace; }}
.step-title {{ font-size: 0.98rem; font-weight: 800; color: {OFFWHITE}; margin-bottom: 0.45rem; }}
.step-body {{ font-size: 0.85rem; color: {ICE}; line-height: 1.55; opacity: 0.92; }}
"""

_STEPS = [
    ("01", "GATHER",
     "Pull live prices and technical indicators from Alpaca's Trading and Market Data APIs — "
     "for both stocks and live options chains (strikes, expirations, greeks, quotes)."),
    ("02", "REASON",
     "GPT-OSS-120B (via Groq) evaluates the data and proposes a structured buy/sell/hold (or options open/close) "
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


def render_how_it_decides() -> tuple[str, int]:
    cards = "".join(
        f'<div class="explainer-step animate-in" style="animation-delay:{i * 70}ms">'
        f'<div class="step-num">{n}</div><div class="step-title">{t}</div>'
        f'<div class="step-body">{b}</div></div>'
        for i, (n, t, b) in enumerate(_STEPS)
    )
    body = f'<div class="explainer-grid">{cards}</div>'
    # Static content, but the GATE step's copy is noticeably longer than the
    # others and wraps to more lines at narrower column widths (CSS Grid
    # auto-sizes the row to the tallest card, so a too-short iframe box
    # clips it) -- sized generously for that worst case rather than the
    # shorter steps.
    return _doc(body, _EXPLAINER_STYLE), 460


# --------------------------------------------------------------------------- #
# Risk Gate Catalog -- src/risk_gates.py's STOCK_GATES / OPTIONS_GATES,
# numbered and named, each with a live blocked-count pulled from real logged
# decisions (src/risk_gates.py's classifier, not a fabricated number).
# --------------------------------------------------------------------------- #
_GATE_CATALOG_STYLE = f"""
.gate-summary-row {{ display: flex; gap: 0.7rem; flex-wrap: wrap; margin-bottom: 1.1rem; }}
.gate-summary-card {{
  flex: 1 1 150px; min-width: 140px;
  background: linear-gradient(160deg, rgba(20,26,52,0.92) 0%, rgba(14,18,38,0.75) 100%);
  border: 1px solid rgba(124,111,240,0.16); border-radius: 14px; padding: 0.85rem 1rem;
}}
.gate-summary-label {{ font-size: 0.64rem; color: {MUTED_ON_DARK}; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }}
.gate-summary-value {{ font-size: 1.4rem; font-weight: 800; }}
.gate-track-title {{
  font-size: 0.72rem; font-weight: 800; color: {ACCENT2}; text-transform: uppercase;
  letter-spacing: 0.08em; margin: 1.1rem 0 0.6rem 0;
}}
.gate-list {{ display: flex; flex-direction: column; gap: 0.55rem; }}
.gate-row {{
  display: flex; align-items: center; gap: 0.9rem;
  background: rgba(20,26,52,0.55); border: 1px solid rgba(124,111,240,0.13);
  border-radius: 12px; padding: 0.75rem 1rem;
  transition: border-color 0.2s ease, transform 0.2s ease;
}}
.gate-row:hover {{ border-color: rgba(124,111,240,0.32); transform: translateX(2px); }}
.gate-num {{
  flex-shrink: 0; width: 34px; height: 34px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  background: {GRADIENT}; color: #0A0E1E; font-weight: 800; font-size: 0.88rem;
  font-family: 'JetBrains Mono', monospace;
}}
.gate-body {{ flex: 1; min-width: 0; }}
.gate-name {{ font-size: 0.92rem; font-weight: 800; color: {OFFWHITE}; }}
.gate-desc {{ font-size: 0.78rem; color: {ICE}; opacity: 0.85; line-height: 1.45; margin-top: 2px; }}
.gate-count {{
  flex-shrink: 0; text-align: center; min-width: 76px; padding: 0.4rem 0.6rem;
  border-radius: 10px; font-family: 'JetBrains Mono', monospace;
}}
.gate-count.zero {{ background: rgba(124,111,240,0.08); border: 1px solid rgba(124,111,240,0.15); }}
.gate-count.nonzero {{ background: rgba(251,113,133,0.10); border: 1px solid rgba(251,113,133,0.35); }}
.gate-count-value {{ font-size: 1.05rem; font-weight: 800; }}
.gate-count.zero .gate-count-value {{ color: {MUTED_ON_DARK}; }}
.gate-count.nonzero .gate-count-value {{ color: {RED}; }}
.gate-count-label {{ font-size: 0.56rem; color: {MUTED_ON_DARK}; text-transform: uppercase; letter-spacing: 0.03em; margin-top: 1px; }}
"""


def render_gate_catalog(
    stock_gates: list[dict], stock_blocked: dict, stock_evaluated: int, stock_approved: int,
    options_gates: list[dict], options_blocked: dict, options_evaluated: int, options_approved: int,
) -> tuple[str, int]:
    total_evaluated = stock_evaluated + options_evaluated
    total_approved = stock_approved + options_approved
    total_blocked = total_evaluated - total_approved

    summary = [
        ("Decisions Evaluated", str(total_evaluated)),
        ("Survived Every Gate", str(total_approved)),
        ("Blocked", str(total_blocked)),
    ]
    summary_html = "".join(
        f'<div class="gate-summary-card animate-in"><div class="gate-summary-label">{_esc(l)}</div>'
        f'<div class="gate-summary-value num">{_esc(v)}</div></div>'
        for l, v in summary
    )

    def _track(title: str, gates: list[dict], blocked: dict, i_offset: int) -> str:
        rows = []
        for i, g in enumerate(gates):
            count = blocked.get(g["id"], 0)
            tone = "nonzero" if count else "zero"
            rows.append(f"""
            <div class="gate-row animate-in" style="animation-delay:{(i_offset + i) * 40}ms">
              <div class="gate-num">{i + 1:02d}</div>
              <div class="gate-body">
                <div class="gate-name">{_esc(g['name'])}</div>
                <div class="gate-desc">{_esc(g['description'])}</div>
              </div>
              <div class="gate-count {tone}">
                <div class="gate-count-value">{count}</div>
                <div class="gate-count-label">blocked</div>
              </div>
            </div>
            """)
        return f'<div class="gate-track-title">{_esc(title)}</div><div class="gate-list">{"".join(rows)}</div>'

    body = (
        f'<div class="gate-summary-row">{summary_html}</div>'
        f'{_track("Stock Entry Gates", stock_gates, stock_blocked, 0)}'
        f'{_track("Options Entry Gates", options_gates, options_blocked, len(stock_gates))}'
    )
    height = 150 + len(stock_gates) * 78 + len(options_gates) * 78 + 90
    return _doc(body, _GATE_CATALOG_STYLE), height


# --------------------------------------------------------------------------- #
# Backtest -- persistent "simulation, not live results" banner + trade log.
# The equity curve itself is a native st.plotly_chart in dashboard.py (same
# pattern as the live equity curve), not rendered here.
# --------------------------------------------------------------------------- #
_BACKTEST_BANNER_STYLE = f"""
.bt-banner {{
  position: relative; overflow: hidden; display: flex; align-items: flex-start; gap: 0.85rem;
  background: linear-gradient(135deg, rgba(124,111,240,0.18) 0%, rgba(34,211,238,0.10) 100%);
  border: 1px solid rgba(124,111,240,0.4); border-radius: 14px; padding: 0.9rem 1.2rem;
}}
.bt-banner::before {{
  content: ''; position: absolute; inset: 0; pointer-events: none;
  background-image: repeating-linear-gradient(135deg, rgba(255,255,255,0.035) 0 10px, transparent 10px 20px);
}}
.bt-banner-icon {{ font-size: 1.35rem; flex-shrink: 0; line-height: 1.3; }}
.bt-banner-title {{ font-size: 0.8rem; font-weight: 800; color: {OFFWHITE}; text-transform: uppercase; letter-spacing: 0.05em; }}
.bt-banner-text {{ font-size: 0.83rem; color: {ICE}; opacity: 0.92; margin-top: 3px; line-height: 1.5; }}
"""


def render_backtest_banner(disclaimer: str) -> tuple[str, int]:
    body = f"""
    <div class="bt-banner animate-in">
      <div class="bt-banner-icon">&#129514;</div>
      <div><div class="bt-banner-title">Backtest &mdash; historical simulation, not live results</div>
      <div class="bt-banner-text">{_esc(disclaimer)}</div></div>
    </div>
    """
    lines = max(1, len(disclaimer) // 85 + 1)
    return _doc(body, _BACKTEST_BANNER_STYLE), 56 + lines * 20


_BT_TRADES_STYLE = f"""
.bt-table {{ font-size: 0.8rem; }}
.bt-head, .bt-row {{
  display: grid; grid-template-columns: 96px 76px 68px 92px 70px 120px 1fr;
  gap: 0.7rem; align-items: center;
}}
.bt-head {{
  padding: 0 0.9rem 0.5rem 0.9rem; color: {MUTED_ON_DARK};
  font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700;
  border-bottom: 1px solid rgba(124,111,240,0.16); position: sticky; top: 0; background: {NAVY};
}}
.bt-row {{ padding: 0.5rem 0.9rem; border-radius: 8px; border-left: 3px solid transparent; }}
.bt-row.buy {{ border-left-color: {ICE}; }}
.bt-row.sell-win {{ border-left-color: {GREEN}; }}
.bt-row.sell-loss {{ border-left-color: {RED}; }}
.bt-badge {{ display: inline-block; font-size: 0.6rem; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase; padding: 0.1rem 0.5rem; border-radius: 999px; }}
.bt-badge.buy {{ background: rgba(169,180,245,0.14); color: {ICE}; border: 1px solid rgba(169,180,245,0.4); }}
.bt-badge.sell {{ background: rgba(52,211,153,0.15); color: {GREEN}; border: 1px solid rgba(52,211,153,0.45); }}
.bt-cell.reason {{ color: {ICE}; opacity: 0.85; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.76rem; }}
.scroll-panel {{ overflow-y: auto; padding-right: 6px; }}
"""


def render_backtest_trades(trades: list[dict]) -> tuple[str, int]:
    """trades: list of dicts as produced by BacktestResult.trades (symbol,
    action, date, price, qty, reason, pnl, pnl_pct)."""
    if not trades:
        body = _empty_state("No simulated trades in this run.")
        return _doc(body, _BT_TRADES_STYLE), 70

    header = (
        '<div class="bt-head"><div>DATE</div><div>SYMBOL</div><div>ACTION</div>'
        '<div>PRICE</div><div>QTY</div><div>P&amp;L</div><div>REASON</div></div>'
    )
    rows = [header]
    for t in reversed(trades):  # newest first
        is_sell = t["action"] == "SELL"
        pnl = t.get("pnl")
        row_cls = "buy" if not is_sell else ("sell-win" if (pnl or 0) >= 0 else "sell-loss")
        if pnl is None:
            pnl_html = "—"
        else:
            tone = "positive" if pnl >= 0 else "negative"
            pnl_html = f'<span class="num tone-{tone}">{"+" if pnl >= 0 else "-"}${abs(pnl):,.2f}</span>'

        rows.append(f"""
        <div class="bt-row {row_cls}">
          <div class="bt-cell num">{_esc(t['date'])}</div>
          <div class="bt-cell"><b>{_esc(t['symbol'])}</b></div>
          <div class="bt-cell"><span class="bt-badge {'sell' if is_sell else 'buy'}">{_esc(t['action'])}</span></div>
          <div class="bt-cell num">${t['price']:,.2f}</div>
          <div class="bt-cell num">{t['qty']:g}</div>
          <div class="bt-cell">{pnl_html}</div>
          <div class="bt-cell reason">{_esc(t['reason'])}</div>
        </div>
        """)

    row_h = 40
    container_h = min(560, 44 + len(trades) * row_h)
    body = f'<div class="bt-table scroll-panel" style="max-height:{container_h}px;">{"".join(rows)}</div>'
    return _doc(body, _BT_TRADES_STYLE), container_h + 16


# --------------------------------------------------------------------------- #
# Footer
# --------------------------------------------------------------------------- #
_FOOTER_STYLE = f"""
.app-footer {{ text-align: center; font-size: 0.78rem; color: {MUTED_ON_DARK}; line-height: 1.8; padding-top: 0.4rem; }}
.app-footer a {{ color: {ICE}; text-decoration: none; }}
.app-footer a:hover {{ text-decoration: underline; }}
"""


_ALERT_STYLE = f"""
.alert {{
  border-radius: 12px; padding: 0.8rem 1.05rem; font-size: 0.88rem; line-height: 1.5;
  border-left: 3px solid {ICE}; background: rgba(20,26,52,0.7); color: {ICE};
  border: 1px solid rgba(124,111,240,0.12); border-left: 3px solid {ICE};
}}
.alert.error {{ border-left-color: {RED}; background: rgba(251,113,133,0.12); color: {OFFWHITE}; }}
.alert.success {{ border-left-color: {GREEN}; background: rgba(52,211,153,0.12); color: {OFFWHITE}; }}
.alert code {{ background: rgba(124,111,240,0.15); padding: 0.05rem 0.35rem; border-radius: 4px; color: {OFFWHITE}; }}
"""


def render_inline_alert(message: str, tone: str = "info") -> tuple[str, int]:
    """Replaces st.info/st.warning/st.success for anything in the primary
    viewing experience -- message is HTML-safe caller-authored copy (already
    escaped/composed by dashboard.py), not raw untrusted text."""
    css_class = {"error": "error", "success": "success"}.get(tone, "")
    body = f'<div class="alert {css_class} animate-in">{message}</div>'
    lines = max(1, len(message) // 80 + 1)
    return _doc(body, _ALERT_STYLE), 46 + lines * 20


def render_footer(is_paper: bool = True) -> tuple[str, int]:
    status_line = (
        "Paper trading only. Live, read-only account data &mdash; no real capital at risk."
        if is_paper else
        "&#9888; LIVE TRADING &mdash; real capital is at risk."
    )
    body = f"""
    <div class="app-footer">
      An autonomous, explainable paper-trading agent &mdash; GPT-OSS-120B reasons, a deterministic risk gate decides.<br/>
      Built for the Alpaca AI Trading Agents Hackathon
      (<a href="https://lablab.ai" target="_blank">lablab.ai</a>)
      &middot; <a href="https://github.com/isianioui/Alpaca-AI-Trading-Agent" target="_blank">GitHub repo</a><br/>
      {status_line}
    </div>
    """
    return _doc(body, _FOOTER_STYLE), 90
