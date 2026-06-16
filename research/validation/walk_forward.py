#!/usr/bin/env python3
"""
Walk-Forward Validation Engine -- research/validation/walk_forward.py

Answers: "Did the strategy perform reasonably out-of-sample compared
to in-sample?"

Method
------
Compare the latest in-sample (IS) and out-of-sample (OOS) backtests
for a spec. Compute three retention / penalty components, weight them,
and produce walk_forward_score (0.0-1.0).

Components
----------
  pf_component  = clamp(OOS PF / IS PF, 0, 1)          weight 40%
  exp_component = clamp(OOS exp/trade / IS exp/trade,
                        0, 1)                             weight 40%
  dd_component  = clamp(IS DD% / OOS DD%, 0, 1)
                  if OOS DD > IS DD, else 1.0             weight 20%

  walk_forward_score = 0.40 * pf + 0.40 * exp + 0.20 * dd

Tiers
-----
  PASS     >= 0.70
  WARNING   0.50-0.69
  FAIL      < 0.50

Results are written to walk_forward_score / walk_forward_pass in the
latest scoring_results row. No schema changes.

NOT_RUN
-------
If no OOS backtest exists for the spec, the engine returns NOT_RUN
and does not touch scoring_results.

What it does NOT do
-------------------
- Does not fabricate OOS data.
- Does not replace Monte Carlo testing.
- Does not promote a strategy past REVIEW_REQUIRED.
- Does not run live trading or connect to any broker.

Usage
-----
    python -m research.validation.walk_forward --spec-id N
    python -m research.validation.walk_forward --all
    python -m research.validation.walk_forward --spec-id N --dry-run
"""

from __future__ import annotations

import argparse
import json
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

_PASS_GATE  = 0.70
_WARN_GATE  = 0.50

_PF_WEIGHT  = 0.40
_EXP_WEIGHT = 0.40
_DD_WEIGHT  = 0.20


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class WFResult:
    spec_id:              int
    spec_name:            str
    is_backtest_id:       int
    oos_backtest_id:      int
    scoring_id:           int
    # IS metrics
    is_pf:                float
    is_exp_per_trade:     float
    is_dd_pct:            float
    is_trades:            int
    is_start:             Optional[str]
    is_end:               Optional[str]
    # OOS metrics
    oos_pf:               float
    oos_exp_per_trade:    float
    oos_dd_pct:           float
    oos_trades:           int
    oos_start:            Optional[str]
    oos_end:              Optional[str]
    # Components
    pf_retention:         float
    expectancy_retention: float
    dd_component:         float
    # Score
    walk_forward_score:   float
    walk_forward_pass:    bool
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


def _fetch_latest_bt(
    conn: sqlite3.Connection, spec_id: int, is_in_sample: int
) -> Optional[Dict]:
    row = conn.execute("""
        SELECT backtest_id, profit_factor, expectancy_per_trade,
               max_drawdown_pct, total_trades,
               data_start_date, data_end_date, net_profit
        FROM backtests
        WHERE spec_id = ?
          AND is_in_sample = ?
        ORDER BY backtest_id DESC
        LIMIT 1
    """, (spec_id, is_in_sample)).fetchone()
    if row is None:
        return None
    return {
        "backtest_id":        row[0],
        "profit_factor":      row[1],
        "expectancy_per_trade": row[2],
        "max_drawdown_pct":   row[3],
        "total_trades":       row[4],
        "data_start_date":    row[5],
        "data_end_date":      row[6],
        "net_profit":         row[7],
    }


def _fetch_latest_scoring(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT scoring_id, walk_forward_score, walk_forward_pass
        FROM scoring_results
        WHERE spec_id = ?
        ORDER BY scoring_id DESC
        LIMIT 1
    """, (spec_id,)).fetchone()
    if row is None:
        return None
    return {
        "scoring_id":        row[0],
        "walk_forward_score": row[1],
        "walk_forward_pass":  row[2],
    }


def _all_spec_ids(conn: sqlite3.Connection) -> List[int]:
    return [r[0] for r in conn.execute(
        "SELECT spec_id FROM strategy_specs ORDER BY spec_id"
    ).fetchall()]


def _write_result(conn: sqlite3.Connection, r: WFResult) -> None:
    conn.execute("""
        UPDATE scoring_results
        SET walk_forward_score = ?,
            walk_forward_pass  = ?
        WHERE scoring_id = ?
    """, (r.walk_forward_score, 1 if r.walk_forward_pass else 0, r.scoring_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def _safe_retention(oos_val: Optional[float], is_val: Optional[float]) -> float:
    """OOS/IS clamped to [0, 1]. Returns 0 when IS is zero or None."""
    if not is_val or is_val <= 0:
        return 0.0
    if not oos_val:
        return 0.0
    return min(oos_val / is_val, 1.0)


def _dd_component(is_dd: Optional[float], oos_dd: Optional[float]) -> float:
    """1.0 if OOS drawdown <= IS drawdown, else clamp(IS/OOS, 0, 1)."""
    if not is_dd or not oos_dd:
        return 1.0           # no data -- neutral
    if oos_dd <= is_dd:
        return 1.0           # OOS better or equal -- no penalty
    return min(is_dd / oos_dd, 1.0)


def _compute_score(is_bt: Dict, oos_bt: Dict) -> Tuple[float, float, float, float, bool]:
    """
    Returns (pf_retention, exp_retention, dd_comp, walk_forward_score, wf_pass).
    """
    pf_ret  = _safe_retention(oos_bt["profit_factor"],
                               is_bt["profit_factor"])
    exp_ret = _safe_retention(oos_bt["expectancy_per_trade"],
                               is_bt["expectancy_per_trade"])
    dd_comp = _dd_component(is_bt["max_drawdown_pct"],
                             oos_bt["max_drawdown_pct"])

    score = _PF_WEIGHT * pf_ret + _EXP_WEIGHT * exp_ret + _DD_WEIGHT * dd_comp
    return pf_ret, exp_ret, dd_comp, round(score, 4), score >= _PASS_GATE


def run_wf_for_spec(
    conn:    sqlite3.Connection,
    spec_id: int,
    dry_run: bool = False,
) -> Tuple[Optional[WFResult], Optional[str]]:
    """
    Run walk-forward validation for one spec.
    Returns (WFResult, None) on success.
    Returns (None, reason_str) when the spec must be skipped.
    Returns (None, "NOT_RUN") when no OOS backtest exists.
    """
    spec = _fetch_spec(conn, spec_id)
    if spec is None:
        return None, f"spec_id={spec_id} not found"

    oos_bt = _fetch_latest_bt(conn, spec_id, is_in_sample=0)
    if oos_bt is None:
        return None, "NOT_RUN"

    is_bt = _fetch_latest_bt(conn, spec_id, is_in_sample=1)
    if is_bt is None:
        return None, "OOS backtest found but no IS backtest -- import IS backtest first"

    scoring = _fetch_latest_scoring(conn, spec_id)
    if scoring is None:
        return None, "No scoring result -- run score_from_backtests.py first"

    pf_ret, exp_ret, dd_comp, score, wf_pass = _compute_score(is_bt, oos_bt)

    result = WFResult(
        spec_id              = spec_id,
        spec_name            = spec["spec_name"],
        is_backtest_id       = is_bt["backtest_id"],
        oos_backtest_id      = oos_bt["backtest_id"],
        scoring_id           = scoring["scoring_id"],
        is_pf                = is_bt["profit_factor"]           or 0.0,
        is_exp_per_trade     = is_bt["expectancy_per_trade"]    or 0.0,
        is_dd_pct            = is_bt["max_drawdown_pct"]        or 0.0,
        is_trades            = is_bt["total_trades"]            or 0,
        is_start             = is_bt["data_start_date"],
        is_end               = is_bt["data_end_date"],
        oos_pf               = oos_bt["profit_factor"]          or 0.0,
        oos_exp_per_trade    = oos_bt["expectancy_per_trade"]   or 0.0,
        oos_dd_pct           = oos_bt["max_drawdown_pct"]       or 0.0,
        oos_trades           = oos_bt["total_trades"]           or 0,
        oos_start            = oos_bt["data_start_date"],
        oos_end              = oos_bt["data_end_date"],
        pf_retention         = round(pf_ret,  4),
        expectancy_retention = round(exp_ret, 4),
        dd_component         = round(dd_comp, 4),
        walk_forward_score   = score,
        walk_forward_pass    = wf_pass,
        ran_at               = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    )

    if not dry_run:
        _write_result(conn, result)

    return result, None


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _wf_tier(score: float, wf_pass: bool) -> Tuple[str, str]:
    if wf_pass:
        return "PASS", "+"
    if score >= _WARN_GATE:
        return "WARNING", "!"
    return "FAIL", "X"


def _pct(v: float) -> str:
    return f"{v:.2%}"


def _to_markdown(r: WFResult, dry_run: bool = False) -> str:
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    tier, _ = _wf_tier(r.walk_forward_score, r.walk_forward_pass)

    p(f"# Walk-Forward Report: {r.spec_name}")
    p(f"**Run date:** {r.ran_at[:10]}")
    p(f"**spec_id:** {r.spec_id}  |  "
      f"**IS backtest_id:** {r.is_backtest_id}  |  "
      f"**OOS backtest_id:** {r.oos_backtest_id}  |  "
      f"**scoring_id:** {r.scoring_id}")
    if dry_run:
        p()
        p("> **DRY-RUN** -- results not written to database")
    p()
    p("---")
    p()
    p(f"## Result: {tier}")
    p()
    p("## IS vs OOS Comparison")
    p()
    p(f"| Metric | In-Sample | Out-of-Sample | Retention / Component |")
    p(f"|--------|-----------|---------------|-----------------------|")
    p(f"| Date range | {r.is_start} → {r.is_end} | "
      f"{r.oos_start} → {r.oos_end} | — |")
    p(f"| Trade count | {r.is_trades} | {r.oos_trades} | — |")
    p(f"| Profit factor | {r.is_pf:.2f} | {r.oos_pf:.2f} | "
      f"{_pct(r.pf_retention)} retention |")
    p(f"| Expectancy/trade | {r.is_exp_per_trade:.2f} | {r.oos_exp_per_trade:.2f} | "
      f"{_pct(r.expectancy_retention)} retention |")
    p(f"| Max drawdown | {_pct(r.is_dd_pct)} | {_pct(r.oos_dd_pct)} | "
      f"DD component: {_pct(r.dd_component)} |")
    p()
    p("## Score Breakdown")
    p()
    p(f"| Component | Value | Weight | Contribution |")
    p(f"|-----------|-------|--------|-------------|")
    p(f"| PF retention | {_pct(r.pf_retention)} | {_PF_WEIGHT:.0%} | "
      f"{r.pf_retention * _PF_WEIGHT:.4f} |")
    p(f"| Expectancy retention | {_pct(r.expectancy_retention)} | {_EXP_WEIGHT:.0%} | "
      f"{r.expectancy_retention * _EXP_WEIGHT:.4f} |")
    p(f"| Drawdown component | {_pct(r.dd_component)} | {_DD_WEIGHT:.0%} | "
      f"{r.dd_component * _DD_WEIGHT:.4f} |")
    p(f"| **walk_forward_score** | **{r.walk_forward_score:.4f}** | 100% | — |")
    p()
    p("## Thresholds")
    p()
    p(f"| Tier | Threshold |")
    p(f"|------|-----------|")
    p(f"| PASS | >= {_PASS_GATE:.0%} |")
    p(f"| WARNING | {_WARN_GATE:.0%} – {_PASS_GATE:.0%} |")
    p(f"| FAIL | < {_WARN_GATE:.0%} |")
    p()
    p("---")
    p()
    note = "Results NOT written to DB (dry-run). " if dry_run else \
           "walk_forward_score and walk_forward_pass updated in scoring_results. "
    p(f"*Read-only validation. {note}"
      "Human approval required before any strategy advances beyond REVIEW_REQUIRED.*")

    return "\n".join(lines)


def _to_dict(r: WFResult, dry_run: bool = False) -> Dict:
    return {
        "spec_id":              r.spec_id,
        "spec_name":            r.spec_name,
        "is_backtest_id":       r.is_backtest_id,
        "oos_backtest_id":      r.oos_backtest_id,
        "scoring_id":           r.scoring_id,
        "dry_run":              dry_run,
        "is": {
            "backtest_id":      r.is_backtest_id,
            "profit_factor":    r.is_pf,
            "exp_per_trade":    r.is_exp_per_trade,
            "max_drawdown_pct": r.is_dd_pct,
            "total_trades":     r.is_trades,
            "date_range":       f"{r.is_start} → {r.is_end}",
        },
        "oos": {
            "backtest_id":      r.oos_backtest_id,
            "profit_factor":    r.oos_pf,
            "exp_per_trade":    r.oos_exp_per_trade,
            "max_drawdown_pct": r.oos_dd_pct,
            "total_trades":     r.oos_trades,
            "date_range":       f"{r.oos_start} → {r.oos_end}",
        },
        "components": {
            "pf_retention":         r.pf_retention,
            "expectancy_retention": r.expectancy_retention,
            "dd_component":         r.dd_component,
            "weights":              {"pf": _PF_WEIGHT, "exp": _EXP_WEIGHT, "dd": _DD_WEIGHT},
        },
        "walk_forward_score": r.walk_forward_score,
        "walk_forward_pass":  r.walk_forward_pass,
        "pass_gate":          _PASS_GATE,
        "warn_gate":          _WARN_GATE,
        "ran_at":             r.ran_at,
    }


def write_reports(
    r: WFResult, reports_dir: Path, dry_run: bool = False
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = r.ran_at[:10].replace("-", "")
    safe_name = re.sub(r"[^\w\-]", "_", r.spec_name)

    md_path   = reports_dir / f"{safe_name}_walk_forward_{date_str}.md"
    json_path = reports_dir / f"{safe_name}_walk_forward_{date_str}.json"

    md_path.write_text(_to_markdown(r, dry_run=dry_run), encoding="utf-8")
    json_path.write_text(
        json.dumps(_to_dict(r, dry_run=dry_run), indent=2), encoding="utf-8"
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_result(r: WFResult, dry_run: bool = False) -> None:
    tier, icon = _wf_tier(r.walk_forward_score, r.walk_forward_pass)
    tag = "  [DRY-RUN]" if dry_run else ""
    print(f"WF: {r.spec_name}  [spec_id={r.spec_id}]{tag}")
    print(f"  IS  bt_id={r.is_backtest_id}  "
          f"{r.is_start} -> {r.is_end}  trades={r.is_trades}")
    print(f"  OOS bt_id={r.oos_backtest_id}  "
          f"{r.oos_start} -> {r.oos_end}  trades={r.oos_trades}")
    print()
    print(f"  {'Metric':<22}  {'IS':>8}  {'OOS':>8}  {'Retention':>10}")
    print(f"  {'-'*22}  {'-'*8}  {'-'*8}  {'-'*10}")
    print(f"  {'Profit factor':<22}  {r.is_pf:>8.2f}  {r.oos_pf:>8.2f}  "
          f"{_pct(r.pf_retention):>10}")
    print(f"  {'Expectancy/trade':<22}  {r.is_exp_per_trade:>8.2f}  "
          f"{r.oos_exp_per_trade:>8.2f}  {_pct(r.expectancy_retention):>10}")
    print(f"  {'Max drawdown':<22}  {_pct(r.is_dd_pct):>8}  "
          f"{_pct(r.oos_dd_pct):>8}  {'DD comp: ' + _pct(r.dd_component):>10}")
    print()
    print(f"  Score: {r.pf_retention:.4f}x{_PF_WEIGHT} + "
          f"{r.expectancy_retention:.4f}x{_EXP_WEIGHT} + "
          f"{r.dd_component:.4f}x{_DD_WEIGHT} = {r.walk_forward_score:.4f}")
    print(f"  [{icon}] Walk-Forward {tier}  "
          f"score={r.walk_forward_score:.4f}  "
          f"(PASS>={_PASS_GATE}  WARN>={_WARN_GATE}  FAIL<{_WARN_GATE})")
    print()
    if not dry_run:
        print(f"  scoring_results.scoring_id={r.scoring_id} updated")
        print(f"    walk_forward_score = {r.walk_forward_score}")
        print(f"    walk_forward_pass  = {r.walk_forward_pass}")
    print()


def _print_skip(spec_id: int, reason: str) -> None:
    if reason == "NOT_RUN":
        print(f"  NOT_RUN spec_id={spec_id}: no OOS backtest -- "
              "import an OOS period with --oos to enable walk-forward")
    else:
        print(f"  SKIP spec_id={spec_id}: {reason}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Walk-Forward Validation Engine -- IS vs OOS comparison. "
            "No live trading. No schema changes. Human approval required."
        ),
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec-id", type=int, metavar="ID",
                     help="Run walk-forward for one spec")
    grp.add_argument("--all", action="store_true",
                     help="Run walk-forward for all specs")
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
    print(f"Hermes Walk-Forward Validation Engine  [{mode}]")
    print(f"  DB          : {db_path}")
    print(f"  Score formula: "
          f"PF x {_PF_WEIGHT} + expectancy x {_EXP_WEIGHT} + DD x {_DD_WEIGHT}")
    print(f"  Tiers       : PASS>={_PASS_GATE}  WARN>={_WARN_GATE}  FAIL<{_WARN_GATE}")
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

        results:  List[WFResult] = []
        not_run:  List[int]      = []
        skipped:  List[Tuple[int, str]] = []

        for sid in spec_ids:
            result, reason = run_wf_for_spec(
                conn    = conn,
                spec_id = sid,
                dry_run = args.dry_run,
            )

            if result is None:
                if reason == "NOT_RUN":
                    not_run.append(sid)
                    _print_skip(sid, reason)
                else:
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
            if not result.walk_forward_pass:
                exit_code = 1

        # Summary for --all
        if args.all and (results or not_run or skipped):
            print("-" * 68)
            print("WALK-FORWARD SUMMARY")
            print("-" * 68)
            print()
            if results:
                w = max(len(r.spec_name) for r in results)
                print(f"  {'Strategy':<{w}}  Score   PF ret  Exp ret  DD comp  Verdict")
                print(f"  {'-'*w}  ------  ------  -------  -------  -------")
                for r in results:
                    tier, icon = _wf_tier(r.walk_forward_score, r.walk_forward_pass)
                    print(f"  {r.spec_name:<{w}}  "
                          f"{r.walk_forward_score:.4f}  "
                          f"{r.pf_retention:.2%}  "
                          f"{r.expectancy_retention:.2%}   "
                          f"{r.dd_component:.2%}   "
                          f"[{icon}] {tier}")
                print()
            if not_run:
                print(f"  NOT_RUN ({len(not_run)}) -- no OOS backtest: "
                      + ", ".join(f"spec_id={s}" for s in not_run))
                print()
            if skipped:
                print(f"  SKIP ({len(skipped)}):")
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
