#!/usr/bin/env python3
"""
Strategy Lifecycle Management -- research/lifecycle/lifecycle.py

Phase 31

Question: "Where is each strategy in its research journey?"

The Three-Question Test:
  Can it produce better evidence?   YES -- full lifecycle state view
  Can it ask a better question?     YES -- shows exactly what is missing and why
  Does a human still decide?        YES -- REVIEW_REQUIRED is the maximum automated state

Lifecycle states (sequential)
------------------------------
  IDEA                -- concept exists; no spec in database
  SPEC_IMPORTED       -- strategy spec exists in strategy_specs table
  BACKTEST_IMPORTED   -- IS backtest exists with trade_list_json
  SCORED              -- scoring_results row exists
  AUDITED             -- audit report JSON found in reports/audits/
  VALIDATED_MC        -- Monte Carlo score in DB or MC JSON in reports/validation/
  VALIDATED_WF        -- walk-forward score in DB or WF JSON in reports/validation/
  REGIME_ANALYZED     -- regime report JSON found in reports/regime/
  DECISION_PACKAGED   -- decision package JSON exists (any readiness status)
  REVIEW_REQUIRED     -- decision package readiness == READY_FOR_HUMAN_REVIEW
  HUMAN_APPROVED      -- approval file found in research/approved/
  HUMAN_REJECTED      -- rejection file found in research/rejected/
  ARCHIVED            -- archive marker found in research/archived/

Rules (encoded in logic, not just documentation)
-------------------------------------------------
  Rule 1: Automated pipeline may advance state only up to REVIEW_REQUIRED.
  Rule 2: HUMAN_APPROVED and HUMAN_REJECTED require file evidence that only
          a human command can create (research/approved/, research/rejected/).
  Rule 3: No lifecycle state triggers execution of any kind.
  Rule 4: Lifecycle output is advisory and state-tracking only.
  Rule 5: This module does not modify strategy logic or broker systems.

Human state detection
---------------------
  HUMAN_APPROVED : any file under research/approved/ prefixed with safe_name
  HUMAN_REJECTED : any file under research/rejected/ prefixed with safe_name
  ARCHIVED       : any file under research/archived/ prefixed with safe_name

State inference is read-only. No files are created or modified to determine state.

Usage
-----
    python -m research.lifecycle.lifecycle --spec-id N
    python -m research.lifecycle.lifecycle --all
    python -m research.lifecycle.lifecycle --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB       = _PROJECT_ROOT / "database"  / "hermes_research.db"
REPORTS_DIR      = _PROJECT_ROOT / "reports"   / "lifecycle"
AUDIT_DIR        = _PROJECT_ROOT / "reports"   / "audits"
VALIDATION_DIR   = _PROJECT_ROOT / "reports"   / "validation"
REGIME_DIR       = _PROJECT_ROOT / "reports"   / "regime"
DECISION_PKG_DIR = _PROJECT_ROOT / "reports"   / "decision_packages"
APPROVED_DIR     = _PROJECT_ROOT / "research"  / "approved"
REJECTED_DIR     = _PROJECT_ROOT / "research"  / "rejected"
ARCHIVED_DIR     = _PROJECT_ROOT / "research"  / "archived"


# ---------------------------------------------------------------------------
# State registry
# ---------------------------------------------------------------------------

# Ordered from earliest to most advanced.
# States beyond REVIEW_REQUIRED are human-only.
LIFECYCLE_STATES: List[str] = [
    "IDEA",
    "SPEC_IMPORTED",
    "BACKTEST_IMPORTED",
    "SCORED",
    "AUDITED",
    "VALIDATED_MC",
    "VALIDATED_WF",
    "REGIME_ANALYZED",
    "DECISION_PACKAGED",
    "REVIEW_REQUIRED",
    "HUMAN_APPROVED",
    "HUMAN_REJECTED",
    "ARCHIVED",
]

_STATE_RANK: Dict[str, int] = {s: i for i, s in enumerate(LIFECYCLE_STATES)}

# Maximum state reachable by automated pipeline (Rule 1)
MAX_AUTOMATED_STATE = "REVIEW_REQUIRED"
MAX_AUTOMATED_RANK  = _STATE_RANK[MAX_AUTOMATED_STATE]

# States that require human action (Rule 2)
HUMAN_STATES = {"HUMAN_APPROVED", "HUMAN_REJECTED", "ARCHIVED"}

# Next milestone and how to reach it
_NEXT_STEP: Dict[str, Tuple[str, str]] = {
    "IDEA": (
        "SPEC_IMPORTED",
        "Import strategy spec to database",
    ),
    "SPEC_IMPORTED": (
        "BACKTEST_IMPORTED",
        "Import NT8 backtest with trade list",
    ),
    "BACKTEST_IMPORTED": (
        "SCORED",
        "python -m research.scoring.score_strategies --spec-id N",
    ),
    "SCORED": (
        "AUDITED",
        "python -m research.audit.strategy_auditor --spec-id N",
    ),
    "AUDITED": (
        "VALIDATED_MC",
        "python -m research.validation.monte_carlo --spec-id N",
    ),
    "VALIDATED_MC": (
        "VALIDATED_WF",
        "Import OOS backtest (--oos) then run walk-forward engine",
    ),
    "VALIDATED_WF": (
        "REGIME_ANALYZED",
        "python -m research.regime.regime_analyzer --spec-id N",
    ),
    "REGIME_ANALYZED": (
        "DECISION_PACKAGED",
        "python -m research.decision.decision_package --spec-id N",
    ),
    "DECISION_PACKAGED": (
        "REVIEW_REQUIRED",
        "Address blockers in decision package and regenerate",
    ),
    "REVIEW_REQUIRED": (
        "HUMAN_APPROVED or HUMAN_REJECTED",
        "Human review required -- REVIEW_REQUIRED is the terminal automated state",
    ),
    "HUMAN_APPROVED": (
        "Forward Testing",
        "Human decision required for next step",
    ),
    "HUMAN_REJECTED": (
        "ARCHIVED",
        "Archive rejected strategy with documented reason in research/rejected/",
    ),
    "ARCHIVED": (
        "(terminal)",
        "No further automated action",
    ),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EvidenceFlags:
    has_spec:          bool = False
    has_backtest:      bool = False
    has_trade_list:    bool = False
    has_scoring:       bool = False
    has_audit:         bool = False
    has_mc:            bool = False
    has_wf:            bool = False
    has_regime:        bool = False
    has_decision_pkg:  bool = False
    pkg_ready:         bool = False   # readiness == READY_FOR_HUMAN_REVIEW
    human_approved:    bool = False
    human_rejected:    bool = False
    archived:          bool = False


@dataclass
class LifecycleRecord:
    spec_id:        int
    spec_name:      str
    symbol:         str
    timeframe:      str
    state:          str
    state_rank:     int
    next_milestone: str
    next_step:      str
    evidence:       EvidenceFlags
    generated_at:   str


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name)


def _any_file(directory: Path, prefix: str) -> bool:
    if not directory.exists():
        return False
    return any(directory.glob(f"{prefix}*"))


def _latest_json(directory: Path, prefix: str, infix: str) -> Optional[Dict]:
    if not directory.exists():
        return None
    matches = sorted(directory.glob(f"{prefix}{infix}*.json"))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _all_spec_ids(conn: sqlite3.Connection) -> List[int]:
    return [r[0] for r in conn.execute(
        "SELECT spec_id FROM strategy_specs ORDER BY spec_id"
    ).fetchall()]


def _fetch_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT spec_id, spec_name, COALESCE(symbol,''), COALESCE(timeframe,'') "
        "FROM strategy_specs WHERE spec_id = ?",
        (spec_id,),
    ).fetchone()
    if not row:
        return None
    return {"spec_id": row[0], "spec_name": row[1],
            "symbol": row[2], "timeframe": row[3]}


def _has_scoring(conn: sqlite3.Connection, spec_id: int) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM scoring_results WHERE spec_id = ? LIMIT 1",
        (spec_id,),
    ).fetchone())


def _has_backtest(conn: sqlite3.Connection, spec_id: int) -> Tuple[bool, bool]:
    """Returns (has_any_backtest, has_trade_list)."""
    row = conn.execute(
        "SELECT trade_list_json IS NOT NULL AND trade_list_json != '' "
        "FROM backtests WHERE spec_id = ? AND is_in_sample = 1 "
        "ORDER BY backtest_id DESC LIMIT 1",
        (spec_id,),
    ).fetchone()
    if not row:
        return False, False
    return True, bool(row[0])


def _scoring_scores(conn: sqlite3.Connection, spec_id: int) -> Tuple[Optional[float], Optional[float]]:
    """Returns (walk_forward_score, monte_carlo_score)."""
    row = conn.execute(
        "SELECT walk_forward_score, monte_carlo_score FROM scoring_results "
        "WHERE spec_id = ? ORDER BY scoring_id DESC LIMIT 1",
        (spec_id,),
    ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------

def _collect_evidence(
    conn:    sqlite3.Connection,
    spec_id: int,
    name:    str,
) -> EvidenceFlags:
    safe = _safe_name(name)
    ev   = EvidenceFlags()

    ev.has_spec = True  # we only call this when spec exists

    has_bt, has_tl = _has_backtest(conn, spec_id)
    ev.has_backtest   = has_bt
    ev.has_trade_list = has_tl

    ev.has_scoring = _has_scoring(conn, spec_id)

    wf_score, mc_score = _scoring_scores(conn, spec_id)

    # Audit
    ev.has_audit = _any_file(AUDIT_DIR, safe)

    # Monte Carlo -- DB score or report file
    ev.has_mc = (
        mc_score is not None
        or bool(_latest_json(VALIDATION_DIR, safe, "_monte_carlo_"))
    )

    # Walk-forward -- DB score or report file
    ev.has_wf = (
        wf_score is not None
        or bool(_latest_json(VALIDATION_DIR, safe, "_walk_forward_"))
    )

    # Regime
    ev.has_regime = bool(_latest_json(REGIME_DIR, safe, "_regime_analysis_"))

    # Decision package
    pkg = _latest_json(DECISION_PKG_DIR, safe, "_decision_package_")
    if pkg:
        ev.has_decision_pkg = True
        ev.pkg_ready = (pkg.get("readiness_status") == "READY_FOR_HUMAN_REVIEW")

    # Human states -- file presence only (Rule 2)
    ev.human_approved = _any_file(APPROVED_DIR, safe)
    ev.human_rejected = _any_file(REJECTED_DIR, safe)
    ev.archived       = _any_file(ARCHIVED_DIR, safe)

    return ev


# ---------------------------------------------------------------------------
# State inference
# ---------------------------------------------------------------------------

def _infer_state(ev: EvidenceFlags) -> str:
    # Terminal human states first -- these override all automated states (Rule 2)
    if ev.archived:
        return "ARCHIVED"
    if ev.human_rejected:
        return "HUMAN_REJECTED"
    if ev.human_approved:
        return "HUMAN_APPROVED"

    # Automated states -- highest milestone reached (Rule 1: cap at REVIEW_REQUIRED)
    if ev.has_decision_pkg and ev.pkg_ready:
        return "REVIEW_REQUIRED"
    if ev.has_decision_pkg:
        return "DECISION_PACKAGED"
    if ev.has_regime:
        return "REGIME_ANALYZED"
    if ev.has_wf:
        return "VALIDATED_WF"
    if ev.has_mc:
        return "VALIDATED_MC"
    if ev.has_audit:
        return "AUDITED"
    if ev.has_scoring:
        return "SCORED"
    if ev.has_trade_list:
        return "BACKTEST_IMPORTED"
    if ev.has_backtest:
        return "BACKTEST_IMPORTED"
    if ev.has_spec:
        return "SPEC_IMPORTED"
    return "IDEA"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_lifecycle_states() -> List[str]:
    """Return all lifecycle states in order."""
    return list(LIFECYCLE_STATES)


def infer_lifecycle_state(
    conn:    sqlite3.Connection,
    spec_id: int,
) -> Optional[LifecycleRecord]:
    """
    Infer the current lifecycle state for spec_id from DB + file evidence.
    Returns None if the spec does not exist.
    No writes. No state changes. Read-only.
    """
    spec = _fetch_spec(conn, spec_id)
    if not spec:
        return None

    name = spec["spec_name"]
    ev   = _collect_evidence(conn, spec_id, name)
    state = _infer_state(ev)

    next_milestone, next_step = _NEXT_STEP.get(
        state, ("(unknown)", "No guidance available")
    )

    return LifecycleRecord(
        spec_id        = spec_id,
        spec_name      = name,
        symbol         = spec["symbol"],
        timeframe      = spec["timeframe"],
        state          = state,
        state_rank     = _STATE_RANK.get(state, -1),
        next_milestone = next_milestone,
        next_step      = next_step,
        evidence       = ev,
        generated_at   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    )


def lifecycle_summary(
    conn: sqlite3.Connection,
) -> Tuple[List[LifecycleRecord], Dict[str, int]]:
    """
    Return (records, state_counts) for all specs in the database.
    Records are sorted by state_rank descending (most advanced first).
    """
    spec_ids = _all_spec_ids(conn)
    records: List[LifecycleRecord] = []
    for sid in spec_ids:
        rec = infer_lifecycle_state(conn, sid)
        if rec:
            records.append(rec)

    records.sort(key=lambda r: r.state_rank, reverse=True)

    counts: Dict[str, int] = {}
    for r in records:
        counts[r.state] = counts.get(r.state, 0) + 1

    return records, counts


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _ev_bar(ev: EvidenceFlags) -> str:
    """Single-line ASCII evidence bar: [S][B][Sc][Au][MC][WF][Re][Dp][RR]"""
    def c(flag: bool, label: str) -> str:
        return f"[{label}]" if flag else f"[{'.' * len(label)}]"

    return (
        c(ev.has_spec,         "S")
        + c(ev.has_trade_list, "B")
        + c(ev.has_scoring,    "Sc")
        + c(ev.has_audit,      "Au")
        + c(ev.has_mc,         "MC")
        + c(ev.has_wf,         "WF")
        + c(ev.has_regime,     "Re")
        + c(ev.has_decision_pkg, "Dp")
        + c(ev.pkg_ready,      "RR")
    )


def generate_lifecycle_report(
    records:     List[LifecycleRecord],
    state_counts: Dict[str, int],
    reports_dir: Path = REPORTS_DIR,
    dry_run:     bool = False,
) -> Tuple[Optional[Path], Optional[Path]]:
    date_str  = datetime.now().strftime("%Y%m%d")
    total     = len(records)
    generated = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Markdown
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    p("# Strategy Lifecycle Report")
    p(f"**Generated:** {generated[:10]}  |  **Strategies:** {total}")
    p()
    p("> Where is each strategy in its research journey?")
    p()
    p(f"Legend: [S]=Spec [B]=Backtest [Sc]=Scored [Au]=Audited "
      f"[MC]=Monte Carlo [WF]=Walk-Forward [Re]=Regime [Dp]=Decision Pkg [RR]=Review Required")
    p()
    p("---")
    p()

    # State distribution
    p("## State Distribution")
    p()
    p("| State | Count |")
    p("|-------|-------|")
    for state in LIFECYCLE_STATES:
        n = state_counts.get(state, 0)
        if n:
            tag = " <- HUMAN ACTION REQUIRED" if state == "REVIEW_REQUIRED" else ""
            p(f"| {state} | {n}{tag} |")
    p()

    # Attention: strategies awaiting human review
    rr = [r for r in records if r.state == "REVIEW_REQUIRED"]
    if rr:
        p("## Awaiting Human Review")
        p()
        p("These strategies have completed the automated pipeline.")
        p("Human review is required. No automated action will advance them further.")
        p()
        for r in rr:
            p(f"- **{r.spec_name}** (spec_id={r.spec_id})  "
              f"{r.symbol} {r.timeframe}")
        p()

    # Full strategy table
    p("## All Strategies")
    p()
    p("| spec_id | Strategy | State | Evidence | Next Milestone |")
    p("|---------|----------|-------|----------|----------------|")
    for r in records:
        bar    = _ev_bar(r.evidence)
        p(f"| {r.spec_id} | {r.spec_name} | {r.state} | {bar} | {r.next_milestone} |")
    p()

    # Per-strategy detail
    p("## Per-Strategy Detail")
    p()
    for r in records:
        p(f"### {r.spec_name}  (spec_id={r.spec_id})")
        p(f"**State:** {r.state}  |  "
          f"**Symbol:** {r.symbol or '-'}  "
          f"**Timeframe:** {r.timeframe or '-'}")
        p()
        p(f"**Evidence:** {_ev_bar(r.evidence)}")
        p()
        p(f"**Next milestone:** {r.next_milestone}")
        p(f"**How to advance:** {r.next_step}")
        p()

        if r.state in HUMAN_STATES:
            p(f"> This strategy has passed REVIEW_REQUIRED. "
              f"Current state ({r.state}) was set by human action.")
        elif r.state == "REVIEW_REQUIRED":
            p("> **REVIEW_REQUIRED** -- terminal automated state. "
              "Human review is mandatory before any further action.")
        else:
            p(f"> Automated pipeline may advance this strategy to "
              f"**REVIEW_REQUIRED** through normal research steps.")
        p()

    p("---")
    p()
    p("*Read-only advisory output. No database writes. No strategy changes.*")
    p(f"*Maximum automated state: {MAX_AUTOMATED_STATE}*")
    p("*HUMAN_APPROVED and HUMAN_REJECTED require explicit human action.*")
    p("*Accumulate evidence. Improve questions. Preserve authority.*")

    md_content = "\n".join(lines)

    # JSON
    json_content = json.dumps(
        {
            "generated_at":        generated,
            "total_strategies":    total,
            "max_automated_state": MAX_AUTOMATED_STATE,
            "state_distribution":  state_counts,
            "review_required_count": len(rr),
            "strategies": [
                {
                    "spec_id":        r.spec_id,
                    "spec_name":      r.spec_name,
                    "symbol":         r.symbol,
                    "timeframe":      r.timeframe,
                    "state":          r.state,
                    "state_rank":     r.state_rank,
                    "next_milestone": r.next_milestone,
                    "next_step":      r.next_step,
                    "evidence": {
                        "has_spec":         r.evidence.has_spec,
                        "has_backtest":     r.evidence.has_backtest,
                        "has_trade_list":   r.evidence.has_trade_list,
                        "has_scoring":      r.evidence.has_scoring,
                        "has_audit":        r.evidence.has_audit,
                        "has_mc":           r.evidence.has_mc,
                        "has_wf":           r.evidence.has_wf,
                        "has_regime":       r.evidence.has_regime,
                        "has_decision_pkg": r.evidence.has_decision_pkg,
                        "pkg_ready":        r.evidence.pkg_ready,
                        "human_approved":   r.evidence.human_approved,
                        "human_rejected":   r.evidence.human_rejected,
                        "archived":         r.evidence.archived,
                    },
                }
                for r in records
            ],
        },
        indent=2,
    )

    if dry_run:
        return None, None

    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path   = reports_dir / f"lifecycle_summary_{date_str}.md"
    json_path = reports_dir / f"lifecycle_summary_{date_str}.json"
    md_path.write_text(md_content,   encoding="utf-8")
    json_path.write_text(json_content, encoding="utf-8")
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_record(r: LifecycleRecord, dry_run: bool = False) -> None:
    tag = "  [DRY-RUN]" if dry_run else ""
    print(f"LIFECYCLE: {r.spec_name}  [spec_id={r.spec_id}]{tag}")
    print(f"  Symbol    : {r.symbol or '(none)'}  {r.timeframe or ''}")
    print(f"  State     : {r.state}  (rank {r.state_rank} of {len(LIFECYCLE_STATES) - 1})")
    print(f"  Evidence  : {_ev_bar(r.evidence)}")
    print(f"  Next      : {r.next_milestone}")
    print(f"  How       : {r.next_step}")

    if r.state == "REVIEW_REQUIRED":
        print(f"  *** REVIEW_REQUIRED -- terminal automated state ***")
        print(f"  *** Human review is mandatory before any further action ***")
    elif r.state in HUMAN_STATES:
        print(f"  *** {r.state} -- set by human action ***")
    print()


def _print_summary(records: List[LifecycleRecord], counts: Dict[str, int]) -> None:
    total = len(records)
    rr    = counts.get("REVIEW_REQUIRED", 0)
    print(f"Lifecycle Summary  |  {total} strategies  |  {rr} awaiting human review")
    print()
    print(f"  {'State':<30}  {'Count':>5}")
    print(f"  {'-'*30}  {'-'*5}")
    for state in LIFECYCLE_STATES:
        n = counts.get(state, 0)
        if n:
            flag = "  <- HUMAN ACTION REQUIRED" if state == "REVIEW_REQUIRED" else ""
            print(f"  {state:<30}  {n:>5}{flag}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Strategy Lifecycle Management (Phase 31). "
            "Question: Where is each strategy in its research journey? "
            "No DB writes. No strategy changes. "
            f"Maximum automated state: {MAX_AUTOMATED_STATE}."
        )
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec-id", type=int, metavar="ID",
                     help="Show lifecycle state for one strategy")
    grp.add_argument("--all",     action="store_true",
                     help="Show lifecycle state for all strategies")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Console output only -- no files written")
    parser.add_argument("--db",          default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR), metavar="DIR")
    args = parser.parse_args()

    db_path     = Path(args.db)
    reports_dir = Path(args.reports_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes Strategy Lifecycle  [{mode}]")
    print(f"  DB                   : {db_path}")
    print(f"  Max automated state  : {MAX_AUTOMATED_STATE}")
    print(f"  Advisory only -- no DB writes, no strategy changes")
    if not args.dry_run:
        print(f"  Reports              : {reports_dir}")
    print()

    conn = sqlite3.connect(str(db_path))
    try:
        if args.spec_id is not None:
            rec = infer_lifecycle_state(conn, args.spec_id)
            if not rec:
                print(f"ERROR: spec_id={args.spec_id} not found in database")
                sys.exit(1)
            _print_record(rec, dry_run=args.dry_run)
            if not args.dry_run:
                md_path, json_path = generate_lifecycle_report(
                    [rec], {rec.state: 1}, reports_dir, dry_run=False
                )
                if md_path:
                    print(f"  Reports")
                    print(f"    MD  : {md_path}")
                    print(f"    JSON: {json_path}")
                    print()
        else:
            records, counts = lifecycle_summary(conn)
            if not records:
                print("No strategies found in database.")
                sys.exit(0)

            for r in records:
                _print_record(r, dry_run=args.dry_run)

            _print_summary(records, counts)

            if not args.dry_run:
                md_path, json_path = generate_lifecycle_report(
                    records, counts, reports_dir, dry_run=False
                )
                if md_path:
                    print(f"  Reports")
                    print(f"    MD  : {md_path}")
                    print(f"    JSON: {json_path}")
                    print()
            else:
                print("DRY-RUN complete. No files written. No DB changes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
