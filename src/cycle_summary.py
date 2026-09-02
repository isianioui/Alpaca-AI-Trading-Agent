"""
Pure-Python templating over already-logged decision records -- no LLM call.

Turns the structured fields already written by trading_agent.py /
options_trading_agent.py / exit_engine.py (symbol, action, risk_approved,
risk_reason, confidence, trigger, timestamps) into:
  - a plain-English "what did the last cycle do" narrative
    (summarize_latest_cycle), and
  - simple activity counters across the whole log (compute_activity_counters)

for the dashboard's "Latest Cycle" card and stat-chip row. Both functions
are deterministic string/arithmetic templating over data that already
exists -- nothing here calls Gemini or invents a fact not present in the
records passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _kind_of(record: dict) -> str:
    if record.get("trigger") == "exit_engine":
        return "exit_engine"
    return "option" if record.get("asset_class") == "option" else "stock"


def group_into_cycles(decisions: list[dict], max_gap_seconds: float = 300) -> list[list[dict]]:
    """Groups LLM-driven decision records (excludes exit_engine records,
    which are counted separately -- each exit-engine run logs exactly one
    exit_engine_summary record, so it's already a 1:1 cycle count) into
    contiguous runs: a new group starts whenever the asset-class "kind"
    changes or the gap since the previous record exceeds max_gap_seconds.
    This matches how run_cycle() actually writes to the log -- one process,
    single-threaded, one record per symbol in quick succession -- so a
    simple kind+gap heuristic is enough; no cycle id needs to exist in the
    log for this to work."""
    records = [d for d in decisions if _kind_of(d) != "exit_engine" and d.get("status") in ("ok", "error")]
    records = sorted(records, key=lambda d: d.get("timestamp") or "")

    groups: list[list[dict]] = []
    current: list[dict] = []
    prev_kind = None
    prev_time = None

    for r in records:
        kind = _kind_of(r)
        t = _parse_ts(r.get("timestamp"))
        gap_too_large = prev_time is not None and t is not None and (t - prev_time).total_seconds() > max_gap_seconds
        if current and (kind != prev_kind or gap_too_large):
            groups.append(current)
            current = []
        current.append(r)
        prev_kind = kind
        prev_time = t

    if current:
        groups.append(current)
    return groups


def _narrate_llm_cycle(records: list[dict]) -> str:
    ok = [r for r in records if r.get("status") == "ok"]
    errored = [r for r in records if r.get("status") == "error"]

    header = f"Checked {len(ok)} symbol{'s' if len(ok) != 1 else ''}"
    if errored:
        header += f" ({len(errored)} skipped due to errors)"
    header += "."

    if not ok:
        return header

    holds = [r for r in ok if (r.get("llm_action") or "hold").lower() == "hold"]
    non_holds = [r for r in ok if (r.get("llm_action") or "hold").lower() != "hold"]

    if not non_holds:
        return header + " All were holds -- no candidate met the model's conviction threshold to propose a trade."

    executed = [r for r in non_holds if r.get("order") is not None]
    rejected = [r for r in non_holds if not r.get("risk_approved")]
    approved_dry = [r for r in non_holds if r.get("risk_approved") and r.get("order") is None]

    bits = []
    if executed:
        bits.append(f"{len(executed)} trade{'s' if len(executed) != 1 else ''} executed")
    if approved_dry:
        bits.append(f"{len(approved_dry)} approved (dry run, no order placed)")
    if rejected:
        bits.append(f"{len(rejected)} rejected by the risk gate")
    if holds:
        bits.append(f"{len(holds)} hold")
    body = ", ".join(bits) + "." if bits else ""

    highlight = max(non_holds, key=lambda r: r.get("confidence", 0) or 0)
    action = (highlight.get("llm_action") or "").upper()
    label = "Executed" if highlight.get("order") is not None else ("Rejected" if not highlight.get("risk_approved") else "Approved")
    reason = highlight.get("risk_reason") or highlight.get("reasoning") or ""
    highlight_line = (
        f" Highest-signal call: {label} — {highlight.get('symbol')} {action} "
        f"(confidence {highlight.get('confidence', 0):.2f}). {reason}"
    )

    return f"{header} {body}{highlight_line}"


def _narrate_exit_engine(record: dict) -> str:
    checked = record.get("positions_checked", 0)
    closes = record.get("closes", 0)
    holds = record.get("holds", 0)

    if checked == 0:
        return "No open positions to evaluate against exit rules."
    if closes == 0:
        return f"Open positions: {checked} checked against exit rules, all within thresholds."
    symbols = ", ".join(record.get("closed_symbols") or [])
    return (
        f"Open positions: {checked} checked against exit rules — {closes} triggered a close"
        f"{f' ({symbols})' if symbols else ''}, {holds} remain within thresholds."
    )


@dataclass
class CycleSummary:
    timestamp: str
    kind: str          # "stock" | "option" | "exit_engine"
    narrative: str


def summarize_latest_cycle(decisions: list[dict]) -> Optional[CycleSummary]:
    """Finds the most recent cycle in the log (by timestamp, across every
    record type) and builds its narrative. Returns None if nothing has run
    yet -- callers must render an honest empty state, not a fabricated
    example."""
    candidates = [d for d in decisions if d.get("status") in ("ok", "error")]
    if not candidates:
        return None

    candidates = sorted(candidates, key=lambda d: d.get("timestamp") or "")
    latest = candidates[-1]

    if latest.get("record_type") == "exit_engine_summary":
        return CycleSummary(
            timestamp=latest.get("timestamp", ""),
            kind="exit_engine",
            narrative=_narrate_exit_engine(latest),
        )

    groups = group_into_cycles(decisions)
    if not groups:
        return None
    cycle = groups[-1]
    return CycleSummary(
        timestamp=cycle[-1].get("timestamp", ""),
        kind=_kind_of(cycle[-1]),
        narrative=_narrate_llm_cycle(cycle),
    )


@dataclass
class ActivityCounters:
    days_live: Optional[int]
    cycles_run: int
    trades_filled: int
    open_positions: int
    refused_by_risk: int


def compute_activity_counters(
    decisions: list[dict],
    trade_history_records: list[dict],
    open_positions_count: int,
    account_created_at: Optional[datetime] = None,
) -> ActivityCounters:
    """All inputs are already-fetched data (decision log, trade history,
    a live position count, the account's own created_at) -- purely
    arithmetic, no new data source and no LLM call."""
    timestamps = [_parse_ts(d.get("timestamp")) for d in decisions]
    timestamps = [t for t in timestamps if t is not None]

    days_live = None
    if account_created_at is not None:
        now = datetime.now(account_created_at.tzinfo) if account_created_at.tzinfo else datetime.now()
        days_live = max(0, (now - account_created_at).days)
    elif timestamps:
        now = datetime.now(timestamps[0].tzinfo) if timestamps[0].tzinfo else datetime.now()
        days_live = max(0, (now - min(timestamps)).days)

    cycles_run = len(group_into_cycles(decisions))
    cycles_run += sum(1 for d in decisions if d.get("record_type") == "exit_engine_summary")

    refused_by_risk = sum(
        1 for d in decisions
        if d.get("status") == "ok" and d.get("record_type") != "exit_engine_summary"
        and d.get("risk_approved") is False
    )

    return ActivityCounters(
        days_live=days_live,
        cycles_run=cycles_run,
        trades_filled=len(trade_history_records),
        open_positions=open_positions_count,
        refused_by_risk=refused_by_risk,
    )
