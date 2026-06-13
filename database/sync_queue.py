#!/usr/bin/env python3
"""
hermes_research_sync.py

Syncs the research pipeline to the database and dashboard:

1. Reads strategy specs from research/strategy_queue/
2. Registers / updates strategy ideas and specs in database/
3. Updates AI Learning Brain summaries
4. Generates dashboard_state.json for the dashboard

This is the glue between Strategy Factory -> Database -> AI Learning Brain -> Dashboard.
"""

import sqlite3
import json
import re
import os
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "database" / "hermes_research.db"
QUEUE_DIR = BASE_DIR / "research" / "strategy_queue"
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

DASHBOARD_STATE_PATH = REPORTS_DIR / "dashboard_state.json"

MARKET_PATTERNS = {
    r"MNQ": ("futures", "MNQ"),
    r"MGC": ("futures", "MGC"),
    r"BTC": ("crypto", "BTCUSDT"),
    r"SPY": ("stocks", "SPY"),
    r"NQ": ("futures", "NQ"),
    r"ES": ("futures", "ES"),
    r"GC": ("futures", "GC"),
    r"CL": ("futures", "CL"),
    r"ETH": ("crypto", "ETHUSDT"),
}

STATUS_MAP = {
    "draft": "spec_created",
    "pending": "pending",
    "approved": "approved",
    "rejected": "rejected",
}


def classify_market(spec_id: str):
    for pat, cls in MARKET_PATTERNS.items():
        if pat in spec_id:
            return cls
    return ("unknown", "UNKNOWN")


def parse_spec(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta = {
        "spec_id": path.stem,
        "file": str(path.relative_to(BASE_DIR)),
        "market": classify_market(path.stem),
        "status": "pending",
        "name": path.stem,
        "timeframe": None,
        "session": None,
        "type": None,
        "edge_hypothesis": None,
        "failure_conditions": None,
        "optimization_variables": [],
    }

    m = re.search(r"- \*\*Spec ID\*\*: (.+)", text)
    if m:
        meta["spec_id"] = m.group(1).strip()
        meta["name"] = meta["spec_id"]

    m = re.search(r"- \*\*Status\*\*: (.+)", text)
    if m:
        meta["status"] = m.group(1).strip().lower()

    m = re.search(r"- \*\*Asset Class\*\*: (.+)", text)
    if m:
        meta["asset_class"] = m.group(1).strip()
    else:
        meta["asset_class"] = meta["market"][0]

    m = re.search(r"- \*\*Symbol\*\*: (.+)", text)
    if m:
        meta["symbol"] = m.group(1).strip()
    else:
        meta["symbol"] = meta["market"][1]

    m = re.search(r"- \*\*Timeframe\*\*: (.+)", text)
    if m:
        meta["timeframe"] = m.group(1).strip()

    m = re.search(r"- \*\*Session\*\*: (.+)", text)
    if m:
        meta["session"] = m.group(1).strip()

    m = re.search(r"## Overview\n\n(.+?)(?:\n##|\Z)", text, re.S)
    if m:
        meta["description"] = m.group(1).strip()

    m = re.search(r"## Edge Hypothesis\n\n(.+?)(?:\n##|\Z)", text, re.S)
    if m:
        meta["edge_hypothesis"] = m.group(1).strip()

    m = re.search(r"## Failure Conditions\n\n(.+?)(?:\n##|\Z)", text, re.S)
    if m:
        meta["failure_conditions"] = m.group(1).strip()
        if meta["status"] != STATUS_MAP.get(meta["status"], meta["status"]):
            meta["status"] = STATUS_MAP.get(meta["status"], meta["status"])

    m = re.search(r"\| Parameter \| Range \| Step \|\n\|[-\s|]+\|\n([\s\S]+?)(?:\n##|\Z)", text)
    if m:
        for line in m.group(1).splitlines():
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) == 3:
                meta["optimization_variables"].append(
                    {"name": parts[0], "range": parts[1], "step": parts[2]}
                )

    # Heuristic strategy type tag from spec id
    meta["strategy_type"] = re.sub(r"_v\d+$", "", meta["name"])
    return meta


def init_db(conn: sqlite3.Connection):
    schema_path = BASE_DIR / "database" / "init.sql"
    if schema_path.exists():
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()


def ensure_market(conn: sqlite3.Connection, asset_class, symbol, name):
    cur = conn.execute(
        "SELECT market_id FROM markets WHERE symbol = ? AND asset_class = ?",
        (symbol, asset_class),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        """INSERT INTO markets (asset_class, symbol, name, is_active, current_regime)
           VALUES (?, ?, ?, 1, 'unknown')""",
        (asset_class, symbol, name),
    )
    conn.commit()
    return cur.lastrowid


def upsert_strategy_idea(conn: sqlite3.Connection, spec: dict):
    now = datetime.utcnow().isoformat()
    asset_class = spec.get("asset_class", "unknown")
    symbol = spec.get("symbol", "UNKNOWN")
    market_id = ensure_market(conn, asset_class, symbol, spec["name"])

    cur = conn.execute(
        "SELECT idea_id FROM strategy_ideas WHERE idea_name = ?",
        (spec["name"],),
    )
    row = cur.fetchone()
    if row:
        conn.execute(
            """UPDATE strategy_ideas
               SET status = ?, updated_at = ?
             WHERE idea_id = ?""",
            (spec["status"], now, row[0]),
        )
        return row[0]

    cur = conn.execute(
        """INSERT INTO strategy_ideas
             (idea_name, market_id, asset_class, symbol, timeframe,
              strategy_type, description, hypothesis, failure_conditions,
              suggested_filters, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            spec["name"],
            market_id,
            asset_class,
            symbol,
            spec.get("timeframe"),
            spec.get("strategy_type"),
            spec.get("description"),
            spec.get("edge_hypothesis"),
            spec.get("failure_conditions"),
            "null",
            spec["status"],
            now,
            now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def upsert_strategy_spec(conn: sqlite3.Connection, spec: dict, idea_id: int):
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "SELECT spec_id FROM strategy_specs WHERE spec_name = ?",
        (spec["spec_id"],),
    )
    row = cur.fetchone()
    payload = (
        spec["spec_id"],
        idea_id,
        spec.get("asset_class"),
        spec.get("symbol"),
        spec.get("timeframe"),
        spec.get("session"),
        "Entry rules in spec file",
        "Exit rules in spec file",
        "ATR-based",
        "1.5R",
        "rules in spec file",
        "rules in spec file",
        json.dumps(spec.get("optimization_variables", [])),
        spec.get("edge_hypothesis"),
        spec.get("failure_conditions"),
        now,
    )
    if row:
        conn.execute(
            """UPDATE strategy_specs
               SET spec_name = ?, asset_class = ?, symbol = ?, timeframe = ?,
                   session = ?, optimization_variables = ?, why_edge_exists = ?,
                   why_strategy_may_fail = ?, updated_at = ?, status = ?
             WHERE spec_id = ?""",
            (
                payload[0],
                payload[3],
                payload[4],
                payload[5],
                payload[6],
                payload[11],
                payload[13],
                payload[14],
                now,
                spec["status"],
                row[0],
            ),
        )
        return row[0]

    cur = conn.execute(
        """INSERT INTO strategy_specs
             (spec_name, idea_id, asset_class, symbol, timeframe, session,
              entry_rules, exit_rules, stop_loss_type, profit_target_type,
              risk_rules, filters, optimization_variables, why_edge_exists,
              why_strategy_may_fail, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        payload + (spec["status"], now),
    )
    conn.commit()
    return cur.lastrowid


def update_learning_brain(conn: sqlite3.Connection, specs: list[dict], spec_id_map: dict[str, int]):
    if spec_id_map is None:
        spec_id_map = {}
    now = datetime.utcnow().isoformat()
    for spec in specs:
        int_spec_id = spec_id_map.get(spec["spec_id"])
        note = (
            f"Processed {spec['spec_id']}. "
            f"Asset class: {spec.get('asset_class')}. "
            f"Strategy type: {spec.get('strategy_type')}. "
            f"Edge: {spec.get('edge_hypothesis', 'unknown')}"
        )
        conn.execute(
            """INSERT INTO research_notes
                 (spec_id, note_type, content, tags, confidence, created_at)
               VALUES (?, 'observation', ?, ?, 50, ?)""",
            (
                int_spec_id,
                note,
                json.dumps(["strategy_factory", spec.get("asset_class"), spec.get("strategy_type")]),
                now,
            ),
        )
    conn.commit()


def build_dashboard_state(conn: sqlite3.Connection) -> dict:
    cur = conn.execute(
        """SELECT asset_class, COUNT(*) FROM strategy_specs
           GROUP BY asset_class"""
    )
    by_asset = {row[0]: row[1] for row in cur.fetchall()}

    cur = conn.execute(
        """SELECT COUNT(*) FROM strategy_ideas"""
    )
    total_ideas = cur.fetchone()[0]

    cur = conn.execute(
        """SELECT COUNT(*) FROM strategy_specs"""
    )
    total_specs = cur.fetchone()[0]

    cur = conn.execute(
        """SELECT COUNT(*) FROM rejected_strategies"""
    )
    total_rejected = cur.fetchone()[0]

    cur = conn.execute(
        """SELECT approval_reason, strategy_name, symbol, asset_class
           FROM approved_strategies LIMIT 1"""
    )
    best = cur.fetchone()

    return {
        "generated": total_ideas,
        "tested": total_specs,
        "rejected": total_rejected,
        "approved": 1 if best else 0,
        "bestPF": None,
        "bestSharpe": None,
        "lowestDD": None,
        "bestApproved": {
            "id": None,
            "name": best[1] if best else "—",
            "symbol": best[2] if best else "—",
            "asset": best[3] if best else "—",
        },
        "byAsset": by_asset,
        "lastUpdated": datetime.utcnow().isoformat(),
    }


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    specs = []
    spec_id_map = {}
    for path in sorted(QUEUE_DIR.glob("*.md")):
        spec = parse_spec(path)
        specs.append(spec)
        idea_id = upsert_strategy_idea(conn, spec)
        spec_id = upsert_strategy_spec(conn, spec, idea_id)
        spec_id_map[spec["spec_id"]] = spec_id

    update_learning_brain(conn, specs, spec_id_map)

    state = build_dashboard_state(conn)
    DASHBOARD_STATE_PATH.write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )
    conn.close()

    print(f"Synced {len(specs)} specs to database.")
    print(f"Dashboard state written to: {DASHBOARD_STATE_PATH}")
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
