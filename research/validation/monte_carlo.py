#!/usr/bin/env python3
"""
Monte Carlo Robustness Engine -- research/validation/monte_carlo.py

Answers: "How likely is this strategy to survive a different sequence
of wins and losses?"

Method: Bootstrap resampling (sample N trades with replacement)
------
1. Load trade_list_json from the latest in-sample backtest.
2. Resample N trades with replacement -- 1000 simulations by default.
3. For each simulation:
   - Replay the equity curve from initial_capital
   - Measure peak-to-trough drawdown in dollars
   - Record whether final equity > initial_capital (probability_positive)
4. Survival = % of simulations where max drawdown < ruin threshold.
5. monte_carlo_score = survival_rate (0.0-1.0).
6. monte_carlo_pass  = survival_rate >= 0.85.

Bootstrap vs shuffle: sampling with replacement means each simulation
draws a different subset of trades. Total P&L and drawdown vary across
simulations, making probability_positive and drawdown distribution
metrics meaningful.

Ruin threshold
--------------
  Use prop-firm drawdown if available per strategy spec.
  Otherwise: initial_capital * 5%  (prop-firm standard fallback).

Survival tiers
--------------
  PASS     >= 85%
  WARNING   70-84%
  FAIL      < 70%

Results are written to the existing monte_carlo_score / monte_carlo_pass
fields in the latest scoring_results row. No schema changes.

What it does NOT do
-------------------
- Does not alter trade P&L values.
- Does not simulate slippage or commission variation.
- Does not replace walk-forward testing.
- Does not promote a strategy past REVIEW_REQUIRED.

Usage
-----
    python -m research.validation.monte_carlo --spec-id N
    python -m research.validation.monte_carlo --all
    python -m research.validation.monte_carlo --spec-id N --dry-run
    python -m research.validation.monte_carlo --spec-id N --simulations 2000
    python -m research.validation.monte_carlo --spec-id N --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB      = _PROJECT_ROOT / "database" / "hermes_research.db"
DEFAULT_REPORTS = _PROJECT_ROOT / "reports" / "validation"

_SURVIVAL_GATE   = 0.85   # >= 85%: PASS
_WARN_GATE       = 0.70   # 70-84%: WARNING  |  < 70%: FAIL
_DEFAULT_SIMS    = 1000
_PROP_FIRM_DD    = 0.05   # prop-firm standard: 5% of initial capital


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MCResult:
    spec_id:              int
    spec_name:            str
    backtest_id:          int
    scoring_id:           int
    simulations:          int
    trade_count:          int
    initial_capital:      float
    ruin_dollar_threshold: float   # dollar amount used as ruin trigger
    ruin_pct_threshold:   float    # fraction (e.g. 0.05)
    survival_count:       int
    survival_rate:        float
    probability_positive: float    # % of sims where final equity > initial_capital
    monte_carlo_score:    float    # == survival_rate
    monte_carlo_pass:     bool     # survival_rate >= _SURVIVAL_GATE
    worst_drawdown:       float    # worst max-DD as fraction across all sims
    median_drawdown:      float    # median max-DD as fraction
    p95_drawdown:         float    # 95th-percentile max-DD as fraction
    ran_at:               str


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fetch_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT spec_id, spec_name FROM strategy_specs WHERE spec_id = ?",
        (spec_id,),
    ).fetchone()
    return {"spec_id": row[0], "spec_name": row[1]} if row else None


def _fetch_latest_is_backtest(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    """Latest in-sample backtest with trade list and initial_capital."""
    row = conn.execute("""
        SELECT backtest_id, trade_list_json, initial_capital, max_drawdown_pct
        FROM backtests
        WHERE spec_id = ?
          AND is_in_sample = 1
          AND trade_list_json IS NOT NULL
          AND initial_capital IS NOT NULL
        ORDER BY backtest_id DESC
        LIMIT 1
    """, (spec_id,)).fetchone()
    if row is None:
        return None
    return {
        "backtest_id":      row[0],
        "trade_list_json":  row[1],
        "initial_capital":  row[2],
        "max_drawdown_pct": row[3],
    }


def _fetch_latest_scoring(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT scoring_id, composite_score, grade,
               monte_carlo_score, monte_carlo_pass
        FROM scoring_results
        WHERE spec_id = ?
        ORDER BY scoring_id DESC
        LIMIT 1
    """, (spec_id,)).fetchone()
    if row is None:
        return None
    return {
        "scoring_id":        row[0],
        "composite_score":   row[1],
        "grade":             row[2],
        "monte_carlo_score": row[3],
        "monte_carlo_pass":  row[4],
    }


def _all_spec_ids(conn: sqlite3.Connection) -> List[int]:
    rows = conn.execute(
        "SELECT spec_id FROM strategy_specs ORDER BY spec_id"
    ).fetchall()
    return [r[0] for r in rows]


def _write_result(conn: sqlite3.Connection, r: MCResult) -> None:
    conn.execute("""
        UPDATE scoring_results
        SET monte_carlo_score = ?,
            monte_carlo_pass  = ?
        WHERE scoring_id = ?
    """, (r.monte_carlo_score, 1 if r.monte_carlo_pass else 0, r.scoring_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Ruin threshold
# ---------------------------------------------------------------------------

def _get_ruin_threshold(bt: Dict) -> Tuple[float, float]:
    """
    Returns (ruin_dollar, ruin_pct).

    Priority:
      1. Prop-firm limit stored on the strategy spec (future extension point).
      2. Fallback: initial_capital * 5% (prop-firm industry standard).
    """
    capital = float(bt["initial_capital"])
    # Future: read strategy_specs.prop_firm_max_dd if column added
    ruin_pct    = _PROP_FIRM_DD
    ruin_dollar = capital * ruin_pct
    return ruin_dollar, ruin_pct


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def _simulate(
    pnl_list:        List[float],
    initial_capital: float,
    simulations:     int,
    ruin_dollar:     float,
    seed:            Optional[int],
) -> Tuple[float, float, float, float, int, float]:
    """
    Bootstrap resample N trades with replacement, N simulations.

    Returns:
        (survival_rate, worst_dd_pct, median_dd_pct, p95_dd_pct,
         survival_count, probability_positive)
    """
    rng = random.Random(seed)
    n   = len(pnl_list)
    drawdowns_dollar: List[float] = []
    positive_count = 0

    for _ in range(simulations):
        sample = rng.choices(pnl_list, k=n)   # bootstrap: with replacement

        equity = [initial_capital]
        for pnl in sample:
            equity.append(equity[-1] + pnl)

        # Peak-to-trough drawdown in dollars
        peak   = equity[0]
        max_dd = 0.0
        for e in equity:
            if e > peak:
                peak = e
            dd = peak - e
            if dd > max_dd:
                max_dd = dd
        drawdowns_dollar.append(max_dd)

        if equity[-1] > initial_capital:
            positive_count += 1

    drawdowns_dollar.sort()
    survival_count = sum(1 for dd in drawdowns_dollar if dd < ruin_dollar)
    survival_rate  = survival_count / simulations
    prob_positive  = positive_count / simulations

    # Convert dollar drawdowns to fractions for reporting
    cap       = initial_capital if initial_capital > 0 else 1.0
    worst_pct  = drawdowns_dollar[-1]                         / cap
    median_pct = drawdowns_dollar[simulations // 2]           / cap
    p95_pct    = drawdowns_dollar[int(simulations * 0.95)]    / cap

    return survival_rate, worst_pct, median_pct, p95_pct, survival_count, prob_positive


def run_mc_for_spec(
    conn:        sqlite3.Connection,
    spec_id:     int,
    simulations: int           = _DEFAULT_SIMS,
    seed:        Optional[int] = None,
    dry_run:     bool          = False,
) -> Tuple[Optional[MCResult], Optional[str]]:
    """
    Run Monte Carlo for one spec. Returns (MCResult, None) on success,
    (None, reason_str) when the spec must be skipped.

    Writes monte_carlo_score / monte_carlo_pass to the latest
    scoring_results row unless dry_run=True.
    """
    spec = _fetch_spec(conn, spec_id)
    if spec is None:
        return None, f"spec_id={spec_id} not found in strategy_specs"

    bt = _fetch_latest_is_backtest(conn, spec_id)
    if bt is None:
        return None, (
            "No in-sample backtest with trade_list_json + initial_capital. "
            "Re-import with --initial-capital to enable Monte Carlo."
        )

    scoring = _fetch_latest_scoring(conn, spec_id)
    if scoring is None:
        return None, "No scoring result -- run score_from_backtests.py first"

    try:
        trades = json.loads(bt["trade_list_json"])
    except (json.JSONDecodeError, TypeError):
        return None, "trade_list_json could not be parsed"

    pnl_list = [float(t["pnl"]) for t in trades if isinstance(t, dict) and "pnl" in t]
    if len(pnl_list) < 2:
        return None, f"Need at least 2 trades for Monte Carlo (found {len(pnl_list)})"

    ruin_dollar, ruin_pct = _get_ruin_threshold(bt)
    capital = float(bt["initial_capital"])

    (survival_rate, worst_dd, median_dd, p95_dd,
     survival_count, prob_positive) = _simulate(
        pnl_list        = pnl_list,
        initial_capital = capital,
        simulations     = simulations,
        ruin_dollar     = ruin_dollar,
        seed            = seed,
    )

    mc_pass = survival_rate >= _SURVIVAL_GATE
    result  = MCResult(
        spec_id               = spec_id,
        spec_name             = spec["spec_name"],
        backtest_id           = bt["backtest_id"],
        scoring_id            = scoring["scoring_id"],
        simulations           = simulations,
        trade_count           = len(pnl_list),
        initial_capital       = capital,
        ruin_dollar_threshold = round(ruin_dollar, 2),
        ruin_pct_threshold    = ruin_pct,
        survival_count        = survival_count,
        survival_rate         = round(survival_rate, 4),
        probability_positive  = round(prob_positive, 4),
        monte_carlo_score     = round(survival_rate, 4),
        monte_carlo_pass      = mc_pass,
        worst_drawdown        = round(worst_dd,  4),
        median_drawdown       = round(median_dd, 4),
        p95_drawdown          = round(p95_dd,    4),
        ran_at                = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    )

    if not dry_run:
        _write_result(conn, result)

    return result, None


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _mc_tier(survival_rate: float, mc_pass: bool) -> Tuple[str, str]:
    """Returns (tier_label, icon)."""
    if mc_pass:
        return "PASS", "+"
    if survival_rate >= _WARN_GATE:
        return "WARNING", "!"
    return "FAIL", "X"


def _verdict_line(r: MCResult) -> str:
    tier, icon = _mc_tier(r.survival_rate, r.monte_carlo_pass)
    return (
        f"[{icon}] Monte Carlo {tier}  "
        f"survival={r.survival_rate:.1%}  "
        f"(PASS>={_SURVIVAL_GATE:.0%}  WARN>={_WARN_GATE:.0%}  "
        f"ruin=${r.ruin_dollar_threshold:,.0f} / {r.ruin_pct_threshold:.0%})"
    )


def _to_markdown(r: MCResult, dry_run: bool = False) -> str:
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    tier, _ = _mc_tier(r.survival_rate, r.monte_carlo_pass)

    p(f"# Monte Carlo Report: {r.spec_name}")
    p(f"**Run date:** {r.ran_at[:10]}")
    p(f"**spec_id:** {r.spec_id}  |  "
      f"**backtest_id:** {r.backtest_id}  |  "
      f"**scoring_id:** {r.scoring_id}")
    if dry_run:
        p()
        p("> **DRY-RUN** -- results not written to database")
    p()
    p("---")
    p()
    p(f"## Result: {tier}")
    p()
    p(f"| Metric | Value |")
    p(f"|--------|-------|")
    p(f"| Method | Bootstrap resampling (with replacement) |")
    p(f"| Simulations | {r.simulations:,} |")
    p(f"| Trade count (pool) | {r.trade_count} |")
    p(f"| Initial capital | ${r.initial_capital:,.0f} |")
    p(f"| Ruin threshold | ${r.ruin_dollar_threshold:,.0f} "
      f"({r.ruin_pct_threshold:.0%} of capital) |")
    p(f"| Survival gate | {_SURVIVAL_GATE:.0%} PASS  /  {_WARN_GATE:.0%} WARNING |")
    p(f"| **Survival count** | **{r.survival_count:,} / {r.simulations:,}** |")
    p(f"| **Survival rate** | **{r.survival_rate:.1%}** |")
    p(f"| **Probability positive** | **{r.probability_positive:.1%}** |")
    p(f"| monte_carlo_score | {r.monte_carlo_score:.4f} |")
    p(f"| monte_carlo_pass | {r.monte_carlo_pass} |")
    p()
    p("## Drawdown Distribution")
    p()
    p(f"| Scenario | Max Drawdown |")
    p(f"|----------|-------------|")
    p(f"| Worst simulation | {r.worst_drawdown:.1%} |")
    p(f"| 95th percentile | {r.p95_drawdown:.1%} |")
    p(f"| Median simulation | {r.median_drawdown:.1%} |")
    p()
    p("---")
    p()
    note = "Results NOT written to DB (dry-run). " if dry_run else \
           "monte_carlo_score and monte_carlo_pass updated in scoring_results. "
    p(f"*Read-only validation. {note}"
      "Human approval required before any strategy advances beyond REVIEW_REQUIRED.*")

    return "\n".join(lines)


def _to_dict(r: MCResult, dry_run: bool = False) -> Dict:
    return {
        "spec_id":               r.spec_id,
        "spec_name":             r.spec_name,
        "backtest_id":           r.backtest_id,
        "scoring_id":            r.scoring_id,
        "dry_run":               dry_run,
        "method":                "bootstrap_resampling",
        "simulations":           r.simulations,
        "trade_count":           r.trade_count,
        "initial_capital":       r.initial_capital,
        "ruin_dollar_threshold": r.ruin_dollar_threshold,
        "ruin_pct_threshold":    r.ruin_pct_threshold,
        "survival_gate":         _SURVIVAL_GATE,
        "warn_gate":             _WARN_GATE,
        "survival_count":        r.survival_count,
        "survival_rate":         r.survival_rate,
        "probability_positive":  r.probability_positive,
        "monte_carlo_score":     r.monte_carlo_score,
        "monte_carlo_pass":      r.monte_carlo_pass,
        "worst_drawdown":        r.worst_drawdown,
        "median_drawdown":       r.median_drawdown,
        "p95_drawdown":          r.p95_drawdown,
        "ran_at":                r.ran_at,
    }


def write_reports(
    r: MCResult, reports_dir: Path, dry_run: bool = False
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = r.ran_at[:10].replace("-", "")
    safe_name = re.sub(r"[^\w\-]", "_", r.spec_name)

    md_path   = reports_dir / f"{safe_name}_monte_carlo_{date_str}.md"
    json_path = reports_dir / f"{safe_name}_monte_carlo_{date_str}.json"

    md_path.write_text(_to_markdown(r, dry_run=dry_run), encoding="utf-8")
    json_path.write_text(
        json.dumps(_to_dict(r, dry_run=dry_run), indent=2), encoding="utf-8"
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_result(r: MCResult, dry_run: bool = False) -> None:
    tag = "  [DRY-RUN]" if dry_run else ""
    print(f"MC: {r.spec_name}  [spec_id={r.spec_id}]{tag}")
    print(f"  Simulations  : {r.simulations:,}  (bootstrap with replacement)")
    print(f"  Trades       : {r.trade_count}  capital=${r.initial_capital:,.0f}")
    print(f"  Ruin gate    : ${r.ruin_dollar_threshold:,.0f}"
          f"  ({r.ruin_pct_threshold:.0%} of capital)")
    print()
    print(f"  Survival     : {r.survival_count:,} / {r.simulations:,}"
          f"  ({r.survival_rate:.1%})")
    print(f"  P(positive)  : {r.probability_positive:.1%}"
          f"  (final equity > initial_capital)")
    print(f"  {_verdict_line(r)}")
    print()
    print(f"  Drawdown distribution")
    print(f"    Worst      : {r.worst_drawdown:.1%}")
    print(f"    95th pct   : {r.p95_drawdown:.1%}")
    print(f"    Median     : {r.median_drawdown:.1%}")
    print()
    if not dry_run:
        print(f"  scoring_results.scoring_id={r.scoring_id} updated")
        print(f"    monte_carlo_score = {r.monte_carlo_score}")
        print(f"    monte_carlo_pass  = {r.monte_carlo_pass}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Monte Carlo Robustness Engine -- bootstrap resampling. "
            "No live trading. No schema changes. Human approval required."
        ),
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec-id", type=int, metavar="ID",
                     help="Run Monte Carlo for one spec")
    grp.add_argument("--all", action="store_true",
                     help="Run Monte Carlo for all specs")
    parser.add_argument("--simulations", type=int, default=_DEFAULT_SIMS,
                        help=f"Number of bootstrap simulations (default {_DEFAULT_SIMS})")
    parser.add_argument("--seed",        type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Compute results but do not write to DB or reports")
    parser.add_argument("--db",          default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS), metavar="DIR")
    args = parser.parse_args()

    db_path     = Path(args.db)
    reports_dir = Path(args.reports_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes Monte Carlo Engine  [{mode}]")
    print(f"  DB          : {db_path}")
    print(f"  Method      : bootstrap resampling (with replacement)")
    print(f"  Simulations : {args.simulations:,}")
    print(f"  Ruin gate   : initial_capital x {_PROP_FIRM_DD:.0%}  (prop-firm standard)")
    print(f"  Survival    : PASS>={_SURVIVAL_GATE:.0%}  WARN>={_WARN_GATE:.0%}  FAIL<{_WARN_GATE:.0%}")
    if args.seed is not None:
        print(f"  Seed        : {args.seed}")
    if not args.dry_run:
        print(f"  Reports dir : {reports_dir}")
    print()

    conn      = sqlite3.connect(str(db_path))
    exit_code = 0

    try:
        spec_ids = [args.spec_id] if args.spec_id is not None else _all_spec_ids(conn)

        if not spec_ids:
            print("No specs found.")
            sys.exit(0)

        results: List[MCResult] = []
        skipped: List[Tuple[int, str]] = []

        for sid in spec_ids:
            result, reason = run_mc_for_spec(
                conn        = conn,
                spec_id     = sid,
                simulations = args.simulations,
                seed        = args.seed,
                dry_run     = args.dry_run,
            )

            if result is None:
                skipped.append((sid, reason))
                print(f"  SKIP spec_id={sid}: {reason}")
                print()
                continue

            _print_result(result, dry_run=args.dry_run)

            if not args.dry_run:
                md_path, json_path = write_reports(result, reports_dir)
                print(f"  Reports")
                print(f"    MD  : {md_path}")
                print(f"    JSON: {json_path}")
                print()

            results.append(result)
            if not result.monte_carlo_pass:
                exit_code = 1

        # Summary for --all
        if args.all and (results or skipped):
            print("-" * 64)
            print("MONTE CARLO SUMMARY")
            print("-" * 64)
            print()
            if results:
                w = max(len(r.spec_name) for r in results)
                print(f"  {'Strategy':<{w}}  Survival  P(+)    Score   Verdict")
                print(f"  {'-'*w}  --------  ------  ------  -------")
                for r in results:
                    tier, icon = _mc_tier(r.survival_rate, r.monte_carlo_pass)
                    print(f"  {r.spec_name:<{w}}  "
                          f"{r.survival_rate:>7.1%}  "
                          f"{r.probability_positive:>5.1%}  "
                          f"{r.monte_carlo_score:.4f}  [{icon}] {tier}")
                print()
            if skipped:
                print(f"  Skipped ({len(skipped)}):")
                for sid, reason in skipped:
                    print(f"    spec_id={sid}: {reason}")
                print()

        if args.dry_run:
            print("DRY-RUN complete. No DB writes. No reports written.")

    finally:
        conn.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
