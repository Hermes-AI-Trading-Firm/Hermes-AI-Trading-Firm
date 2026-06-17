#!/usr/bin/env python3
"""
NT8 Export Certifier -- research/certification/nt8_export_certifier.py

Phase 36

Goal: Certify that Hermes correctly ingests real NinjaTrader Strategy Analyzer exports.

Scope
-----
  Validation only. Local files only. Local DB only.
  No live trading. No broker connection. No order placement. No ATM control.
  No automatic approval. REVIEW_REQUIRED remains the terminal pipeline state.

Checks
------
  1.  Probe summary file            -- file readable, columns detected
  2.  Probe trade list file         -- file readable, columns detected
  3.  Confirm required columns      -- all REQUIRED_*_COLS present in each file
  4.  Trade count matches CSV       -- valid_rows == CSV data row count
  5.  trade_list_json count matches -- DB json_array_length matches probe count
  6.  equity_curve_json length      -- DB json_array_length matches probe count
  7.  Backtest row created/deduped  -- backtest row exists for spec_id
  8.  Score can be generated        -- run_for_spec completes without error
  9.  Audit can run                 -- audit_spec completes without error
  10. REVIEW_REQUIRED unchanged     -- no state moved beyond REVIEW_REQUIRED

Checks 1-4 run in dry-run mode (no DB required).
Checks 5-10 require a DB connection and an actual import.

Usage
-----
    # Dry-run: probe sample files, no DB writes
    python -m research.certification.nt8_export_certifier --dry-run

    # Dry-run with explicit files
    python -m research.certification.nt8_export_certifier --dry-run \\
        --summary path/to/summary.csv \\
        --trade-list path/to/trades.csv

    # Full certification with real NT8 export
    python -m research.certification.nt8_export_certifier \\
        --summary path/to/summary.csv \\
        --trade-list path/to/trades.csv \\
        --spec-id 3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB   = _PROJECT_ROOT / "database" / "hermes_research.db"
REPORTS_DIR  = _PROJECT_ROOT / "reports"  / "certification"
SAMPLE_SUMMARY    = (_PROJECT_ROOT / "connectors" / "ninjatrader"
                     / "sample_nt8_backtest_summary.csv")
SAMPLE_TRADE_LIST = (_PROJECT_ROOT / "connectors" / "ninjatrader"
                     / "sample_nt8_trade_list.csv")

MAX_AUTOMATED_STATE = "REVIEW_REQUIRED"

_HUMAN_STATES = {"HUMAN_APPROVED", "HUMAN_REJECTED", "ARCHIVED"}

_STATUS_ORDER = {
    "PASS": 0, "WARN": 1, "FAIL": 2, "SKIP": 3,
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CertCheck:
    number:     int
    name:       str
    status:     str         # PASS / WARN / FAIL / SKIP
    detail:     str
    data:       Dict = field(default_factory=dict)


@dataclass
class CertReport:
    spec_id:          Optional[int]
    spec_name:        Optional[str]
    summary_path:     Optional[str]
    trade_list_path:  Optional[str]
    mode:             str           # DRY-RUN / FULL
    checks:           List[CertCheck]
    overall:          str           # PASS / WARN / FAIL
    generated_at:     str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    )


# ---------------------------------------------------------------------------
# Check builders
# ---------------------------------------------------------------------------

def _pass(n: int, name: str, detail: str, data: Dict = None) -> CertCheck:
    return CertCheck(n, name, "PASS", detail, data or {})

def _warn(n: int, name: str, detail: str, data: Dict = None) -> CertCheck:
    return CertCheck(n, name, "WARN", detail, data or {})

def _fail(n: int, name: str, detail: str, data: Dict = None) -> CertCheck:
    return CertCheck(n, name, "FAIL", detail, data or {})

def _skip(n: int, name: str, detail: str) -> CertCheck:
    return CertCheck(n, name, "SKIP", detail, {})


def _overall(checks: List[CertCheck]) -> str:
    statuses = {c.status for c in checks if c.status != "SKIP"}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARN" in statuses:
        return "WARN"
    return "PASS"


# ---------------------------------------------------------------------------
# Checks 1-4: file probes (no DB, dry-run safe)
# ---------------------------------------------------------------------------

def check_probe_summary(path: Optional[Path]) -> CertCheck:
    from connectors.ninjatrader.backtest_ingestor import probe_summary
    if path is None:
        return _skip(1, "Probe summary file", "No summary file provided")
    r = probe_summary(path)
    verdict = r.get("verdict", "UNKNOWN")
    detail = (
        f"rows={r['row_count']}  columns={len(r['columns_found'])}"
        f"  verdict={verdict}"
    )
    if r.get("is_sample"):
        detail += "  [SAMPLE FILE -- replace with real NT8 export]"
    if verdict == "READY":
        return _pass(1, "Probe summary file", detail, r)
    if verdict.startswith("WARN"):
        return _warn(1, "Probe summary file", detail, r)
    return _fail(1, "Probe summary file", detail, r)


def check_probe_trade_list(path: Optional[Path],
                           initial_capital: Optional[float]) -> CertCheck:
    from connectors.ninjatrader.backtest_ingestor import probe_trade_list
    if path is None:
        return _skip(2, "Probe trade list file", "No trade list file provided")
    r = probe_trade_list(path, initial_capital)
    verdict = r.get("verdict", "UNKNOWN")
    detail = (
        f"rows={r['row_count']}  valid={r.get('valid_rows', 0)}"
        f"  skipped={r.get('skipped_rows', 0)}  verdict={verdict}"
    )
    if r.get("is_sample"):
        detail += "  [SAMPLE FILE -- replace with real NT8 export]"
    if verdict == "READY":
        return _pass(2, "Probe trade list file", detail, r)
    if verdict.startswith("WARN"):
        return _warn(2, "Probe trade list file", detail, r)
    return _fail(2, "Probe trade list file", detail, r)


def check_required_columns(summary_probe: Optional[Dict],
                            trade_list_probe: Optional[Dict]) -> CertCheck:
    missing_s = sorted((summary_probe    or {}).get("required_missing", []))
    missing_t = sorted((trade_list_probe or {}).get("required_missing", []))
    all_missing = missing_s + missing_t

    if summary_probe is None and trade_list_probe is None:
        return _skip(3, "Confirm required columns mapped", "No files provided")

    if all_missing:
        detail = f"Missing: {all_missing}"
        return _fail(3, "Confirm required columns mapped", detail,
                     {"missing_summary": missing_s, "missing_trade_list": missing_t})

    counts = []
    if summary_probe:
        counts.append(
            f"summary={len(summary_probe.get('required_present', []))} required cols OK"
        )
    if trade_list_probe:
        counts.append(
            f"trade_list={len(trade_list_probe.get('required_present', []))} required cols OK"
        )
    return _pass(3, "Confirm required columns mapped", "  ".join(counts))


def check_trade_count(trade_list_probe: Optional[Dict]) -> CertCheck:
    if trade_list_probe is None:
        return _skip(4, "Trade count matches CSV row count", "No trade list file provided")

    row_count   = trade_list_probe.get("row_count", 0)
    valid_rows  = trade_list_probe.get("valid_rows", 0)
    skipped     = trade_list_probe.get("skipped_rows", 0)
    tl_count    = trade_list_probe.get("trade_list_json_count", 0)

    detail = (
        f"CSV rows={row_count}  valid={valid_rows}"
        f"  skipped={skipped}  trade_list_json_count={tl_count}"
    )
    data = {"row_count": row_count, "valid_rows": valid_rows,
            "skipped": skipped, "trade_list_json_count": tl_count}

    if tl_count != valid_rows:
        return _fail(4, "Trade count matches CSV row count",
                     detail + "  [MISMATCH: trade_list_json_count != valid_rows]", data)
    if skipped > 0:
        return _warn(4, "Trade count matches CSV row count",
                     detail + f"  [{skipped} rows skipped -- check parse warnings]", data)
    return _pass(4, "Trade count matches CSV row count", detail, data)


# ---------------------------------------------------------------------------
# Checks 5-7: DB integrity (requires import)
# ---------------------------------------------------------------------------

def check_trade_list_json(conn: sqlite3.Connection,
                          backtest_id: int,
                          expected: int) -> CertCheck:
    row = conn.execute(
        "SELECT json_array_length(trade_list_json) FROM backtests WHERE backtest_id=?",
        (backtest_id,)
    ).fetchone()
    actual = row[0] if (row and row[0] is not None) else 0
    detail = f"backtest_id={backtest_id}  expected={expected}  actual={actual}"
    if actual == 0:
        return _fail(5, "trade_list_json count matches", detail + "  [EMPTY]",
                     {"backtest_id": backtest_id, "expected": expected, "actual": actual})
    if actual != expected:
        return _warn(5, "trade_list_json count matches",
                     detail + f"  [DELTA={actual-expected}]",
                     {"backtest_id": backtest_id, "expected": expected, "actual": actual})
    return _pass(5, "trade_list_json count matches", detail,
                 {"backtest_id": backtest_id, "expected": expected, "actual": actual})


def check_equity_curve_json(conn: sqlite3.Connection,
                             backtest_id: int,
                             expected: int) -> CertCheck:
    row = conn.execute(
        "SELECT json_array_length(equity_curve_json) FROM backtests WHERE backtest_id=?",
        (backtest_id,)
    ).fetchone()
    actual = row[0] if (row and row[0] is not None) else 0
    detail = f"backtest_id={backtest_id}  expected={expected}  actual={actual}"
    if actual == 0 and expected > 0:
        return _warn(6, "equity_curve_json length matches",
                     detail + "  [EMPTY -- initial_capital may be required]",
                     {"backtest_id": backtest_id, "expected": expected, "actual": actual})
    if actual != expected and expected > 0:
        return _warn(6, "equity_curve_json length matches",
                     detail + f"  [DELTA={actual-expected}]",
                     {"backtest_id": backtest_id, "expected": expected, "actual": actual})
    return _pass(6, "equity_curve_json length matches", detail,
                 {"backtest_id": backtest_id, "expected": expected, "actual": actual})


def check_backtest_row(conn: sqlite3.Connection,
                       spec_id: int,
                       backtest_id: Optional[int],
                       was_deduped: bool) -> CertCheck:
    row = conn.execute(
        "SELECT backtest_id, backtest_name, data_start_date, data_end_date "
        "FROM backtests WHERE spec_id=? ORDER BY backtest_id DESC LIMIT 1",
        (spec_id,)
    ).fetchone()
    if row is None:
        return _fail(7, "Backtest row created or deduped",
                     f"spec_id={spec_id}: no backtest row found in DB")
    detail = (
        f"backtest_id={row[0]}  {row[2]} to {row[3]}"
        + ("  [DEDUPED -- existing row reused]" if was_deduped else "  [NEW ROW]")
    )
    if was_deduped:
        return _warn(7, "Backtest row created or deduped", detail,
                     {"backtest_id": row[0], "deduped": True})
    return _pass(7, "Backtest row created or deduped", detail,
                 {"backtest_id": row[0], "deduped": False})


# ---------------------------------------------------------------------------
# Check 8: scoring
# ---------------------------------------------------------------------------

def check_score(conn: sqlite3.Connection, spec_id: int) -> Tuple[CertCheck, Optional[object]]:
    from research.scoring.runner import run_for_spec
    try:
        result = run_for_spec(conn, spec_id, save=True)
        sr = result.scoring_result
        detail = (
            f"score={sr.composite_score}  grade={sr.grade}"
            f"  recommendation={sr.recommendation}"
        )
        return _pass(8, "Score can be generated", detail,
                     {"score": sr.composite_score, "grade": sr.grade,
                      "recommendation": sr.recommendation}), result
    except Exception as exc:
        return _fail(8, "Score can be generated", f"ERROR: {exc}"), None


# ---------------------------------------------------------------------------
# Check 9: audit
# ---------------------------------------------------------------------------

def check_audit(conn: sqlite3.Connection, spec_id: int) -> CertCheck:
    from research.audit.strategy_auditor import audit_spec
    try:
        report = audit_spec(conn, spec_id)
        if report is None:
            return _fail(9, "Audit can run",
                         f"spec_id={spec_id}: audit returned None (spec not found?)")
        fail_count = sum(1 for c in report.checks if c.status == "FAIL")
        warn_count = sum(1 for c in report.checks if c.status == "WARN")
        detail = (
            f"recommendation={report.recommendation}"
            f"  checks={len(report.checks)}"
            f"  FAIL={fail_count}  WARN={warn_count}"
        )
        data = {"recommendation": report.recommendation,
                "fail_count": fail_count, "warn_count": warn_count}
        if fail_count > 0:
            return _warn(9, "Audit can run",
                         detail + "  [audit ran; FAIL findings need resolution]", data)
        if warn_count > 0:
            return _warn(9, "Audit can run", detail + "  [audit ran; WARN findings present]", data)
        return _pass(9, "Audit can run", detail, data)
    except Exception as exc:
        return _fail(9, "Audit can run", f"ERROR: {exc}")


# ---------------------------------------------------------------------------
# Check 10: REVIEW_REQUIRED unchanged
# ---------------------------------------------------------------------------

def check_review_required(conn: sqlite3.Connection,
                           spec_id: int,
                           state_before: Optional[str]) -> CertCheck:
    from research.lifecycle.lifecycle import infer_lifecycle_state
    rec = infer_lifecycle_state(conn, spec_id)
    state_after = rec.state if rec else "UNKNOWN"

    detail = f"before={state_before or 'UNKNOWN'}  after={state_after}"
    data = {"state_before": state_before, "state_after": state_after}

    if state_after in _HUMAN_STATES and state_before not in _HUMAN_STATES:
        return _fail(10, "REVIEW_REQUIRED remains terminal",
                     detail + "  [VIOLATION: state advanced beyond REVIEW_REQUIRED]", data)

    if state_after in _HUMAN_STATES and state_before in _HUMAN_STATES:
        return _pass(10, "REVIEW_REQUIRED remains terminal",
                     detail + "  [state was already in human territory before certification]",
                     data)

    return _pass(10, "REVIEW_REQUIRED remains terminal",
                 detail + f"  [terminal state: {MAX_AUTOMATED_STATE}]", data)


# ---------------------------------------------------------------------------
# Main certification runner
# ---------------------------------------------------------------------------

def run_certification(
    summary_path:    Optional[Path],
    trade_list_path: Optional[Path],
    spec_id:         Optional[int],
    conn:            Optional[sqlite3.Connection],
    initial_capital: Optional[float] = None,
    dry_run:         bool = False,
) -> CertReport:
    """
    Run all certification checks. Read-only for checks 1-4.
    Checks 5-10 require conn and perform import/score/audit.
    """
    checks: List[CertCheck] = []

    # Resolve spec name for report header
    spec_name: Optional[str] = None
    if conn and spec_id:
        row = conn.execute(
            "SELECT spec_name FROM strategy_specs WHERE spec_id=?", (spec_id,)
        ).fetchone()
        if row:
            spec_name = row[0]

    # --- Checks 1-4: file probes ---
    summary_probe    = None
    trade_list_probe = None

    c1 = check_probe_summary(summary_path)
    checks.append(c1)
    if c1.status != "SKIP":
        summary_probe = c1.data

    c2 = check_probe_trade_list(trade_list_path, initial_capital)
    checks.append(c2)
    if c2.status != "SKIP":
        trade_list_probe = c2.data

    checks.append(check_required_columns(summary_probe, trade_list_probe))
    checks.append(check_trade_count(trade_list_probe))

    mode = "DRY-RUN" if dry_run else "FULL"

    if dry_run or conn is None or spec_id is None:
        for n, name in [
            (5,  "trade_list_json count matches"),
            (6,  "equity_curve_json length matches"),
            (7,  "Backtest row created or deduped"),
            (8,  "Score can be generated"),
            (9,  "Audit can run"),
            (10, "REVIEW_REQUIRED remains terminal"),
        ]:
            checks.append(_skip(n, name,
                                "Skipped in dry-run mode (no DB writes)"))
        return CertReport(
            spec_id         = spec_id,
            spec_name       = spec_name,
            summary_path    = str(summary_path) if summary_path else None,
            trade_list_path = str(trade_list_path) if trade_list_path else None,
            mode            = mode,
            checks          = checks,
            overall         = _overall(checks),
        )

    # --- Checks 5-10: full certification with DB ---
    from connectors.ninjatrader.backtest_ingestor import (
        _ensure_dedup_index,
        import_backtest_summary,
        import_trade_list,
    )

    # Capture lifecycle state before import
    try:
        from research.lifecycle.lifecycle import infer_lifecycle_state
        rec_before = infer_lifecycle_state(conn, spec_id)
        state_before = rec_before.state if rec_before else None
    except Exception:
        state_before = None

    # Run the actual import
    _ensure_dedup_index(conn)
    backtest_id: Optional[int] = None
    was_deduped = False

    if summary_path:
        bid, errs = import_backtest_summary(
            conn, summary_path, spec_id,
            initial_capital=initial_capital,
            is_in_sample=True,
            dry_run=False,
        )
        if bid:
            backtest_id = bid
        else:
            was_deduped = any("Duplicate" in e for e in errs)
            if was_deduped:
                row = conn.execute(
                    "SELECT MAX(backtest_id) FROM backtests WHERE spec_id=?",
                    (spec_id,)
                ).fetchone()
                if row and row[0]:
                    backtest_id = row[0]

    if trade_list_path and backtest_id is not None:
        import_trade_list(
            conn, trade_list_path, spec_id,
            backtest_id=backtest_id,
            initial_capital=initial_capital,
            is_in_sample=True,
            dry_run=False,
        )
    elif trade_list_path and backtest_id is None:
        ins, _, _ = import_trade_list(
            conn, trade_list_path, spec_id,
            initial_capital=initial_capital,
            is_in_sample=True,
            dry_run=False,
        )
        row = conn.execute(
            "SELECT MAX(backtest_id) FROM backtests WHERE spec_id=?",
            (spec_id,)
        ).fetchone()
        if row and row[0]:
            backtest_id = row[0]

    # Expected counts from probe
    expected_tl_count = (trade_list_probe or {}).get("trade_list_json_count", 0)
    expected_eq_count = (trade_list_probe or {}).get("equity_curve_json_count", 0)

    if backtest_id:
        checks.append(check_trade_list_json(conn, backtest_id, expected_tl_count))
        checks.append(check_equity_curve_json(conn, backtest_id, expected_eq_count))
    else:
        checks.append(_fail(5, "trade_list_json count matches",
                            "No backtest_id -- import did not produce a backtest row"))
        checks.append(_fail(6, "equity_curve_json length matches",
                            "No backtest_id -- import did not produce a backtest row"))

    checks.append(check_backtest_row(conn, spec_id, backtest_id, was_deduped))
    c8, _ = check_score(conn, spec_id)
    checks.append(c8)
    checks.append(check_audit(conn, spec_id))
    checks.append(check_review_required(conn, spec_id, state_before))

    return CertReport(
        spec_id         = spec_id,
        spec_name       = spec_name,
        summary_path    = str(summary_path) if summary_path else None,
        trade_list_path = str(trade_list_path) if trade_list_path else None,
        mode            = mode,
        checks          = checks,
        overall         = _overall(checks),
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

_STATUS_ICON = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL", "SKIP": "SKIP"}
_STATUS_BADGE = {"PASS": "[+]", "WARN": "[!]", "FAIL": "[X]", "SKIP": "[-]"}


def generate_report_md(report: CertReport) -> str:
    lines: List[str] = []
    now = report.generated_at

    lines.append("# NT8 Export Certification Report")
    lines.append(f"Generated: {now}")
    lines.append(f"Mode: {report.mode}")
    lines.append("")
    if report.spec_id:
        lines.append(f"Strategy: {report.spec_name or 'unknown'}  (spec_id={report.spec_id})")
    if report.summary_path:
        lines.append(f"Summary:  {report.summary_path}")
    if report.trade_list_path:
        lines.append(f"Trades:   {report.trade_list_path}")
    lines.append("")
    lines.append(f"Overall: **{report.overall}**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append(f"| # | Check | Status | Detail |")
    lines.append(f"|---|-------|--------|--------|")
    for c in report.checks:
        badge = _STATUS_BADGE.get(c.status, c.status)
        lines.append(f"| {c.number} | {c.name} | {badge} {c.status} | {c.detail} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    fails  = [c for c in report.checks if c.status == "FAIL"]
    warns  = [c for c in report.checks if c.status == "WARN"]
    passes = [c for c in report.checks if c.status == "PASS"]

    if fails:
        lines.append("## Failures")
        for c in fails:
            lines.append(f"- **Check {c.number} -- {c.name}**: {c.detail}")
        lines.append("")

    if warns:
        lines.append("## Warnings")
        for c in warns:
            lines.append(f"- **Check {c.number} -- {c.name}**: {c.detail}")
        lines.append("")

    lines.append(f"## Result")
    if report.overall == "PASS":
        lines.append("Certification PASSED.")
        lines.append("Hermes correctly ingested this NT8 export.")
    elif report.overall == "WARN":
        lines.append("Certification PASSED WITH WARNINGS.")
        lines.append("Review warnings above before treating this data as production-ready.")
    else:
        lines.append("Certification FAILED.")
        lines.append("Resolve failures above before using this export in the research pipeline.")
    lines.append("")
    lines.append(f"Checks passed: {len(passes)} / {len([c for c in report.checks if c.status != 'SKIP'])}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Validation only. No live trading. No broker connection.*")
    lines.append(f"*Terminal automated state: {MAX_AUTOMATED_STATE}*")
    lines.append("*Human approval required before any strategy advances beyond research.*")
    return "\n".join(lines)


def write_reports(report: CertReport, reports_dir: Path = REPORTS_DIR) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_tag  = datetime.now().strftime("%Y%m%d")
    safe_name = (report.spec_name or "unknown").replace(" ", "_")
    md_path   = reports_dir / f"nt8_export_certification_{safe_name}_{date_tag}.md"
    json_path = reports_dir / f"nt8_export_certification_{safe_name}_{date_tag}.json"

    md_path.write_text(generate_report_md(report), encoding="utf-8")
    json_path.write_text(json.dumps({
        "generated_at":      report.generated_at,
        "mode":              report.mode,
        "overall":           report.overall,
        "spec_id":           report.spec_id,
        "spec_name":         report.spec_name,
        "summary_path":      report.summary_path,
        "trade_list_path":   report.trade_list_path,
        "max_automated_state": MAX_AUTOMATED_STATE,
        "checks": [
            {"number": c.number, "name": c.name,
             "status": c.status, "detail": c.detail, "data": c.data}
            for c in report.checks
        ],
    }, indent=2), encoding="utf-8")
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console printer
# ---------------------------------------------------------------------------

def print_report(report: CertReport) -> None:
    print(f"\nNT8 Export Certification -- Phase 36")
    print(f"Mode: {report.mode}")
    if report.spec_name:
        print(f"Strategy: {report.spec_name}  (spec_id={report.spec_id})")
    print()
    print(f"  {'#':<3} {'Check':<38} {'Status'}")
    print(f"  {'-'*3} {'-'*38} {'-'*6}")
    for c in report.checks:
        badge = _STATUS_BADGE.get(c.status, c.status)
        print(f"  {c.number:<3} {c.name:<38} {badge} {c.status}")
        if c.status in ("FAIL", "WARN"):
            print(f"      {c.detail}")
        elif c.status == "PASS" and c.detail:
            print(f"      {c.detail}")
    print()
    print(f"  Overall: {report.overall}")
    print()
    print(f"  Terminal state: {MAX_AUTOMATED_STATE}")
    print(f"  Human approval required before any strategy advances beyond research.")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "NT8 Export Certifier (Phase 36) -- "
            "Certify that Hermes correctly ingests real NT8 exports. "
            "Validation only. No live trading."
        )
    )
    parser.add_argument("--summary",         metavar="PATH",
                        help="NT8 Performance Summary CSV")
    parser.add_argument("--trade-list",      metavar="PATH",
                        help="NT8 Trade List CSV")
    parser.add_argument("--spec-id",         type=int,
                        help="strategy_specs.spec_id")
    parser.add_argument("--initial-capital", type=float, default=None,
                        help="Starting account value for equity curve")
    parser.add_argument("--dry-run",         action="store_true",
                        help=(
                            "Run checks 1-4 only (file probes). "
                            "Uses sample files if no --summary/--trade-list provided. "
                            "No DB writes."
                        ))
    parser.add_argument("--db",              default=str(DEFAULT_DB), metavar="PATH")
    args = parser.parse_args()

    # Resolve file paths
    summary_path    = Path(args.summary)    if args.summary    else None
    trade_list_path = Path(args.trade_list) if args.trade_list else None

    # In dry-run with no files, use sample files to exercise checks 1-4
    if args.dry_run and summary_path is None and trade_list_path is None:
        summary_path    = SAMPLE_SUMMARY    if SAMPLE_SUMMARY.exists()    else None
        trade_list_path = SAMPLE_TRADE_LIST if SAMPLE_TRADE_LIST.exists() else None
        if summary_path or trade_list_path:
            print("(Using sample files for dry-run probe)")

    db_path = Path(args.db)
    conn: Optional[sqlite3.Connection] = None

    if not args.dry_run:
        if not args.spec_id:
            parser.error("--spec-id is required for full certification (use --dry-run for file-only checks)")
        if not db_path.exists():
            print(f"ERROR: database not found: {db_path}", file=sys.stderr)
            sys.exit(1)
        conn = sqlite3.connect(str(db_path))

    try:
        report = run_certification(
            summary_path    = summary_path,
            trade_list_path = trade_list_path,
            spec_id         = args.spec_id,
            conn            = conn,
            initial_capital = args.initial_capital,
            dry_run         = args.dry_run,
        )
    finally:
        if conn:
            conn.close()

    print_report(report)

    if not args.dry_run:
        md_path, json_path = write_reports(report)
        print(f"Reports written:")
        print(f"  {md_path}")
        print(f"  {json_path}")
    else:
        print("  [dry-run] No files written.")

    sys.exit(0 if report.overall in ("PASS", "WARN") else 1)


if __name__ == "__main__":
    main()
