"""Batch scoring runner.

Scores a list of ScoringInput objects and optionally persists results
to the scoring_results table.  One spec failure does not abort the batch.

Public API
----------
run_batch(inputs, conn=None, save=True) -> BatchSummary
run_from_db(conn, save=True)            -> BatchSummary
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .scoring import ScoringInput, ScoringResult, save_scoring_result, score


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    scoring_result: ScoringResult
    saved:          bool
    error:          Optional[str] = None


@dataclass
class BatchSummary:
    total:             int
    saved:             int
    errors:            int
    by_grade:          Dict[str, int] = field(default_factory=dict)
    by_recommendation: Dict[str, int] = field(default_factory=dict)
    results:           List[RunResult] = field(default_factory=list)

    def print_summary(self) -> None:
        print(f"Batch complete: {self.total} scored, {self.saved} saved, {self.errors} errors")
        for grade, n in sorted(self.by_grade.items()):
            print(f"  {grade:<8} {n}")
        if self.errors:
            for r in self.results:
                if r.error:
                    print(f"  ERROR spec_id={r.scoring_result.spec_id}: {r.error}")


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def run_batch(
    inputs: List[ScoringInput],
    conn:   Optional[sqlite3.Connection] = None,
    save:   bool = True,
) -> BatchSummary:
    """Score each ScoringInput and optionally write to scoring_results.

    Parameters
    ----------
    inputs : list of ScoringInput
    conn   : open sqlite3 connection (required when save=True)
    save   : write results to DB; set False for dry-run / testing

    Returns
    -------
    BatchSummary with per-result detail and aggregate counts.
    """
    if save and conn is None:
        raise ValueError("conn is required when save=True")

    results: List[RunResult] = []
    saved_count = error_count = 0
    by_grade: Dict[str, int]          = {}
    by_rec:   Dict[str, int]          = {}

    for inp in inputs:
        try:
            sr = score(inp)
        except Exception as exc:
            dummy = ScoringResult(
                spec_id=inp.spec_id,
                composite_score=0.0,
                grade="Reject",
                recommendation="Reject",
                component_scores={},
                gate_failures=[str(exc)],
                overfit_warnings=[],
                overfitting_risk=1.0,
                monte_carlo_pass=False,
                walk_forward_pass=False,
                prop_firm_supported=False,
                prop_firm_support={},
            )
            results.append(RunResult(scoring_result=dummy, saved=False, error=str(exc)))
            error_count += 1
            continue

        persisted = False
        err: Optional[str] = None
        if save:
            try:
                save_scoring_result(conn, sr)
                persisted = True
                saved_count += 1
            except Exception as exc:
                err = str(exc)
                error_count += 1

        results.append(RunResult(scoring_result=sr, saved=persisted, error=err))
        by_grade[sr.grade]          = by_grade.get(sr.grade, 0) + 1
        by_rec[sr.recommendation]   = by_rec.get(sr.recommendation, 0) + 1

    return BatchSummary(
        total=len(inputs),
        saved=saved_count,
        errors=error_count,
        by_grade=by_grade,
        by_recommendation=by_rec,
        results=results,
    )


# ---------------------------------------------------------------------------
# DB convenience loader
# ---------------------------------------------------------------------------


def run_from_db(
    conn: sqlite3.Connection,
    save: bool = True,
) -> BatchSummary:
    """Build ScoringInputs from strategy_specs + latest backtests, then score.

    Each spec is joined to its most recent backtest (if any).  Specs with
    no backtest data will have empty backtest dicts — most hard gates will
    fire and they will land as Reject until real data is attached.

    Parameters
    ----------
    conn : open sqlite3 connection
    save : write results to scoring_results (default True)
    """
    cur = conn.execute("""
        SELECT
            ss.spec_id,
            b.profit_factor,
            b.sharpe_ratio,
            b.max_drawdown_pct,
            b.win_rate,
            b.total_trades       AS trades
        FROM strategy_specs ss
        LEFT JOIN backtests b ON b.backtest_id = (
            SELECT MAX(backtest_id) FROM backtests WHERE spec_id = ss.spec_id
        )
        WHERE ss.status NOT IN ('approved', 'rejected')
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]

    inputs: List[ScoringInput] = []
    for row in rows:
        r: Dict[str, Any] = {cols[i]: row[i] for i in range(len(cols))}
        spec_id = r.pop("spec_id")
        bt = {k: v for k, v in r.items() if v is not None}
        inputs.append(ScoringInput(spec_id=spec_id, backtest=bt))

    return run_batch(inputs, conn=conn, save=save)
