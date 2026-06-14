#!/usr/bin/env python3
"""
Score strategies from imported backtest rows.

Reads the latest backtest per spec_id from the backtests table, converts
metrics to ScoringInput, runs the scoring engine, and writes results to
scoring_results.  Only specs that have at least one imported backtest row
are included.

Usage
-----
Score all specs with backtest data:
    python research/scoring/score_from_backtests.py --all

Score a single spec:
    python research/scoring/score_from_backtests.py --spec-id 3

Dry-run (parse and score without writing to scoring_results):
    python research/scoring/score_from_backtests.py --all --dry-run

Label a batch run for display identification:
    python research/scoring/score_from_backtests.py --all --run-label june_batch_01

Combined:
    python research/scoring/score_from_backtests.py --spec-id 3 --run-label initial_score --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB = _PROJECT_ROOT / "database" / "hermes_research.db"


# ---------------------------------------------------------------------------
# Query: latest backtest per spec, INNER JOIN (backtest required)
# ---------------------------------------------------------------------------

_QUERY_ALL = """
    SELECT
        ss.spec_id,
        ss.spec_name,
        b.backtest_id,
        b.data_start_date,
        b.data_end_date,
        b.profit_factor,
        b.sharpe_ratio,
        b.max_drawdown_pct     AS max_drawdown,
        b.win_rate,
        b.total_trades         AS trades,
        b.expectancy_per_trade AS expectancy
    FROM strategy_specs ss
    INNER JOIN backtests b ON b.backtest_id = (
        SELECT MAX(backtest_id) FROM backtests WHERE spec_id = ss.spec_id
    )
    WHERE ss.status NOT IN ('approved', 'rejected')
    ORDER BY ss.spec_id
"""

_QUERY_ONE = _QUERY_ALL.replace(
    "ORDER BY ss.spec_id",
    "AND ss.spec_id = ? ORDER BY ss.spec_id",
)


# ---------------------------------------------------------------------------
# Build ScoringInput dicts from query rows
# ---------------------------------------------------------------------------

_BACKTEST_COLS = {
    "profit_factor", "sharpe_ratio", "max_drawdown",
    "win_rate", "trades", "expectancy",
}


def _rows_to_inputs(rows: List[Any], cols: List[str]) -> List[Dict]:
    """Return list of {spec_id, spec_name, backtest_id, date_range, bt_dict}."""
    result = []
    for row in rows:
        r = {cols[i]: row[i] for i in range(len(cols))}
        bt = {k: v for k, v in r.items() if k in _BACKTEST_COLS and v is not None}
        result.append({
            "spec_id":      r["spec_id"],
            "spec_name":    r["spec_name"] or f"spec_{r['spec_id']}",
            "backtest_id":  r["backtest_id"],
            "date_range":   f"{r['data_start_date'] or '?'} – {r['data_end_date'] or '?'}",
            "bt":           bt,
        })
    return result


# ---------------------------------------------------------------------------
# Score one entry, optionally save
# ---------------------------------------------------------------------------

def _score_entry(
    entry: Dict,
    conn: sqlite3.Connection,
    save: bool,
) -> Dict:
    """Score one backtest entry and optionally persist to scoring_results."""
    from research.scoring.scoring import ScoringInput, score, save_scoring_result

    inp = ScoringInput(spec_id=entry["spec_id"], backtest=entry["bt"])

    try:
        result = score(inp)
    except Exception as exc:
        return {
            "spec_id":    entry["spec_id"],
            "spec_name":  entry["spec_name"],
            "score":      None,
            "grade":      "ERROR",
            "rec":        "ERROR",
            "gates":      [],
            "saved":      False,
            "error":      str(exc),
        }

    saved = False
    error: Optional[str] = None
    if save:
        try:
            save_scoring_result(conn, result)
            saved = True
        except Exception as exc:
            error = str(exc)

    return {
        "spec_id":   entry["spec_id"],
        "spec_name": entry["spec_name"],
        "score":     result.composite_score,
        "grade":     result.grade,
        "rec":       result.recommendation,
        "gates":     result.gate_failures,
        "saved":     saved,
        "error":     error,
    }


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_GRADE_COLOUR = {
    "A+": "A+", "A": "A ", "B": "B ", "C": "C ", "D": "D ",
    "Reject": "RJ", "ERROR": "!!"
}


def _print_result(r: Dict, label: str) -> None:
    prefix = f"[{label}] " if label else ""
    grade  = _GRADE_COLOUR.get(r["grade"], r["grade"])
    score  = f"{r['score']:.1f}" if r["score"] is not None else " --- "
    status = "saved" if r["saved"] else ("dry-run" if not r["error"] else f"ERROR: {r['error']}")
    gates  = f"  gates={r['gates']}" if r["gates"] else ""
    print(f"  {prefix}spec_id={r['spec_id']:>3}  {r['spec_name']:<30}  "
          f"score={score:>5}  grade={grade}  {r['rec']:<22}  [{status}]{gates}")


def _print_summary(results: List[Dict], label: str, dry_run: bool) -> None:
    total  = len(results)
    errors = sum(1 for r in results if r["error"])
    saved  = sum(1 for r in results if r["saved"])

    by_grade: Dict[str, int] = {}
    by_rec:   Dict[str, int] = {}
    for r in results:
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
        by_rec[r["rec"]]     = by_rec.get(r["rec"], 0) + 1

    mode = "DRY RUN" if dry_run else ("OK" if errors == 0 else "COMPLETED WITH ERRORS")
    lbl  = f" [{label}]" if label else ""
    print(f"\n--- Summary{lbl} ---")
    print(f"  Total scored : {total}")
    if dry_run:
        print(f"  Written      : 0 (dry-run)")
    else:
        print(f"  Saved        : {saved}")
        print(f"  Errors       : {errors}")
    print()
    for grade, n in sorted(by_grade.items()):
        print(f"  Grade {grade:<6} : {n}")
    print()
    for rec, n in sorted(by_rec.items()):
        print(f"  {rec:<22} : {n}")
    print(f"\n  Status       : {mode}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(
    db_path:   Path,
    spec_id:   Optional[int],
    dry_run:   bool,
    run_label: str,
) -> int:
    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        if spec_id is not None:
            cur = conn.execute(_QUERY_ONE, (spec_id,))
        else:
            cur = conn.execute(_QUERY_ALL)

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        entries = _rows_to_inputs(rows, cols)

        scope = f"spec_id={spec_id}" if spec_id is not None else "all specs with backtest data"
        lbl   = f" [{run_label}]" if run_label else ""
        mode  = "DRY RUN (no writes)" if dry_run else "LIVE"
        print(f"Hermes Score-from-Backtests{lbl}")
        print(f"  DB    : {db_path}")
        print(f"  Mode  : {mode}")
        print(f"  Scope : {scope}")
        print(f"  Found : {len(entries)} spec(s) with backtest data")
        print()

        if not entries:
            print("  Nothing to score — import backtest data first.")
            print("  Usage: python connectors/ninjatrader/backtest_ingestor.py --help")
            return 0

        save = not dry_run
        results = []
        for entry in entries:
            r = _score_entry(entry, conn, save=save)
            _print_result(r, run_label)
            results.append(r)

        _print_summary(results, run_label, dry_run)

        errors = sum(1 for r in results if r["error"])
        return 0 if errors == 0 else 1

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score strategies from imported backtest rows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--spec-id",
        type=int,
        metavar="ID",
        help="Score one spec by strategy_specs.spec_id",
    )
    scope.add_argument(
        "--all",
        action="store_true",
        help="Score all specs that have at least one backtest row",
    )

    parser.add_argument(
        "--run-label",
        default="",
        metavar="LABEL",
        help="Optional label for this scoring run (display only, no DB write)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and score without writing to scoring_results",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        metavar="PATH",
        help="Path to hermes_research.db",
    )

    args = parser.parse_args()
    spec_id = args.spec_id if not args.all else None

    sys.exit(run(Path(args.db), spec_id, args.dry_run, args.run_label))


if __name__ == "__main__":
    main()
