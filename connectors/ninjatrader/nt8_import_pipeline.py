#!/usr/bin/env python3
"""
NT8 Import Pipeline -- connectors/ninjatrader/nt8_import_pipeline.py

End-to-end pipeline: probe -> validate -> import -> score -> verify.

No live trading. No broker connection. No order placement. No ATM control.
No strategy promotion. Human approval gate remains mandatory.

Modes
-----
--probe-only  Inspect file(s) only. No DB connection required. No spec-id required.
--dry-run     Probe + validate spec exists in DB. No writes.
(normal)      Full pipeline: probe -> validate -> import -> score -> verify.

Usage
-----
Probe only (no DB needed):
    python connectors/ninjatrader/nt8_import_pipeline.py --probe-only \\
        --summary path/to/summary.csv --trade-list path/to/trades.csv

Dry-run (probe + validate, no writes):
    python connectors/ninjatrader/nt8_import_pipeline.py --dry-run \\
        --summary path/to/summary.csv --trade-list path/to/trades.csv \\
        --spec-id 3

Full import:
    python connectors/ninjatrader/nt8_import_pipeline.py \\
        --summary path/to/summary.csv --trade-list path/to/trades.csv \\
        --spec-id 3 --initial-capital 50000 --run-label es_vwap_v001
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from connectors.ninjatrader.backtest_ingestor import (  # noqa: E402
    _ensure_dedup_index,
    _probe_lines,
    import_backtest_summary,
    import_trade_list,
    probe_summary,
    probe_trade_list,
)
from research.scoring.runner import run_for_spec        # noqa: E402
from api.queries import decision_queue, research_rankings  # noqa: E402

DEFAULT_DB = _PROJECT_ROOT / "database" / "hermes_research.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section(n: int, title: str) -> None:
    print(f"=== Step {n}: {title} ===")


def _halt(msg: str) -> None:
    print(f"  [HALT] {msg}")
    print()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Step 1: Probe
# ---------------------------------------------------------------------------

def step_probe(
    summary_path: Optional[Path],
    trade_list_path: Optional[Path],
    initial_capital: Optional[float],
) -> bool:
    """Inspect file(s) without touching the DB. Returns True if no FAIL verdict."""
    _section(1, "Probe")
    any_fail = False

    if summary_path:
        r = probe_summary(summary_path)
        for line in _probe_lines("Performance Summary", r):
            print(line)
        if r["verdict"].startswith("FAIL"):
            any_fail = True

    if trade_list_path:
        r = probe_trade_list(trade_list_path, initial_capital)
        for line in _probe_lines("Trade List", r):
            print(line)
        if r["verdict"].startswith("FAIL"):
            any_fail = True

    return not any_fail


# ---------------------------------------------------------------------------
# Step 2: Validate spec
# ---------------------------------------------------------------------------

def step_validate(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    """Verify spec_id exists in strategy_specs. Returns spec dict or None."""
    _section(2, "Validate Spec")
    row = conn.execute(
        "SELECT spec_id, spec_name, status FROM strategy_specs WHERE spec_id = ?",
        (spec_id,),
    ).fetchone()

    if row is None:
        print(f"  [ERROR] spec_id={spec_id} not found in strategy_specs")
        print("  Verdict: FAIL -- create the spec first, then run the pipeline")
        print()
        return None

    spec = {"spec_id": row[0], "spec_name": row[1], "status": row[2]}
    print(f"  spec_id={spec['spec_id']}  name={spec['spec_name']}  status={spec['status']}")
    print("  Verdict: READY")
    print()
    return spec


# ---------------------------------------------------------------------------
# Step 3: Import backtest
# ---------------------------------------------------------------------------

def step_import(
    conn: sqlite3.Connection,
    summary_path: Optional[Path],
    trade_list_path: Optional[Path],
    spec_id: int,
    initial_capital: Optional[float],
) -> Optional[int]:
    """Import summary + trade list. Returns backtest_id (new or existing)."""
    _section(3, "Import Backtest")
    backtest_id: Optional[int] = None

    if summary_path:
        backtest_id, errs = import_backtest_summary(
            conn,
            path=summary_path,
            spec_id=spec_id,
            initial_capital=initial_capital,
            dry_run=False,
        )
        if backtest_id:
            print(f"  Summary    : backtest_id={backtest_id}  inserted")
        else:
            is_dup = any("Duplicate skipped" in e for e in errs)
            if is_dup:
                row = conn.execute(
                    "SELECT MAX(backtest_id) FROM backtests WHERE spec_id = ?",
                    (spec_id,),
                ).fetchone()
                if row and row[0]:
                    backtest_id = row[0]
                    print(f"  Summary    : duplicate -- using existing backtest_id={backtest_id}")
                else:
                    print("  Summary    : duplicate -- no existing backtest found")
            else:
                print("  Summary    : error -- no backtest_id returned")
        for e in errs:
            lvl = "WARN" if "Duplicate" in e or "Warning" in e else "ERROR"
            print(f"  {lvl:<6}   : {e}")

    if trade_list_path:
        ins, skip, errs = import_trade_list(
            conn,
            path=trade_list_path,
            spec_id=spec_id,
            backtest_id=backtest_id,
            initial_capital=initial_capital,
            dry_run=False,
        )
        if backtest_id:
            print(f"  Trade list : backtest_id={backtest_id}  {ins} trade(s) attached")
        else:
            print(f"  Trade list : {ins} backtest row(s) from trade list  {skip} duplicate(s)")
        for e in errs:
            lvl = "WARN" if "Warning" in e else "ERROR"
            print(f"  {lvl:<6}   : {e}")

    # Resolve backtest_id if still unknown (trade-list-only path)
    if backtest_id is None:
        row = conn.execute(
            "SELECT MAX(backtest_id) FROM backtests WHERE spec_id = ?", (spec_id,)
        ).fetchone()
        if row and row[0]:
            backtest_id = row[0]

    print()
    return backtest_id


# ---------------------------------------------------------------------------
# Step 4: Score
# ---------------------------------------------------------------------------

def step_score(conn: sqlite3.Connection, spec_id: int) -> Any:
    """Score from latest backtest. Returns RunResult or None on failure."""
    _section(4, "Score")
    try:
        result = run_for_spec(conn, spec_id, save=True)
        sr = result.scoring_result
        print(
            f"  score={sr.composite_score}  grade={sr.grade}"
            f"  recommendation={sr.recommendation}  [saved]"
        )
        print()
        return result
    except Exception as exc:
        print(f"  [ERROR] Scoring failed: {exc}")
        print()
        return None


# ---------------------------------------------------------------------------
# Step 5: Verify
# ---------------------------------------------------------------------------

def step_verify(conn: sqlite3.Connection, spec_id: int) -> Dict[str, Any]:
    """Check ranking position and decision queue status."""
    _section(5, "Verify")

    rankings  = research_rankings(conn)
    rank_row  = next(
        (r for r in rankings.get("items", []) if r["spec_id"] == spec_id), None
    )
    total = rankings.get("count", 0)

    if rank_row:
        print(
            f"  Ranking    : rank={rank_row['rank']} of {total}"
            f"  score={rank_row.get('composite_score')}  grade={rank_row.get('grade')}"
        )
    else:
        print(f"  Ranking    : spec_id={spec_id} not found in rankings")

    queue     = decision_queue(conn)
    queue_row = next(
        (r for r in queue.get("items", []) if r["spec_id"] == spec_id), None
    )

    if queue_row:
        print(
            f"  Queue      : status={queue_row.get('status')}"
            f"  recommendation={queue_row.get('recommendation')}"
        )
        print(f"  Next action: {queue_row.get('next_action')}")
    else:
        print(f"  Queue      : spec_id={spec_id} not found in decision queue")

    print()

    return {
        "rank":           rank_row.get("rank")            if rank_row  else None,
        "total":          total,
        "score":          rank_row.get("composite_score") if rank_row  else None,
        "grade":          rank_row.get("grade")           if rank_row  else None,
        "status":         queue_row.get("status")         if queue_row else None,
        "recommendation": queue_row.get("recommendation") if queue_row else None,
        "next_action":    queue_row.get("next_action")    if queue_row else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "NT8 Import Pipeline: probe -> validate -> import -> score -> verify. "
            "No live trading. No broker connection. No order placement. "
            "Human approval required."
        ),
    )
    parser.add_argument("--summary",         metavar="PATH",  help="NT8 Performance Summary CSV")
    parser.add_argument("--trade-list",      metavar="PATH",  help="NT8 Trade List CSV")
    parser.add_argument("--spec-id",         type=int,        help="strategy_specs.spec_id")
    parser.add_argument("--initial-capital", type=float,      help="Starting account value for equity curve")
    parser.add_argument("--run-label",       default=None,    help="Display label for this run")
    parser.add_argument("--db",              default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--probe-only",      action="store_true",
                        help="Inspect file(s) only. No DB required. No spec-id required.")
    parser.add_argument("--dry-run",         action="store_true",
                        help="Probe + validate spec. No writes.")
    args = parser.parse_args()

    if not args.summary and not args.trade_list:
        parser.error("At least one of --summary or --trade-list is required")

    summary_path    = Path(args.summary)    if args.summary    else None
    trade_list_path = Path(args.trade_list) if args.trade_list else None

    mode  = "PROBE-ONLY" if args.probe_only else ("DRY-RUN" if args.dry_run else "NORMAL")
    label = f" [{args.run_label}]" if args.run_label else ""

    print(f"Hermes NT8 Import Pipeline{label}")
    if summary_path:    print(f"  Summary    : {summary_path}")
    if trade_list_path: print(f"  Trade list : {trade_list_path}")
    if args.spec_id:    print(f"  spec-id    : {args.spec_id}")
    print(f"  Mode       : {mode}")
    print()

    # ── PROBE-ONLY ────────────────────────────────────────────────────────────
    if args.probe_only:
        ok = step_probe(summary_path, trade_list_path, args.initial_capital)
        sys.exit(0 if ok else 1)

    # ── spec-id required from here ────────────────────────────────────────────
    if args.spec_id is None:
        parser.error("--spec-id is required (use --probe-only to inspect without a spec-id)")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    try:
        # Step 1: Probe
        ok = step_probe(summary_path, trade_list_path, args.initial_capital)
        if not ok:
            _halt("Probe FAILED -- fix column mapping before importing")

        # Step 2: Validate
        spec = step_validate(conn, args.spec_id)
        if spec is None:
            _halt("Spec not found -- import the spec first, then run the pipeline")

        if args.dry_run:
            print("=== DRY-RUN complete: probe and validate passed. No writes. ===")
            print("  Run without --dry-run to proceed with import, score, and verify.")
            sys.exit(0)

        # Step 3: Import
        _ensure_dedup_index(conn)
        backtest_id = step_import(
            conn, summary_path, trade_list_path, args.spec_id, args.initial_capital
        )
        if backtest_id is None:
            _halt("Import produced no backtest_id -- check errors above")

        # Step 4: Score
        score_result = step_score(conn, args.spec_id)
        if score_result is None:
            _halt("Scoring failed -- backtest data may be incomplete")

        # Step 5: Verify
        verify = step_verify(conn, args.spec_id)

        # Summary
        print("=== Pipeline complete ===")
        sr = score_result.scoring_result
        print(f"  Backtest ID : {backtest_id}")
        print(f"  Score       : {sr.composite_score}  grade={sr.grade}")
        if verify.get("rank"):
            print(f"  Rank        : {verify['rank']} of {verify['total']}")
        print(f"  Status      : {verify.get('status', 'REVIEW_REQUIRED')}")
        print(f"  Next action : {verify.get('next_action', 'Pending review')}")
        print()
        print("  Human approval required before any strategy advances beyond research.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
