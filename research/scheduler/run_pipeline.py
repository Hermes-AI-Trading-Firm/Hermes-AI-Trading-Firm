#!/usr/bin/env python3
"""
Hermes Pipeline Runner — research/scheduler/run_pipeline.py

Executes the full research chain in sequence:
    import → score → report

Designed to be called manually or via Windows Task Scheduler.
No daemon. No continuous loop. Runs once and exits.

Usage
-----
Run full pipeline:
    python research/scheduler/run_pipeline.py

Run specific stages only:
    python research/scheduler/run_pipeline.py --stages import,score
    python research/scheduler/run_pipeline.py --stages report

Validate without writing:
    python research/scheduler/run_pipeline.py --dry-run

Custom database:
    python research/scheduler/run_pipeline.py --db path/to/other.db

Custom log file:
    python research/scheduler/run_pipeline.py --log logs/custom.log

Windows Task Scheduler example (daily at 06:00):
    Program:   python.exe
    Arguments: C:\\path\\to\\Hermes-AI-Trading-Firm\\research\\scheduler\\run_pipeline.py
    Start in:  C:\\path\\to\\Hermes-AI-Trading-Firm
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Path bootstrap — must run before any local imports
# ---------------------------------------------------------------------------

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[2]

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(log_path: Optional[Path], dry_run: bool) -> logging.Logger:
    logger = logging.getLogger("hermes.pipeline")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")

    # Console handler — always present
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler — skipped on dry-run if no explicit path given
    if log_path is not None and not dry_run:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


# ---------------------------------------------------------------------------
# Stage: import
# ---------------------------------------------------------------------------

def stage_import(
    conn: sqlite3.Connection,
    project_root: Path,
    dry_run: bool,
    logger: logging.Logger,
) -> bool:
    """Import NT8 trade and account files into the database."""
    logger.info("=== STAGE: import ===")

    try:
        from connectors.ninjatrader.nt8_sync import (
            import_trades,
            import_account,
            _ensure_dedup_indexes,
            DEFAULT_TRADES,
            DEFAULT_ACCOUNT,
        )
    except ImportError as exc:
        logger.error("Cannot import nt8_sync: %s", exc)
        return False

    trades_path  = project_root / "nt8_export" / "nt8_trades.csv"
    account_path = project_root / "nt8_export" / "nt8_account_state.json"

    if not dry_run:
        _ensure_dedup_indexes(conn)

    # Trades
    logger.info("Importing trades from: %s", trades_path)
    t_ins, t_skip, t_errs = import_trades(conn, trades_path, dry_run=dry_run)
    logger.info("Trades  inserted=%d  skipped=%d  errors=%d", t_ins, t_skip, len(t_errs))
    for e in t_errs:
        logger.warning("  Trade error: %s", e)

    # Account snapshots
    logger.info("Importing account from: %s", account_path)
    a_ins, a_skip, a_errs = import_account(conn, account_path, dry_run=dry_run)
    logger.info("Account inserted=%d  skipped=%d  errors=%d", a_ins, a_skip, len(a_errs))
    for e in a_errs:
        logger.warning("  Account error: %s", e)

    total_errs = len(t_errs) + len(a_errs)
    if total_errs > 0:
        logger.warning("Import completed with %d error(s)", total_errs)
    else:
        logger.info("Import completed successfully")

    return True


# ---------------------------------------------------------------------------
# Stage: score
# ---------------------------------------------------------------------------

def stage_score(
    conn: sqlite3.Connection,
    dry_run: bool,
    logger: logging.Logger,
) -> bool:
    """Re-score all strategies from the database."""
    logger.info("=== STAGE: score ===")

    try:
        from research.scoring.runner import run_from_db
    except ImportError as exc:
        logger.error("Cannot import scoring runner: %s", exc)
        return False

    save = not dry_run
    logger.info("Running batch score (save=%s)", save)

    try:
        summary = run_from_db(conn, save=save)
    except Exception as exc:
        logger.error("Scoring failed: %s", exc)
        return False

    logger.info(
        "Score  total=%d  saved=%d  errors=%d",
        summary.total, summary.saved, summary.errors,
    )
    for grade, count in sorted(summary.by_grade.items()):
        logger.info("  Grade %-3s : %d", grade, count)
    for rec, count in sorted(summary.by_recommendation.items()):
        logger.info("  %-20s : %d", rec, count)

    if summary.errors > 0:
        logger.warning("Scoring completed with %d error(s)", summary.errors)
    else:
        logger.info("Scoring completed successfully")

    return True


# ---------------------------------------------------------------------------
# Stage: report
# ---------------------------------------------------------------------------

def stage_report(
    conn: sqlite3.Connection,
    dry_run: bool,
    logger: logging.Logger,
) -> bool:
    """Generate Markdown + JSON strategy reports."""
    logger.info("=== STAGE: report ===")

    try:
        from research.reporting.report_generator import generate_all_reports
        from research.reporting.exporter import export_all
    except ImportError as exc:
        logger.error("Cannot import reporting modules: %s", exc)
        return False

    logger.info("Generating reports from database")

    try:
        reports = generate_all_reports(conn)
    except Exception as exc:
        logger.error("Report generation failed: %s", exc)
        return False

    logger.info("Generated %d strategy report(s)", len(reports))

    if dry_run:
        logger.info("Dry-run: skipping export to disk")
        return True

    try:
        paths = export_all(reports)
    except Exception as exc:
        logger.error("Report export failed: %s", exc)
        return False

    if "summary" in paths and paths["summary"]:
        logger.info("Firm summary: %s", paths["summary"].get("md", "n/a"))
    for spec_id, spec_paths in paths.get("strategies", {}).items():
        logger.info("  Strategy %s: %s", spec_id, spec_paths.get("md", "n/a"))

    logger.info("Reports completed successfully")
    return True


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_ALL_STAGES = ("import", "score", "report")


def run_pipeline(
    db_path: Path,
    stages: List[str],
    dry_run: bool,
    log_path: Optional[Path],
) -> int:
    """
    Execute the requested pipeline stages in order.

    Returns 0 on full success, 1 if any stage failed or was skipped due to error.
    """
    logger = _setup_logging(log_path, dry_run)

    ts = datetime.now().isoformat(timespec="seconds")
    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info("Hermes Pipeline Runner — %s — mode=%s", ts, mode)
    logger.info("Stages: %s", ", ".join(stages))
    logger.info("DB    : %s", db_path)

    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    overall_ok = True
    t0 = time.monotonic()

    try:
        for stage in stages:
            if stage not in _ALL_STAGES:
                logger.error("Unknown stage '%s' — valid: %s", stage, ", ".join(_ALL_STAGES))
                overall_ok = False
                continue

            stage_t0 = time.monotonic()
            project_root = Path(db_path).resolve().parent.parent  # database/ → project root
            if stage == "import":
                ok = stage_import(conn, project_root, dry_run, logger)
            elif stage == "score":
                ok = stage_score(conn, dry_run, logger)
            elif stage == "report":
                ok = stage_report(conn, dry_run, logger)
            else:
                ok = False

            elapsed = time.monotonic() - stage_t0
            status = "OK" if ok else "FAILED"
            logger.info("Stage '%s' finished in %.1fs — %s", stage, elapsed, status)

            if not ok:
                overall_ok = False

    finally:
        conn.close()

    total_elapsed = time.monotonic() - t0
    final_status = "SUCCESS" if overall_ok else "FAILED"
    logger.info("Pipeline complete in %.1fs — %s", total_elapsed, final_status)

    return 0 if overall_ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_log_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("logs") / f"pipeline_{ts}.log"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hermes pipeline runner — import → score → report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--stages",
        default="import,score,report",
        metavar="STAGES",
        help="Comma-separated list of stages to run (default: import,score,report)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and count without writing to DB or disk",
    )
    parser.add_argument(
        "--db",
        default=str(_PROJECT_ROOT / "database" / "hermes_research.db"),
        metavar="PATH",
        help="Path to hermes_research.db",
    )
    parser.add_argument(
        "--log",
        default=None,
        metavar="PATH",
        help="Log file path (default: logs/pipeline_YYYYMMDD_HHMMSS.log)",
    )
    args = parser.parse_args()

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    db_path = Path(args.db)
    log_path = Path(args.log) if args.log else (_default_log_path() if not args.dry_run else None)

    sys.exit(run_pipeline(db_path, stages, args.dry_run, log_path))


if __name__ == "__main__":
    main()
