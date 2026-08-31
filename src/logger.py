"""
Simple append-only JSONL decision log.

Every cycle of the agent (one pass over the watchlist) writes one
line per symbol with the LLM's reasoning, the risk manager's verdict,
and the resulting order (if any). The Streamlit dashboard reads this
file to render the "why did the agent do that" view judges care about.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "decisions.jsonl"


def log_decision(record: dict, path: Path = DEFAULT_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**record, "timestamp": datetime.now(timezone.utc).isoformat()}
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_decisions(path: Path = DEFAULT_LOG_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
