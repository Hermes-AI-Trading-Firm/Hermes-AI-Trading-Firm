#!/usr/bin/env python3
"""
Event Timeline -- research/timeline/event_timeline.py

Phase 42

Reads across all six pipeline layers and merges them into a single
chronological event stream.

Six layers:
  Evidence Layer    -- backtests, scoring, regime, optimization
  Knowledge Layer   -- strategy creation, idea creation, approvals, rejections
  Question Layer    -- questions generated (current state; no persistent history)
  Priority Layer    -- priority rankings (current state; no persistent history)
  Review Layer      -- human decisions at REVIEW_REQUIRED
  Governance Layer  -- pipeline changes, phase additions, rule changes

What it does NOT do
-------------------
- Does not write to the database
- Does not change strategy state
- Does not make decisions
- Does not advance any strategy past REVIEW_REQUIRED
- Does not generate questions or priorities (read-only from existing sources)

Phase compliance
----------------
  [1] No DB writes
  [2] No strategy state changes
  [3] No approval automation
  [4] No path beyond REVIEW_REQUIRED
  [5] Outputs are advisory only
  [6] Human authority unchanged

Usage
-----
    # Show full timeline (all strategies, all layers)
    python -m research.timeline.event_timeline

    # Filter to one strategy
    python -m research.timeline.event_timeline --spec-id 1
    python -m research.timeline.event_timeline --spec-name BTC_REGIME_BREAKOUT_v001

    # Filter by layer
    python -m research.timeline.event_timeline --layer EVIDENCE
    python -m research.timeline.event_timeline --layer REVIEW

    # Filter by date
    python -m research.timeline.event_timeline --since 2026-06-01

    # Write report to reports/timeline/
    python -m research.timeline.event_timeline --report
    python -m research.timeline.event_timeline --spec-id 1 --report --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
_DB_DEFAULT   = _PROJECT_ROOT / "database" / "hermes_research.db"

VALID_LAYERS = {"EVIDENCE", "KNOWLEDGE", "REVIEW", "GOVERNANCE"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# TimelineEvent
# ---------------------------------------------------------------------------

@dataclass
class TimelineEvent:
    event_id:   str
    timestamp:  str                # ISO datetime or YYYY-MM-DD
    layer:      str                # EVIDENCE / KNOWLEDGE / REVIEW / GOVERNANCE
    event_type: str                # specific event type within the layer
    spec_id:    Optional[int]
    spec_name:  str                # strategy name or "" if system-wide
    summary:    str                # one-line description
    detail:     Dict[str, Any]


# ---------------------------------------------------------------------------
# Evidence Layer
# ---------------------------------------------------------------------------

def _events_evidence(conn: sqlite3.Connection, spec_id: Optional[int] = None) -> List[TimelineEvent]:
    events: List[TimelineEvent] = []

    # Backtests
    q = """
        SELECT b.backtest_id, b.spec_id, s.spec_name,
               b.is_in_sample, b.profit_factor, b.total_trades,
               b.sharpe_ratio, b.max_drawdown_pct, b.created_at
        FROM backtests b
        JOIN strategy_specs s ON s.spec_id = b.spec_id
    """
    rows = conn.execute(q + (" WHERE b.spec_id = ?" if spec_id else ""),
                        (spec_id,) if spec_id else ()).fetchall()
    for r in rows:
        kind = "IS backtest" if r["is_in_sample"] else "OOS backtest"
        pf   = r["profit_factor"]   or 0
        tr   = r["total_trades"]    or 0
        sh   = r["sharpe_ratio"]    or 0
        events.append(TimelineEvent(
            event_id=  f"bt-{r['backtest_id']}",
            timestamp= r["created_at"] or "",
            layer=     "EVIDENCE",
            event_type="BACKTEST_RUN",
            spec_id=   r["spec_id"],
            spec_name= r["spec_name"],
            summary=   f"{kind}: PF={pf:.2f}  trades={tr}  Sharpe={sh:.2f}",
            detail={
                "backtest_id":      r["backtest_id"],
                "is_in_sample":     r["is_in_sample"],
                "profit_factor":    pf,
                "total_trades":     tr,
                "sharpe_ratio":     sh,
                "max_drawdown_pct": r["max_drawdown_pct"],
            },
        ))

    # Scoring
    q = """
        SELECT sc.scoring_id, sc.spec_id, s.spec_name,
               sc.composite_score, sc.grade, sc.recommendation,
               sc.walk_forward_pass, sc.monte_carlo_pass, sc.scored_at
        FROM scoring_results sc
        JOIN strategy_specs s ON s.spec_id = sc.spec_id
    """
    rows = conn.execute(q + (" WHERE sc.spec_id = ?" if spec_id else ""),
                        (spec_id,) if spec_id else ()).fetchall()
    for r in rows:
        score = r["composite_score"] or 0
        events.append(TimelineEvent(
            event_id=  f"sc-{r['scoring_id']}",
            timestamp= r["scored_at"] or "",
            layer=     "EVIDENCE",
            event_type="STRATEGY_SCORED",
            spec_id=   r["spec_id"],
            spec_name= r["spec_name"],
            summary=   f"Scored: {r['grade']}  composite={score:.1f}  {r['recommendation']}",
            detail={
                "scoring_id":        r["scoring_id"],
                "composite_score":   score,
                "grade":             r["grade"],
                "recommendation":    r["recommendation"],
                "walk_forward_pass": r["walk_forward_pass"],
                "monte_carlo_pass":  r["monte_carlo_pass"],
            },
        ))

    # Regime analysis
    q = """
        SELECT ra.regime_analysis_id, ra.spec_id, s.spec_name,
               ra.regime_model, ra.best_regime, ra.worst_regime,
               ra.status, ra.created_at
        FROM regime_analysis ra
        JOIN strategy_specs s ON s.spec_id = ra.spec_id
    """
    rows = conn.execute(q + (" WHERE ra.spec_id = ?" if spec_id else ""),
                        (spec_id,) if spec_id else ()).fetchall()
    for r in rows:
        events.append(TimelineEvent(
            event_id=  f"re-{r['regime_analysis_id']}",
            timestamp= r["created_at"] or "",
            layer=     "EVIDENCE",
            event_type="REGIME_ANALYZED",
            spec_id=   r["spec_id"],
            spec_name= r["spec_name"],
            summary=   f"Regime analyzed: best={r['best_regime']}  worst={r['worst_regime']}  status={r['status']}",
            detail={
                "regime_analysis_id": r["regime_analysis_id"],
                "regime_model":       r["regime_model"],
                "best_regime":        r["best_regime"],
                "worst_regime":       r["worst_regime"],
                "status":             r["status"],
            },
        ))

    # Optimizations
    q = """
        SELECT o.optimization_id, o.spec_id, s.spec_name,
               o.method, o.optimized_profit_factor,
               o.overfit_warning, o.status, o.created_at
        FROM optimizations o
        JOIN strategy_specs s ON s.spec_id = o.spec_id
    """
    rows = conn.execute(q + (" WHERE o.spec_id = ?" if spec_id else ""),
                        (spec_id,) if spec_id else ()).fetchall()
    for r in rows:
        pf = r["optimized_profit_factor"] or 0
        events.append(TimelineEvent(
            event_id=  f"op-{r['optimization_id']}",
            timestamp= r["created_at"] or "",
            layer=     "EVIDENCE",
            event_type="OPTIMIZATION_RUN",
            spec_id=   r["spec_id"],
            spec_name= r["spec_name"],
            summary=   f"Optimization ({r['method']}): PF={pf:.2f}  overfit={r['overfit_warning']}",
            detail={
                "optimization_id":         r["optimization_id"],
                "method":                  r["method"],
                "optimized_profit_factor": pf,
                "overfit_warning":         r["overfit_warning"],
                "status":                  r["status"],
            },
        ))

    return events


# ---------------------------------------------------------------------------
# Knowledge Layer
# ---------------------------------------------------------------------------

def _events_knowledge(conn: sqlite3.Connection, spec_id: Optional[int] = None) -> List[TimelineEvent]:
    events: List[TimelineEvent] = []

    # Strategy ideas (system-wide; not filtered by spec_id)
    if not spec_id:
        rows = conn.execute(
            "SELECT idea_id, idea_name, asset_class, symbol, strategy_type, status, created_at"
            " FROM strategy_ideas"
        ).fetchall()
        for r in rows:
            events.append(TimelineEvent(
                event_id=  f"id-{r['idea_id']}",
                timestamp= r["created_at"] or "",
                layer=     "KNOWLEDGE",
                event_type="IDEA_CREATED",
                spec_id=   None,
                spec_name= r["idea_name"],
                summary=   f"Idea: {r['idea_name']}  {r['asset_class']} {r['symbol']}  type={r['strategy_type']}",
                detail={
                    "idea_id":       r["idea_id"],
                    "asset_class":   r["asset_class"],
                    "symbol":        r["symbol"],
                    "strategy_type": r["strategy_type"],
                    "status":        r["status"],
                },
            ))

    # Strategy specs
    q = ("SELECT spec_id, spec_name, asset_class, symbol, timeframe, status, created_at"
         " FROM strategy_specs")
    rows = conn.execute(q + (" WHERE spec_id = ?" if spec_id else ""),
                        (spec_id,) if spec_id else ()).fetchall()
    for r in rows:
        events.append(TimelineEvent(
            event_id=  f"sp-{r['spec_id']}",
            timestamp= r["created_at"] or "",
            layer=     "KNOWLEDGE",
            event_type="SPEC_CREATED",
            spec_id=   r["spec_id"],
            spec_name= r["spec_name"],
            summary=   f"Spec created: {r['spec_name']}  {r['asset_class']} {r['symbol']} {r['timeframe']}",
            detail={
                "spec_id":    r["spec_id"],
                "asset_class":r["asset_class"],
                "symbol":     r["symbol"],
                "timeframe":  r["timeframe"],
                "status":     r["status"],
            },
        ))

    # Approved strategies
    q = ("SELECT approved_strategy_id, spec_id, strategy_name,"
         " approved_by, approval_date, status, created_at FROM approved_strategies")
    rows = conn.execute(q + (" WHERE spec_id = ?" if spec_id else ""),
                        (spec_id,) if spec_id else ()).fetchall()
    for r in rows:
        events.append(TimelineEvent(
            event_id=  f"ap-{r['approved_strategy_id']}",
            timestamp= r["approval_date"] or r["created_at"] or "",
            layer=     "KNOWLEDGE",
            event_type="STRATEGY_APPROVED",
            spec_id=   r["spec_id"],
            spec_name= r["strategy_name"],
            summary=   f"Approved: {r['strategy_name']}  by={r['approved_by']}  status={r['status']}",
            detail={
                "approved_strategy_id": r["approved_strategy_id"],
                "approved_by":          r["approved_by"],
                "approval_date":        r["approval_date"],
                "status":               r["status"],
            },
        ))

    # Rejected strategies
    q = ("SELECT rejected_strategy_id, spec_id, strategy_name,"
         " rejection_stage, rejection_reason, archived_at FROM rejected_strategies")
    rows = conn.execute(q + (" WHERE spec_id = ?" if spec_id else ""),
                        (spec_id,) if spec_id else ()).fetchall()
    for r in rows:
        events.append(TimelineEvent(
            event_id=  f"rj-{r['rejected_strategy_id']}",
            timestamp= r["archived_at"] or "",
            layer=     "KNOWLEDGE",
            event_type="STRATEGY_REJECTED",
            spec_id=   r["spec_id"],
            spec_name= r["strategy_name"],
            summary=   f"Rejected at {r['rejection_stage']}: {r['rejection_reason']}",
            detail={
                "rejected_strategy_id": r["rejected_strategy_id"],
                "rejection_stage":      r["rejection_stage"],
                "rejection_reason":     r["rejection_reason"],
            },
        ))

    return events


# ---------------------------------------------------------------------------
# Review Layer
# ---------------------------------------------------------------------------

def _events_review(spec_id: Optional[int] = None, spec_name: Optional[str] = None) -> List[TimelineEvent]:
    from research.review_journal.review_journal import load_all_journals, load_journal

    events: List[TimelineEvent] = []
    entries = load_journal(spec_name) if spec_name else load_all_journals()

    for e in entries:
        if spec_id and e.spec_id != spec_id:
            continue
        events.append(TimelineEvent(
            event_id=  f"rv-{e.journal_id}",
            timestamp= e.recorded_at,
            layer=     "REVIEW",
            event_type="HUMAN_REVIEW",
            spec_id=   e.spec_id,
            spec_name= e.spec_name,
            summary=   f"Human review: {e.decision}  confidence={e.confidence_level}  reviewer={e.reviewer}",
            detail={
                "journal_id":            e.journal_id,
                "decision":              e.decision,
                "reasoning":             e.reasoning,
                "confidence_level":      e.confidence_level,
                "key_evidence":          e.key_evidence,
                "concerns":              e.concerns,
                "unanswered_questions":  e.unanswered_questions,
                "next_research_actions": e.next_research_actions,
            },
        ))

    return events


# ---------------------------------------------------------------------------
# Governance Layer
# ---------------------------------------------------------------------------

def _events_governance() -> List[TimelineEvent]:
    from research.governance.governance_ledger import load_governance_history

    events: List[TimelineEvent] = []
    for e in load_governance_history():
        phase_label = e.phase or "system-wide"
        events.append(TimelineEvent(
            event_id=  f"gv-{e.ledger_id}",
            timestamp= e.recorded_at,
            layer=     "GOVERNANCE",
            event_type=e.change_type,
            spec_id=   None,
            spec_name= "",
            summary=   f"{e.change_type}  {phase_label}: {e.summary}",
            detail={
                "ledger_id":              e.ledger_id,
                "change_type":            e.change_type,
                "phase":                  e.phase,
                "question":               e.question,
                "authority_impact":       e.authority_impact,
                "review_required_impact": e.review_required_impact,
                "three_question_test":    e.three_question_test,
                "commit":                 e.commit,
            },
        ))

    return events


# ---------------------------------------------------------------------------
# build_timeline
# ---------------------------------------------------------------------------

def build_timeline(
    conn:      sqlite3.Connection,
    spec_id:   Optional[int] = None,
    spec_name: Optional[str] = None,
    layer:     Optional[str] = None,
    since:     Optional[str] = None,
) -> List[TimelineEvent]:
    """Build the unified event timeline across all pipeline layers."""
    events: List[TimelineEvent] = []

    if not layer or layer == "EVIDENCE":
        events.extend(_events_evidence(conn, spec_id))
    if not layer or layer == "KNOWLEDGE":
        events.extend(_events_knowledge(conn, spec_id))
    if not layer or layer == "REVIEW":
        events.extend(_events_review(spec_id, spec_name))
    if (not layer or layer == "GOVERNANCE") and not spec_id:
        events.extend(_events_governance())

    events.sort(key=lambda e: (e.timestamp or "9999", e.layer))

    if since:
        events = [e for e in events if e.timestamp >= since]

    return events


# ---------------------------------------------------------------------------
# load_governance_history  (alias for external callers)
# ---------------------------------------------------------------------------

def load_governance_history() -> List[TimelineEvent]:
    """Return only governance layer events."""
    from research.governance.governance_ledger import load_governance_history as _lgh
    return [
        TimelineEvent(
            event_id=  f"gv-{e.ledger_id}",
            timestamp= e.recorded_at,
            layer=     "GOVERNANCE",
            event_type=e.change_type,
            spec_id=   None,
            spec_name= "",
            summary=   f"{e.change_type}  {e.phase or 'system-wide'}: {e.summary}",
            detail={},
        )
        for e in _lgh()
    ]


# ---------------------------------------------------------------------------
# generate_timeline_report
# ---------------------------------------------------------------------------

def generate_timeline_report(
    conn:        sqlite3.Connection,
    spec_id:     Optional[int]  = None,
    spec_name:   Optional[str]  = None,
    layer:       Optional[str]  = None,
    since:       Optional[str]  = None,
    reports_dir: Optional[Path] = None,
    dry_run:     bool = False,
) -> List[TimelineEvent]:
    """Build timeline and optionally write MD + JSON report to reports/timeline/."""
    events = build_timeline(conn, spec_id=spec_id, spec_name=spec_name,
                            layer=layer, since=since)

    if dry_run or not reports_dir:
        return events

    reports_dir.mkdir(parents=True, exist_ok=True)
    label = f"spec{spec_id}" if spec_id else "all"
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")

    md_lines = [
        "# Event Timeline",
        "",
        f"Generated : {_now()}",
    ]
    if spec_name:
        md_lines.append(f"Strategy  : {spec_name}")
    if layer:
        md_lines.append(f"Layer     : {layer}")
    if since:
        md_lines.append(f"Since     : {since}")
    md_lines += ["", f"{len(events)} events", ""]

    for e in events:
        date = e.timestamp[:10] if e.timestamp else "----"
        md_lines.append(f"**[{date}] {e.layer} / {e.event_type}**")
        if e.spec_name:
            md_lines.append(f"Strategy: {e.spec_name}")
        md_lines.append(e.summary)
        md_lines.append("")

    (reports_dir / f"timeline_{label}_{ts}.md").write_text(
        "\n".join(md_lines), encoding="utf-8"
    )
    (reports_dir / f"timeline_{label}_{ts}.json").write_text(
        json.dumps([asdict(e) for e in events], indent=2), encoding="utf-8"
    )

    return events


# ---------------------------------------------------------------------------
# Console display
# ---------------------------------------------------------------------------

def _print_timeline(events: List[TimelineEvent]) -> None:
    if not events:
        print("\n  No events found.")
        return

    current_date = ""
    for e in events:
        date = e.timestamp[:10] if e.timestamp else "----"
        if date != current_date:
            print(f"\n  {date}")
            current_date = date
        name = f"  [{e.spec_name}]" if e.spec_name else "  [system]"
        print(f"    {e.layer:<12} {e.event_type:<24}{name}")
        print(f"               {e.summary}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Event Timeline -- unified chronological history across all pipeline layers"
    )

    parser.add_argument("--spec-id",   type=int, default=None,
                        help="Filter to one strategy by spec ID")
    parser.add_argument("--spec-name", default="",
                        help="Filter to one strategy by name")
    parser.add_argument("--layer",     default="",
                        choices=list(VALID_LAYERS) + [""],
                        help="Filter by layer (EVIDENCE/KNOWLEDGE/REVIEW/GOVERNANCE)")
    parser.add_argument("--since",     default="",
                        help="Show events on or after this date (YYYY-MM-DD)")
    parser.add_argument("--report",    action="store_true",
                        help="Write MD + JSON report to reports/timeline/")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Print to console; do not write report files")
    parser.add_argument("--db",        default="",
                        help="Path to database file")

    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _DB_DEFAULT
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _open_db(db_path)

    try:
        if args.report:
            reports_dir = _PROJECT_ROOT / "reports" / "timeline"
            events = generate_timeline_report(
                conn,
                spec_id=    args.spec_id,
                spec_name=  args.spec_name or None,
                layer=      args.layer or None,
                since=      args.since or None,
                reports_dir=reports_dir,
                dry_run=    args.dry_run,
            )
            label = f" for {args.spec_name}" if args.spec_name else ""
            print(f"\nTimeline{label}: {len(events)} events")
            if not args.dry_run:
                print(f"Report written to: {reports_dir}/")
        else:
            events = build_timeline(
                conn,
                spec_id=   args.spec_id,
                spec_name= args.spec_name or None,
                layer=     args.layer or None,
                since=     args.since or None,
            )
            layer_label = f" [{args.layer}]" if args.layer else ""
            name_label  = f" -- {args.spec_name}" if args.spec_name else ""
            print(f"\nEvent Timeline{name_label}{layer_label}  ({len(events)} events)")
            _print_timeline(events)
            print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
