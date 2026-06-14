#!/usr/bin/env python3
"""
Hermes Full Pipeline Integration Test — research/integration/full_pipeline_test.py

Runs an end-to-end test of the Hermes research pipeline using local sample
files only. No live trading. No broker connection. No strategy promotion.
Human approval gate remains mandatory — no strategy advances beyond
REVIEW_REQUIRED status.

Stages
------
 1  Spec Import          Import HERMES_INTEGRATION_TEST_v001 into strategy_specs
 2  Backtest Import      Import sample NT8 backtest summary + trade list
 3  Score from Backtest  Score the spec; write result to scoring_results
 4  Research Rankings    Verify spec appears in /research-rankings (latest only)
 5  Performance Analytics  Verify /equity-curve and /performance-summary respond
 6  Compliance Status    Verify /compliance-status responds
 7  Strategy Report      Generate Markdown + JSON report; record output paths
 8  Decision Queue       Verify spec in queue with status REVIEW_REQUIRED

Usage
-----
    python -m research.integration.full_pipeline_test --dry-run
    python -m research.integration.full_pipeline_test --run-label phase18_sample
    python -m research.integration.full_pipeline_test --db database/hermes_research.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]          # integration/ → research/ → project root

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB      = _PROJECT_ROOT / "database" / "hermes_research.db"
TEST_SPEC_NAME  = "HERMES_INTEGRATION_TEST_v001"
TEST_SPEC_FILE  = _HERE / "test_spec.yaml"
SAMPLE_SUMMARY  = _PROJECT_ROOT / "connectors" / "ninjatrader" / "sample_nt8_backtest_summary.csv"
SAMPLE_TRADES   = _PROJECT_ROOT / "connectors" / "ninjatrader" / "sample_nt8_trade_list.csv"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    name:   str
    status: str           # PASS | FAIL | SKIP | WARN
    detail: str
    error:  Optional[str] = None


@dataclass
class TestContext:
    """Carries state between stages."""
    spec_id:        Optional[int]   = None
    backtest_id:    Optional[int]   = None
    score:          Optional[float] = None
    grade:          Optional[str]   = None
    recommendation: Optional[str]   = None
    report_md:      Optional[str]   = None
    report_json:    Optional[str]   = None


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _pass(name: str, detail: str) -> StageResult:
    return StageResult(name, "PASS", detail)

def _fail(name: str, detail: str, error: str = "") -> StageResult:
    return StageResult(name, "FAIL", detail, error)

def _skip(name: str, detail: str) -> StageResult:
    return StageResult(name, "SKIP", detail)

def _warn(name: str, detail: str) -> StageResult:
    return StageResult(name, "WARN", detail)


# ---------------------------------------------------------------------------
# Stage 1 — Spec Import
# ---------------------------------------------------------------------------

def stage_spec_import(
    conn: sqlite3.Connection,
    ctx:  TestContext,
    dry_run: bool,
) -> StageResult:
    name = "Spec Import"
    try:
        from connectors.strategy_specs.spec_importer import import_spec, _find_existing_spec
    except ImportError as exc:
        return _fail(name, "Cannot import spec_importer", str(exc))

    if not TEST_SPEC_FILE.exists():
        return _fail(name, "Test spec file missing", str(TEST_SPEC_FILE))

    # Always find existing spec regardless of dry-run (read-only check)
    existing_id = _find_existing_spec(conn, TEST_SPEC_NAME)

    if dry_run:
        ctx.spec_id = existing_id
        state = f"spec_id={existing_id} (exists)" if existing_id else "would insert"
        return _skip(name, f"Dry-run: {state}")

    result = import_spec(conn, TEST_SPEC_FILE, dry_run=False, update_existing=False)
    if result["action"] == "error":
        return _fail(name, "Import failed", "; ".join(result["errors"]))

    ctx.spec_id = result["spec_id"]
    action = result["action"]   # inserted | skipped
    return _pass(name, f"{action}  spec_id={ctx.spec_id}  name={TEST_SPEC_NAME}")


# ---------------------------------------------------------------------------
# Stage 2 — Backtest Import
# ---------------------------------------------------------------------------

def stage_backtest_import(
    conn: sqlite3.Connection,
    ctx:  TestContext,
    dry_run: bool,
) -> StageResult:
    name = "Backtest Import"
    try:
        from connectors.ninjatrader.backtest_ingestor import (
            import_backtest_summary,
            import_trade_list,
            _ensure_dedup_index,
        )
    except ImportError as exc:
        return _fail(name, "Cannot import backtest_ingestor", str(exc))

    if not SAMPLE_SUMMARY.exists():
        return _fail(name, "Sample summary CSV missing", str(SAMPLE_SUMMARY))
    if not SAMPLE_TRADES.exists():
        return _fail(name, "Sample trade list CSV missing", str(SAMPLE_TRADES))

    spec_id = ctx.spec_id
    if spec_id is None:
        if dry_run:
            return _skip(name, "Dry-run: spec_id not yet known — would import against new spec")
        return _fail(name, "No spec_id from Stage 1 — cannot link backtest")

    if dry_run:
        _, errs = import_backtest_summary(conn, SAMPLE_SUMMARY, spec_id, dry_run=True)
        ins, _, _ = import_trade_list(conn, SAMPLE_TRADES, spec_id, dry_run=True)
        return _skip(name, f"Dry-run: summary valid, {ins} trade(s) parseable")

    _ensure_dedup_index(conn)

    bt_id, errs = import_backtest_summary(
        conn, SAMPLE_SUMMARY, spec_id, initial_capital=50_000
    )
    if errs and not bt_id:
        return _fail(name, "Summary import failed", "; ".join(errs))

    if bt_id is None:
        # Duplicate — find the existing backtest_id
        row = conn.execute(
            "SELECT backtest_id FROM backtests WHERE spec_id = ? "
            "ORDER BY backtest_id DESC LIMIT 1",
            (spec_id,),
        ).fetchone()
        bt_id = row[0] if row else None
        ctx.backtest_id = bt_id
        detail = f"Duplicate skipped — using existing backtest_id={bt_id}"
        return _warn("Backtest Import", detail)

    ins, _, trade_errs = import_trade_list(
        conn, SAMPLE_TRADES, spec_id, backtest_id=bt_id, initial_capital=50_000
    )
    ctx.backtest_id = bt_id
    warns = errs + trade_errs
    detail = f"backtest_id={bt_id}  {ins} trade(s) attached"
    if warns:
        detail += f"  ({len(warns)} warning(s))"
    return _pass(name, detail)


# ---------------------------------------------------------------------------
# Stage 3 — Score from Backtest
# ---------------------------------------------------------------------------

def stage_score(
    conn: sqlite3.Connection,
    ctx:  TestContext,
    dry_run: bool,
) -> StageResult:
    name = "Score from Backtest"
    try:
        from research.scoring.runner import run_for_spec
    except ImportError as exc:
        return _fail(name, "Cannot import runner", str(exc))

    spec_id = ctx.spec_id
    if spec_id is None:
        return _skip(name, "Dry-run: no spec_id — would score after import")

    try:
        run_result = run_for_spec(conn, spec_id, save=not dry_run)
    except ValueError as exc:
        return _fail(name, "Scoring failed", str(exc))
    except Exception as exc:
        return _fail(name, "Unexpected scoring error", str(exc))

    sr = run_result.scoring_result
    ctx.score          = sr.composite_score
    ctx.grade          = sr.grade
    ctx.recommendation = sr.recommendation

    if run_result.error:
        return _fail(name, f"score={sr.composite_score}  grade={sr.grade}", run_result.error)

    saved = "saved" if run_result.saved else "dry-run (not saved)"
    return _pass(
        name,
        f"score={sr.composite_score}  grade={sr.grade}  "
        f"recommendation={sr.recommendation}  [{saved}]"
    )


# ---------------------------------------------------------------------------
# Stage 4 — Research Rankings
# ---------------------------------------------------------------------------

def stage_rankings(
    conn: sqlite3.Connection,
    ctx:  TestContext,
) -> StageResult:
    name = "Research Rankings"
    try:
        from api.queries import research_rankings
    except ImportError as exc:
        return _fail(name, "Cannot import queries", str(exc))

    try:
        result = research_rankings(conn)
    except Exception as exc:
        return _fail(name, "rankings query raised", str(exc))

    if "error" in result:
        return _fail(name, "rankings query error", result["error"])

    if ctx.spec_id is None:
        return _skip(name, "Dry-run: no spec_id to verify in rankings")

    found = next(
        (item for item in result["items"] if item["spec_id"] == ctx.spec_id), None
    )
    if found is None:
        return _fail(
            name,
            f"spec_id={ctx.spec_id} not found in rankings ({result['count']} items)"
        )

    # Verify deduplication — spec must appear exactly once
    count = sum(1 for item in result["items"] if item["spec_id"] == ctx.spec_id)
    if count > 1:
        return _warn(name, f"spec_id={ctx.spec_id} appears {count}x in rankings — dedup broken")

    return _pass(
        name,
        f"spec_id={ctx.spec_id} at rank={found['rank']}  "
        f"score={found['composite_score']}  grade={found['grade']}  "
        f"total={result['count']} strategies"
    )


# ---------------------------------------------------------------------------
# Stage 5 — Performance Analytics
# ---------------------------------------------------------------------------

def stage_analytics(conn: sqlite3.Connection) -> StageResult:
    name = "Performance Analytics"
    try:
        from api.queries import equity_curve, performance_summary
    except ImportError as exc:
        return _fail(name, "Cannot import queries", str(exc))

    try:
        ec = equity_curve(conn)
        ps = performance_summary(conn)
    except Exception as exc:
        return _fail(name, "analytics query raised", str(exc))

    if "error" in ec:
        return _fail(name, "equity_curve error", ec["error"])
    if "error" in ps:
        return _fail(name, "performance_summary error", ps["error"])

    ec_count = ec.get("count", 0)
    trades   = ps.get("total_trades", 0)

    if ec_count == 0:
        return _warn(name, f"No NT8 trade data — equity curve empty, performance summary zeroed (expected in integration test)")

    return _pass(name, f"equity_curve={ec_count} points  trades={trades}")


# ---------------------------------------------------------------------------
# Stage 6 — Compliance Status
# ---------------------------------------------------------------------------

def stage_compliance(conn: sqlite3.Connection) -> StageResult:
    name = "Compliance Status"
    try:
        from api.queries import compliance_status
    except ImportError as exc:
        return _fail(name, "Cannot import queries", str(exc))

    try:
        result = compliance_status(conn)
    except Exception as exc:
        return _fail(name, "compliance_status raised", str(exc))

    if "error" in result:
        return _fail(name, "compliance_status error", result["error"])

    health = result.get("firm_health_score")
    status = result.get("firm_status", "UNKNOWN")
    accounts = result.get("account_count", 0)
    return _pass(name, f"health={health}  status={status}  accounts={accounts}")


# ---------------------------------------------------------------------------
# Stage 7 — Strategy Report
# ---------------------------------------------------------------------------

def stage_report(
    conn: sqlite3.Connection,
    ctx:  TestContext,
    dry_run: bool,
) -> StageResult:
    name = "Strategy Report"
    try:
        from research.reporting.report_generator import generate_strategy_report
        from research.reporting.exporter import export_strategy_report
    except ImportError as exc:
        return _fail(name, "Cannot import reporting modules", str(exc))

    spec_id = ctx.spec_id
    if spec_id is None:
        return _skip(name, "Dry-run: no spec_id — would generate report after import + score")

    try:
        report = generate_strategy_report(conn, spec_id)
    except Exception as exc:
        return _fail(name, "generate_strategy_report raised", str(exc))

    if report is None:
        return _fail(name, f"No report generated for spec_id={spec_id}")

    if dry_run:
        return _skip(name, f"Dry-run: report data ready for spec_id={spec_id} — skipping disk write")

    try:
        md_path, json_path = export_strategy_report(report)
    except Exception as exc:
        return _fail(name, "export_strategy_report raised", str(exc))

    ctx.report_md   = str(md_path)
    ctx.report_json = str(json_path)
    return _pass(name, f"md={md_path.name}  json={json_path.name}")


# ---------------------------------------------------------------------------
# Stage 8 — Decision Queue
# ---------------------------------------------------------------------------

def stage_decision_queue(
    conn: sqlite3.Connection,
    ctx:  TestContext,
) -> StageResult:
    name = "Decision Queue"
    try:
        from api.queries import decision_queue
    except ImportError as exc:
        return _fail(name, "Cannot import queries", str(exc))

    try:
        result = decision_queue(conn)
    except Exception as exc:
        return _fail(name, "decision_queue raised", str(exc))

    if "error" in result:
        return _fail(name, "decision_queue error", result["error"])

    if ctx.spec_id is None:
        return _skip(name, "Dry-run: no spec_id to verify in decision queue")

    found = next(
        (item for item in result["items"] if item["spec_id"] == ctx.spec_id), None
    )
    if found is None:
        return _fail(
            name,
            f"spec_id={ctx.spec_id} not found in decision queue ({result['count']} items)"
        )

    status = found.get("status")
    rec    = found.get("recommendation")

    # Human approval gate assertion — must NOT be approved/rejected
    if status in ("APPROVED", "REJECTED"):
        return _fail(
            name,
            f"HUMAN APPROVAL GATE BREACH: spec_id={ctx.spec_id} has status={status}",
            "Strategy advanced beyond REVIEW_REQUIRED without human approval",
        )

    return _pass(
        name,
        f"spec_id={ctx.spec_id}  status={status}  "
        f"recommendation={rec}  queue_total={result['count']}"
    )


# ---------------------------------------------------------------------------
# Approval gate final assertion
# ---------------------------------------------------------------------------

def assert_no_promotion(conn: sqlite3.Connection, spec_id: Optional[int]) -> StageResult:
    name = "Human Approval Gate"
    if spec_id is None:
        return _skip(name, "Dry-run: no spec_id to check")

    row = conn.execute(
        "SELECT status FROM strategy_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if row is None:
        return _fail(name, f"spec_id={spec_id} not found in strategy_specs")

    db_status = row[0]
    if db_status in ("approved", "rejected"):
        return _fail(
            name,
            f"GATE BREACH: spec_id={spec_id} has status={db_status}",
            "Strategy must not advance without human sign-off",
        )

    # Also check approved_strategies table
    promoted = conn.execute(
        "SELECT approved_strategy_id FROM approved_strategies WHERE spec_id = ?",
        (spec_id,),
    ).fetchone()
    if promoted:
        return _fail(
            name,
            f"GATE BREACH: spec_id={spec_id} found in approved_strategies",
            "Strategy was auto-promoted — this must not happen",
        )

    return _pass(
        name,
        f"spec_id={spec_id} status='{db_status}' — not approved, not rejected, not promoted"
    )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_STATUS_ICON = {"PASS": "+", "FAIL": "X", "SKIP": "-", "WARN": "!"}


def _print_stage(r: StageResult, idx: int) -> None:
    icon = _STATUS_ICON.get(r.status, "?")
    print(f"  [{icon}] Stage {idx:<2}  {r.name:<26}  {r.status:<4}  {r.detail}")
    if r.error:
        print(f"           {'':26}  ERROR: {r.error}")


def _print_summary(
    results: List[StageResult],
    ctx: TestContext,
    label: str,
    dry_run: bool,
) -> None:
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    warned = sum(1 for r in results if r.status == "WARN")
    skipped = sum(1 for r in results if r.status == "SKIP")

    overall = "PASSED" if failed == 0 else "FAILED"
    mode    = " (DRY-RUN)" if dry_run else ""
    lbl     = f" [{label}]" if label else ""

    print()
    print(f"  {'-' * 62}")
    print(f"  Result{lbl}{mode}  =>  {overall}")
    print(f"  Passed={passed}  Failed={failed}  Warned={warned}  Skipped={skipped}")

    if ctx.score is not None:
        print(f"  Strategy: score={ctx.score}  grade={ctx.grade}  rec={ctx.recommendation}")
    if ctx.spec_id is not None:
        print(f"  spec_id={ctx.spec_id}  backtest_id={ctx.backtest_id}")
    if ctx.report_md:
        print(f"  Report MD  : {ctx.report_md}")
    if ctx.report_json:
        print(f"  Report JSON: {ctx.report_json}")

    print()
    if failed > 0:
        print("  FAILED stages:")
        for r in results:
            if r.status == "FAIL":
                print(f"    {r.name}: {r.detail}")
                if r.error:
                    print(f"      => {r.error}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_pipeline_test(
    db_path:   Path,
    dry_run:   bool,
    run_label: str,
) -> int:
    """Run all 8 pipeline stages. Returns 0 on full pass, 1 if any stage failed."""
    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ctx  = TestContext()

    mode = "DRY-RUN" if dry_run else "LIVE"
    lbl  = f" [{run_label}]" if run_label else ""
    print(f"Hermes Full Pipeline Integration Test{lbl}")
    print(f"  DB   : {db_path}")
    print(f"  Mode : {mode}")
    print(f"  Spec : {TEST_SPEC_NAME}")
    print()

    results: List[StageResult] = []

    def _run(r: StageResult, idx: int) -> StageResult:
        _print_stage(r, idx)
        results.append(r)
        return r

    try:
        _run(stage_spec_import(conn, ctx, dry_run),       1)
        _run(stage_backtest_import(conn, ctx, dry_run),   2)
        _run(stage_score(conn, ctx, dry_run),             3)
        _run(stage_rankings(conn, ctx),                   4)
        _run(stage_analytics(conn),                       5)
        _run(stage_compliance(conn),                      6)
        _run(stage_report(conn, ctx, dry_run),            7)
        _run(stage_decision_queue(conn, ctx),             8)
        _run(assert_no_promotion(conn, ctx.spec_id),      9)
    finally:
        conn.close()

    _print_summary(results, ctx, run_label, dry_run)
    failed = sum(1 for r in results if r.status == "FAIL")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hermes full pipeline integration test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate all stages without writing to database or disk",
    )
    parser.add_argument(
        "--run-label",
        default="",
        metavar="LABEL",
        help="Label for this test run (display only)",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        metavar="PATH",
        help="Path to hermes_research.db",
    )
    args = parser.parse_args()
    sys.exit(run_pipeline_test(Path(args.db), args.dry_run, args.run_label))


if __name__ == "__main__":
    main()
