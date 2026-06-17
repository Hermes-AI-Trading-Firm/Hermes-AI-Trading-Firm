#!/usr/bin/env python3
"""
Learning Review Workspace -- research/review/review_workspace.py

Phase 39

Question: "Given everything we know from all prior strategies,
           what does that tell us about the one in front of us now?"

The Three-Question Test:
  Can it produce better evidence?   YES -- synthesizes accumulated learning into one place
  Can it ask a better question?     YES -- surfaces patterns a reviewer might miss
  Does a human still decide?        YES -- workspace informs; never concludes

Assembles all thirteen decision-support sections into one workspace document
at the REVIEW_REQUIRED gate. The final status is always REVIEW_REQUIRED.
The pipeline cannot change it.

What it does NOT do
-------------------
- Does not approve or reject strategies
- Does not change strategy state
- Does not write to the database
- Does not advance any strategy past REVIEW_REQUIRED

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
    python -m research.review.review_workspace --spec-id 1
    python -m research.review.review_workspace --spec-id 1 --dry-run
    python -m research.review.review_workspace --spec-id 1 --notes "Initial review pass"
    python -m research.review.review_workspace --spec-id 1 --section learning
    python -m research.review.review_workspace --spec-id 1 --section questions
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB   = _PROJECT_ROOT / "database" / "hermes_research.db"
REPORTS_DIR  = _PROJECT_ROOT / "reports"  / "review"

CLASSIFICATION_PATH = _PROJECT_ROOT / "research" / "archetype" / "classifications.json"
PATTERN_LIB_PATH    = _PROJECT_ROOT / "research" / "memory"    / "pattern_library.json"

MAX_AUTOMATED_STATE = "REVIEW_REQUIRED"
FINAL_STATUS        = "REVIEW_REQUIRED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe(name: str) -> str:
    import re
    return re.sub(r"[^\w\-]", "_", name)


def _trunc(text: Any, n: int = 120) -> str:
    if not text:
        return ""
    s = str(text)
    return s[:n] + "..." if len(s) > n else s


def _load_json(path: Path) -> Dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WorkspaceSection:
    number:  int
    title:   str
    status:  str              # OK / WARN / MISSING / N/A
    content: Dict[str, Any]   # section-specific payload
    notes:   str = ""


@dataclass
class ReviewWorkspace:
    spec_id:      int
    spec_name:    str
    sections:     List[WorkspaceSection] = field(default_factory=list)
    human_notes:  str = ""
    final_status: str = FINAL_STATUS
    generated_at: str = field(default_factory=_now)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fetch_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT spec_id, spec_name, asset_class, symbol, timeframe,
               why_edge_exists, status, created_at, updated_at
        FROM strategy_specs WHERE spec_id = ?
    """, (spec_id,)).fetchone()
    if not row:
        return None
    cols = ["spec_id", "spec_name", "asset_class", "symbol", "timeframe",
            "description", "status", "created_at", "updated_at"]
    return dict(zip(cols, row))


def _latest_score(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT composite_score, grade, recommendation,
               walk_forward_pass, monte_carlo_pass,
               walk_forward_score, monte_carlo_score,
               overfitting_risk, profitability_score,
               drawdown_score, consistency_score, regime_score,
               robustness_score, scored_at
        FROM scoring_results WHERE spec_id = ?
        AND scoring_id = (SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = ?)
    """, (spec_id, spec_id)).fetchone()
    if not row:
        return None
    cols = ["composite_score", "grade", "recommendation",
            "walk_forward_pass", "monte_carlo_pass",
            "walk_forward_score", "monte_carlo_score",
            "overfitting_risk", "profitability_score",
            "drawdown_score", "consistency_score", "regime_score",
            "robustness_score", "scored_at"]
    return dict(zip(cols, row))


def _latest_backtest(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT backtest_id, is_in_sample, total_trades, profit_factor,
               sharpe_ratio, max_drawdown_pct, win_rate, net_profit,
               data_start_date, data_end_date, created_at
        FROM backtests WHERE spec_id = ? AND is_in_sample = 1
        ORDER BY backtest_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    cols = ["backtest_id", "is_in_sample", "total_trades", "profit_factor",
            "sharpe_ratio", "max_drawdown_pct", "win_rate", "net_profit",
            "data_start_date", "data_end_date", "created_at"]
    return dict(zip(cols, row))


def _oos_backtests(conn: sqlite3.Connection, spec_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute("""
        SELECT backtest_id, total_trades, profit_factor, sharpe_ratio,
               max_drawdown_pct, win_rate, data_start_date, data_end_date
        FROM backtests WHERE spec_id = ? AND is_in_sample = 0
        ORDER BY backtest_id DESC
    """, (spec_id,)).fetchall()
    cols = ["backtest_id", "total_trades", "profit_factor", "sharpe_ratio",
            "max_drawdown_pct", "win_rate", "data_start_date", "data_end_date"]
    return [dict(zip(cols, r)) for r in rows]


def _regime_rows(conn: sqlite3.Connection, spec_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute("""
        SELECT regime_model, status, best_regime, worst_regime,
               conclusion, regime_filter_recommended, created_at
        FROM regime_analysis WHERE spec_id = ? ORDER BY created_at DESC
    """, (spec_id,)).fetchall()
    cols = ["regime_model", "status", "best_regime", "worst_regime",
            "conclusion", "regime_filter_recommended", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _s1_strategy_summary(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    spec = _fetch_spec(conn, spec_id)
    if not spec:
        return WorkspaceSection(1, "Strategy Summary", "MISSING",
                                {"error": f"spec_id={spec_id} not found"})
    bt = _latest_backtest(conn, spec_id)
    sr = _latest_score(conn, spec_id)
    return WorkspaceSection(
        number=1, title="Strategy Summary", status="OK",
        content={
            "spec_id":     spec["spec_id"],
            "spec_name":   spec["spec_name"],
            "asset_class": spec.get("asset_class", ""),
            "symbol":      spec.get("symbol", ""),
            "timeframe":   spec.get("timeframe", ""),
            "description": spec.get("description", ""),
            "lifecycle_status": spec.get("status", ""),
            "created_at":  spec.get("created_at", ""),
            "has_backtest": bt is not None,
            "has_scoring":  sr is not None,
            "composite_score": sr["composite_score"] if sr else None,
            "grade":           sr["grade"] if sr else None,
            "recommendation":  sr["recommendation"] if sr else None,
        }
    )


def _s2_evidence_quality(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    try:
        from research.traceability.trace_engine import trace_evidence_quality  # type: ignore
        eq = trace_evidence_quality(conn, spec_id)
        status = "OK" if eq.overall == "STRONG" else (
                 "WARN" if eq.overall in ("MODERATE", "WEAK") else "MISSING")
        return WorkspaceSection(
            number=2, title="Evidence Quality", status=status,
            content={
                "overall":      eq.overall,
                "grade_reason": eq.grade_reason,
                "cells": [
                    {"column": c.column, "value": c.value,
                     "source": c.source, "detail": c.detail}
                    for c in eq.cells
                ],
            }
        )
    except Exception as exc:
        return WorkspaceSection(2, "Evidence Quality", "MISSING",
                                {"error": str(exc)})


def _s3_audit_summary(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    try:
        from research.audit.strategy_auditor import audit_spec  # type: ignore
        report = audit_spec(conn, spec_id)
        if report is None:
            return WorkspaceSection(3, "Audit Summary", "MISSING",
                                    {"error": "Spec not found"})
        status = "MISSING" if report.fail_count > 0 else (
                 "WARN"    if report.warn_count > 0 else "OK")
        fails = [{"check": c.check, "detail": c.detail}
                 for c in report.checks if c.status == "FAIL"]
        warns = [{"check": c.check, "detail": c.detail}
                 for c in report.checks if c.status == "WARN"]
        return WorkspaceSection(
            number=3, title="Audit Summary", status=status,
            content={
                "pass_count":     report.pass_count,
                "warn_count":     report.warn_count,
                "fail_count":     report.fail_count,
                "recommendation": report.recommendation,
                "fail_findings":  fails,
                "warn_findings":  warns,
            }
        )
    except Exception as exc:
        return WorkspaceSection(3, "Audit Summary", "MISSING",
                                {"error": str(exc)})


def _s4_walk_forward_summary(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    sr  = _latest_score(conn, spec_id)
    oos = _oos_backtests(conn, spec_id)
    if not sr:
        return WorkspaceSection(4, "Walk-Forward Summary", "MISSING",
                                {"detail": "No scoring result found"})
    wf_present = (sr.get("walk_forward_score") or 0) > 0
    if not wf_present:
        return WorkspaceSection(4, "Walk-Forward Summary", "MISSING",
                                {"detail": "Walk-forward not included in scoring run"})
    status = "OK" if sr.get("walk_forward_pass") == 1 else "WARN"
    return WorkspaceSection(
        number=4, title="Walk-Forward Summary", status=status,
        content={
            "walk_forward_pass":  sr.get("walk_forward_pass") == 1,
            "walk_forward_score": sr.get("walk_forward_score"),
            "oos_backtest_count": len(oos),
            "oos_backtests": [
                {"trades": b["total_trades"], "pf": b["profit_factor"],
                 "mdd_pct": b["max_drawdown_pct"],
                 "period": f"{b['data_start_date']} to {b['data_end_date']}"}
                for b in oos
            ],
        }
    )


def _s5_monte_carlo_summary(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    sr = _latest_score(conn, spec_id)
    if not sr:
        return WorkspaceSection(5, "Monte Carlo Summary", "MISSING",
                                {"detail": "No scoring result found"})
    mc_present = (sr.get("monte_carlo_score") or 0) > 0
    if not mc_present:
        return WorkspaceSection(5, "Monte Carlo Summary", "MISSING",
                                {"detail": "Monte Carlo not included in scoring run"})
    status = "OK" if sr.get("monte_carlo_pass") == 1 else "WARN"
    return WorkspaceSection(
        number=5, title="Monte Carlo Summary", status=status,
        content={
            "monte_carlo_pass":  sr.get("monte_carlo_pass") == 1,
            "monte_carlo_score": sr.get("monte_carlo_score"),
            "overfitting_risk":  sr.get("overfitting_risk"),
        }
    )


def _s6_regime_summary(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    rows = _regime_rows(conn, spec_id)
    completed = [r for r in rows if r["status"] == "completed"]
    if not completed:
        return WorkspaceSection(6, "Regime Summary", "MISSING",
                                {"detail": "No completed regime analysis found"})
    latest = completed[0]
    return WorkspaceSection(
        number=6, title="Regime Summary", status="OK",
        content={
            "analysis_count":           len(completed),
            "model":                    latest["regime_model"],
            "best_regime":              latest["best_regime"],
            "worst_regime":             latest["worst_regime"],
            "regime_filter_recommended": bool(latest["regime_filter_recommended"]),
            "conclusion":               _trunc(latest["conclusion"]),
        }
    )


def _s7_learning_review(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    """Synthesize learning from all prior strategies and the pattern library."""
    spec = _fetch_spec(conn, spec_id)
    if not spec:
        return WorkspaceSection(7, "Learning Review Summary", "MISSING",
                                {"error": "Spec not found"})

    spec_name = spec["spec_name"]
    content: Dict[str, Any] = {}

    # Archetype classification
    classifications = _load_json(CLASSIFICATION_PATH)
    arch = (classifications.get("classifications", {})
            .get(str(spec_id), {}))
    content["archetype"] = {
        "id":    arch.get("archetype_id", ""),
        "label": arch.get("archetype_label", ""),
        "known_weaknesses": arch.get("known_weaknesses", []),
    }

    # Pattern library: what does the pipeline know about this strategy?
    pat_lib = _load_json(PATTERN_LIB_PATH)
    pat_rec = (pat_lib.get("strategy_records", {}).get(str(spec_id))
               or pat_lib.get("strategy_records", {}).get(spec_id) or {})
    content["pattern_library"] = {
        "record_found":     bool(pat_rec),
        "summary":          _trunc(pat_rec.get("summary", "No pattern library entry")),
        "known_strengths":  pat_rec.get("strengths", []),
        "known_weaknesses": pat_rec.get("weaknesses", []),
        "last_updated":     pat_rec.get("updated_at", ""),
    }

    # Outcome history: what happened when similar questions were investigated?
    try:
        from research.outcomes.outcome_tracker import (  # type: ignore
            load_all_outcomes, identify_patterns
        )
        all_outcomes = load_all_outcomes()
        patterns = identify_patterns(all_outcomes)

        # Open questions for this spec
        try:
            from research.questions.question_engine import (  # type: ignore
                collect_question_context, identify_unknowns,
                _all_scored_specs,
            )
            spec_rows = _all_scored_specs(conn)
            spec_row  = next((r for r in spec_rows if r["spec_id"] == spec_id), None)
            open_q_ids: List[str] = []
            if spec_row:
                cls = _load_json(CLASSIFICATION_PATH)
                pl  = _load_json(PATTERN_LIB_PATH)
                ctx = collect_question_context(conn, spec_row, cls, pl)
                open_q_ids = [q.question_id for q in identify_unknowns(ctx)]
        except Exception:
            open_q_ids = []

        # Overlay advancement rates for this spec's open questions
        pat_map = {p.question_id: p for p in patterns}
        relevant_patterns = []
        for qid in open_q_ids:
            p = pat_map.get(qid)
            if p:
                relevant_patterns.append({
                    "question_id":      p.question_id,
                    "times_answered":   p.times_answered,
                    "advancement_rate": round(p.advancement_rate, 2),
                    "example_finding":  _trunc(p.example_finding, 100),
                })

        content["outcome_patterns"] = {
            "total_outcomes_in_library": len(all_outcomes),
            "total_pattern_types":       len(patterns),
            "patterns_for_open_questions": relevant_patterns,
            "note": ("Historical advancement rates for this spec's open questions. "
                     "Patterns inform suggestions only."),
        }
    except Exception as exc:
        content["outcome_patterns"] = {"error": str(exc)}

    # Cross-strategy insight: how many strategies share this archetype?
    if arch.get("archetype_id"):
        try:
            arch_id = arch["archetype_id"]
            all_cls = classifications.get("classifications", {})
            peers = [
                v.get("spec_name", k) for k, v in all_cls.items()
                if v.get("archetype_id") == arch_id and str(k) != str(spec_id)
            ]
            content["archetype_peers"] = {
                "archetype_id": arch_id,
                "peer_count":   len(peers),
                "peers":        peers[:10],
            }
        except Exception:
            content["archetype_peers"] = {}
    else:
        content["archetype_peers"] = {"note": "No archetype classification found"}

    status = "OK" if (pat_rec or arch) else "MISSING"
    return WorkspaceSection(7, "Learning Review Summary", status, content)


def _s8_open_questions(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    try:
        from research.questions.question_engine import (  # type: ignore
            collect_question_context, identify_unknowns, _all_scored_specs,
        )
        spec_rows = _all_scored_specs(conn)
        spec_row  = next((r for r in spec_rows if r["spec_id"] == spec_id), None)
        if not spec_row:
            return WorkspaceSection(8, "Open Research Questions", "MISSING",
                                    {"detail": "Spec not in scored set"})
        cls = _load_json(CLASSIFICATION_PATH)
        pl  = _load_json(PATTERN_LIB_PATH)
        ctx = collect_question_context(conn, spec_row, cls, pl)
        qs  = identify_unknowns(ctx)
        blocking = [q for q in qs if q.affects_review_required]
        status = "WARN" if blocking else ("OK" if qs else "OK")
        return WorkspaceSection(
            number=8, title="Open Research Questions", status=status,
            content={
                "total_open":     len(qs),
                "blocking_count": len(blocking),
                "questions": [
                    {
                        "question_id":           q.question_id,
                        "category":              q.category,
                        "priority":              q.priority,
                        "affects_review_required": q.affects_review_required,
                        "question":              _trunc(q.question, 200),
                        "why_it_matters":        _trunc(q.why_it_matters, 150),
                        "suggested_action":      _trunc(q.suggested_action, 150),
                    }
                    for q in sorted(qs,
                        key=lambda x: (0 if x.affects_review_required else 1,
                                       {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(x.priority, 3)))
                ],
            }
        )
    except Exception as exc:
        return WorkspaceSection(8, "Open Research Questions", "MISSING",
                                {"error": str(exc)})


def _s9_research_priorities(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    try:
        from research.priorities.priority_engine import rank_research_priorities  # type: ignore
        report   = rank_research_priorities(conn)
        relevant = [p for p in report.priorities if spec_id in (p.affected_spec_ids or [])]
        if not relevant:
            return WorkspaceSection(9, "Research Priorities", "OK",
                                    {"detail": "No open research priorities for this spec"})
        top = relevant[0]
        return WorkspaceSection(
            number=9, title="Research Priorities", status="OK",
            content={
                "top_level":    top.level,
                "top_value":    top.research_value,
                "count":        len(relevant),
                "priorities": [
                    {
                        "question_type":          p.question_type,
                        "level":                  p.level,
                        "research_value":         p.research_value,
                        "affects_review_required": p.affects_review_required,
                        "effort":                 p.effort,
                        "suggested_action":       _trunc(p.suggested_action, 150),
                    }
                    for p in relevant
                ],
            }
        )
    except Exception as exc:
        return WorkspaceSection(9, "Research Priorities", "MISSING",
                                {"error": str(exc)})


def _s10_traceability_links(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    try:
        from research.traceability.trace_engine import (  # type: ignore
            build_evidence_chain, trace_decision_package
        )
        chain = build_evidence_chain(conn, spec_id)
        pkg   = trace_decision_package(conn, spec_id)
        return WorkspaceSection(
            number=10, title="Traceability Links", status="OK",
            content={
                "current_state":       chain.current_state,
                "evidence_event_count": len(chain.events),
                "evidence_events": [
                    {"timestamp": e.timestamp[:16].replace("T", " "),
                     "event_type": e.event_type, "summary": e.summary}
                    for e in chain.events
                ],
                "decision_package_ready": pkg.ready_for_review,
                "blocking_gaps":          pkg.blocking_gaps,
            }
        )
    except Exception as exc:
        return WorkspaceSection(10, "Traceability Links", "MISSING",
                                {"error": str(exc)})


def _s11_outcome_history(conn: sqlite3.Connection, spec_id: int) -> WorkspaceSection:
    spec = _fetch_spec(conn, spec_id)
    spec_name = spec["spec_name"] if spec else f"spec_{spec_id}"
    try:
        from research.outcomes.outcome_tracker import load_outcomes  # type: ignore
        outcomes = load_outcomes(spec_name)
        advanced = sum(1 for o in outcomes if o.advanced)
        blocked  = sum(1 for o in outcomes if o.new_blockers)
        return WorkspaceSection(
            number=11, title="Outcome History", status="OK",
            content={
                "total_outcomes":  len(outcomes),
                "times_advanced":  advanced,
                "times_blocked":   blocked,
                "outcomes": [
                    {
                        "recorded_at":     o.recorded_at[:10],
                        "question_id":     o.question_id,
                        "action_taken":    _trunc(o.action_taken, 100),
                        "finding":         _trunc(o.finding, 150),
                        "advanced":        o.advanced,
                        "lifecycle_before": o.lifecycle_before,
                        "lifecycle_after":  o.lifecycle_after,
                    }
                    for o in outcomes
                ],
            }
        )
    except Exception as exc:
        return WorkspaceSection(11, "Outcome History", "OK",
                                {"total_outcomes": 0, "outcomes": [],
                                 "note": str(exc)})


def _s12_human_review_notes(notes: str) -> WorkspaceSection:
    return WorkspaceSection(
        number=12, title="Human Review Notes", status="N/A",
        content={
            "notes": notes or "",
            "instruction": (
                "This section is for the reviewing human. "
                "Pass --notes 'your observations' when generating the workspace, "
                "or edit this section directly in the output file."
            ),
        }
    )


def _s13_final_status() -> WorkspaceSection:
    return WorkspaceSection(
        number=13, title="Final Status", status=FINAL_STATUS,
        content={
            "status":  FINAL_STATUS,
            "meaning": "The pipeline has produced all available evidence and advisory output. "
                       "The decision belongs to the human reviewer. "
                       "This status cannot be changed by any automated process.",
        }
    )


# ---------------------------------------------------------------------------
# generate_workspace
# ---------------------------------------------------------------------------

def generate_workspace(
    conn:        sqlite3.Connection,
    spec_id:     int,
    notes:       str = "",
    reports_dir: Path = REPORTS_DIR,
    dry_run:     bool = False,
) -> Tuple[Optional[Path], Optional[Path]]:
    """Assemble all thirteen sections and write the review workspace."""
    spec = _fetch_spec(conn, spec_id)
    spec_name = spec["spec_name"] if spec else f"spec_{spec_id}"

    sections = [
        _s1_strategy_summary(conn, spec_id),
        _s2_evidence_quality(conn, spec_id),
        _s3_audit_summary(conn, spec_id),
        _s4_walk_forward_summary(conn, spec_id),
        _s5_monte_carlo_summary(conn, spec_id),
        _s6_regime_summary(conn, spec_id),
        _s7_learning_review(conn, spec_id),
        _s8_open_questions(conn, spec_id),
        _s9_research_priorities(conn, spec_id),
        _s10_traceability_links(conn, spec_id),
        _s11_outcome_history(conn, spec_id),
        _s12_human_review_notes(notes),
        _s13_final_status(),
    ]

    workspace = ReviewWorkspace(
        spec_id=spec_id,
        spec_name=spec_name,
        sections=sections,
        human_notes=notes,
    )

    md  = _render_markdown(workspace)
    obj = _to_dict(workspace)

    if dry_run:
        print(md)
        return None, None

    reports_dir.mkdir(parents=True, exist_ok=True)
    date      = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem      = f"{_safe(spec_name)}_review_workspace_{date}"
    md_path   = reports_dir / f"{stem}.md"
    json_path = reports_dir / f"{stem}.json"

    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    return md_path, json_path


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _to_dict(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_dict(i) for i in obj]
    return obj


def _render_markdown(ws: ReviewWorkspace) -> str:
    lines = []

    lines.append(f"# Learning Review Workspace: {ws.spec_name}")
    lines.append(f"\nGenerated: {ws.generated_at}")
    lines.append(f"\nFinal Status: **{ws.final_status}**")
    lines.append("\n---\n")

    for sec in ws.sections:
        lines.append(f"## {sec.number}. {sec.title}")
        lines.append(f"\n**Status: {sec.status}**\n")

        c = sec.content

        if sec.number == 1:
            lines.append(f"- Strategy: `{c.get('spec_name', '')}`")
            lines.append(f"- Asset class: {c.get('asset_class', '')} / {c.get('symbol', '')}")
            lines.append(f"- Timeframe: {c.get('timeframe', '')}")
            lines.append(f"- Lifecycle status: {c.get('lifecycle_status', '')}")
            if c.get("grade"):
                lines.append(f"- Score: {c.get('composite_score', '')} grade={c.get('grade', '')} rec={c.get('recommendation', '')}")
            if c.get("description"):
                lines.append(f"\n{c['description']}")

        elif sec.number == 2:
            lines.append(f"Overall: **{c.get('overall', '--')}**")
            lines.append(f"\nReason: {c.get('grade_reason', '')}\n")
            lines.append("| Column | Value | Detail |")
            lines.append("|--------|-------|--------|")
            for cell in c.get("cells", []):
                lines.append(f"| {cell['column']} | {cell['value']} | {cell['detail']} |")

        elif sec.number == 3:
            lines.append(f"- Pass: {c.get('pass_count', 0)}  Warn: {c.get('warn_count', 0)}  Fail: {c.get('fail_count', 0)}")
            lines.append(f"- Recommendation: {c.get('recommendation', '')}")
            for f in c.get("fail_findings", []):
                lines.append(f"\n  [FAIL] {f['check']}: {f['detail']}")
            for w in c.get("warn_findings", []):
                lines.append(f"\n  [WARN] {w['check']}: {w['detail']}")

        elif sec.number == 4:
            passed = c.get("walk_forward_pass", False)
            lines.append(f"- Result: {'PASS' if passed else 'FAIL'}")
            lines.append(f"- WF score: {c.get('walk_forward_score', '--')}")
            lines.append(f"- OOS backtests: {c.get('oos_backtest_count', 0)}")
            for b in c.get("oos_backtests", []):
                lines.append(f"  - {b['period']}: {b['trades']} trades PF={b['pf']} MDD={b['mdd_pct']}%")

        elif sec.number == 5:
            passed = c.get("monte_carlo_pass", False)
            lines.append(f"- Result: {'PASS' if passed else 'FAIL'}")
            lines.append(f"- MC score: {c.get('monte_carlo_score', '--')}")
            lines.append(f"- Overfitting risk: {c.get('overfitting_risk', '--')}")

        elif sec.number == 6:
            lines.append(f"- Model: {c.get('model', '--')}")
            lines.append(f"- Best regime: {c.get('best_regime', '--')}")
            lines.append(f"- Worst regime: {c.get('worst_regime', '--')}")
            lines.append(f"- Filter recommended: {c.get('regime_filter_recommended', '--')}")
            if c.get("conclusion"):
                lines.append(f"\n{c['conclusion']}")

        elif sec.number == 7:
            arch = c.get("archetype", {})
            if arch.get("label"):
                lines.append(f"- Archetype: {arch['label']}")
            if arch.get("known_weaknesses"):
                lines.append(f"- Known weaknesses: {', '.join(arch['known_weaknesses'])}")
            pl = c.get("pattern_library", {})
            lines.append(f"\n**Pattern Library:** {pl.get('summary', 'No entry')}")
            peers = c.get("archetype_peers", {})
            if peers.get("peer_count"):
                lines.append(f"\nArchetype peers: {peers['peer_count']} strategy/ies share this archetype")
            patterns = c.get("outcome_patterns", {}).get("patterns_for_open_questions", [])
            if patterns:
                lines.append("\n**Historical advancement rates for open questions:**\n")
                lines.append("| Question | Times Answered | Advancement Rate |")
                lines.append("|----------|---------------|-----------------|")
                for p in patterns:
                    lines.append(f"| {p['question_id']} | {p['times_answered']} | {p['advancement_rate']:.0%} |")

        elif sec.number == 8:
            lines.append(f"- Total open: {c.get('total_open', 0)}")
            lines.append(f"- Blocking (affect REVIEW_REQUIRED): {c.get('blocking_count', 0)}")
            if c.get("questions"):
                lines.append("")
                for q in c["questions"]:
                    flag = " [BLOCKING]" if q.get("affects_review_required") else ""
                    lines.append(f"- **{q['question_id']}** ({q['priority']}){flag}")
                    lines.append(f"  {q['question']}")
                    lines.append(f"  Action: {q['suggested_action']}")

        elif sec.number == 9:
            lines.append(f"- Top priority level: {c.get('top_level', '--')}")
            lines.append(f"- Research value: {c.get('top_value', '--')}")
            for p in c.get("priorities", []):
                flag = " *" if p.get("affects_review_required") else ""
                lines.append(f"  - {p['level']:<8} {p['question_type']}{flag}  value={p['research_value']:.1f}  effort={p['effort']}")

        elif sec.number == 10:
            lines.append(f"- Current pipeline state: `{c.get('current_state', '--')}`")
            lines.append(f"- Evidence events: {c.get('evidence_event_count', 0)}")
            lines.append(f"- Decision package ready: {c.get('decision_package_ready', False)}")
            for b in c.get("blocking_gaps", []):
                lines.append(f"  - [BLOCKING] {b}")
            if c.get("evidence_events"):
                lines.append("")
                for ev in c["evidence_events"]:
                    lines.append(f"  `{ev['timestamp']}` [{ev['event_type']}] {ev['summary']}")

        elif sec.number == 11:
            lines.append(f"- Total outcomes recorded: {c.get('total_outcomes', 0)}")
            lines.append(f"- Times advanced: {c.get('times_advanced', 0)}")
            lines.append(f"- Times new blockers found: {c.get('times_blocked', 0)}")
            for o in c.get("outcomes", []):
                adv = "advanced" if o.get("advanced") else ("blocked" if o.get("advanced") is False else "unknown")
                lines.append(f"  - [{o['recorded_at']}] {o['question_id']}: {o['finding']} ({adv})")

        elif sec.number == 12:
            notes_text = c.get("notes", "")
            if notes_text:
                lines.append(notes_text)
            else:
                lines.append("*(No notes provided. Pass --notes \"your observations\" to populate this section.)*")

        elif sec.number == 13:
            lines.append(f"**{c.get('status', FINAL_STATUS)}**")
            lines.append(f"\n{c.get('meaning', '')}")

        if c.get("error"):
            lines.append(f"\n*(Section unavailable: {c['error']})*")
        if c.get("detail") and sec.status == "MISSING":
            lines.append(f"\n*(Not available: {c['detail']})*")

        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("REVIEW_REQUIRED. The pipeline stops here. Human authority begins.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Console display
# ---------------------------------------------------------------------------

def _print_workspace(ws: ReviewWorkspace) -> None:
    print(f"\nLearning Review Workspace: {ws.spec_name}")
    print(f"Generated: {ws.generated_at}")
    print(f"Final status: {ws.final_status}\n")
    for sec in ws.sections:
        print(f"  [{sec.status:<8}] {sec.number:>2}. {sec.title}")
    print()


def _print_section(ws: ReviewWorkspace, section_name: str) -> None:
    name_map = {
        "summary":    1, "evidence":   2, "audit":      3,
        "wf":         4, "mc":         5, "regime":     6,
        "learning":   7, "questions":  8, "priorities": 9,
        "trace":      10, "outcomes":  11, "notes":     12,
        "status":     13,
    }
    target = name_map.get(section_name.lower())
    if target is None:
        print(f"Unknown section: {section_name}")
        print(f"Valid: {', '.join(name_map.keys())}")
        return
    for sec in ws.sections:
        if sec.number == target:
            print(f"\n## {sec.number}. {sec.title}  [{sec.status}]")
            print(json.dumps(sec.content, indent=2, default=str))
            return


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _open_db(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def _build_workspace(conn: sqlite3.Connection, spec_id: int, notes: str) -> ReviewWorkspace:
    spec = _fetch_spec(conn, spec_id)
    spec_name = spec["spec_name"] if spec else f"spec_{spec_id}"
    sections = [
        _s1_strategy_summary(conn, spec_id),
        _s2_evidence_quality(conn, spec_id),
        _s3_audit_summary(conn, spec_id),
        _s4_walk_forward_summary(conn, spec_id),
        _s5_monte_carlo_summary(conn, spec_id),
        _s6_regime_summary(conn, spec_id),
        _s7_learning_review(conn, spec_id),
        _s8_open_questions(conn, spec_id),
        _s9_research_priorities(conn, spec_id),
        _s10_traceability_links(conn, spec_id),
        _s11_outcome_history(conn, spec_id),
        _s12_human_review_notes(notes),
        _s13_final_status(),
    ]
    return ReviewWorkspace(
        spec_id=spec_id,
        spec_name=spec_name,
        sections=sections,
        human_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Learning Review Workspace -- decision support at REVIEW_REQUIRED"
    )
    parser.add_argument("--spec-id",  type=int, required=True,
                        help="Strategy spec ID to review")
    parser.add_argument("--notes",    default="",
                        help="Human reviewer notes to include in section 12")
    parser.add_argument("--section",  metavar="NAME",
                        help="Print one section only (summary/evidence/audit/wf/mc/"
                             "regime/learning/questions/priorities/trace/outcomes/notes/status)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print full workspace to console; do not write files")
    parser.add_argument("--db",       default=str(DEFAULT_DB),
                        help="Path to SQLite database")
    args = parser.parse_args()

    try:
        conn = _open_db(Path(args.db))
    except Exception as exc:
        print(f"Cannot open database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        spec_id = args.spec_id

        if args.dry_run:
            generate_workspace(conn, spec_id, notes=args.notes, dry_run=True)
            return

        if args.section:
            ws = _build_workspace(conn, spec_id, args.notes)
            _print_section(ws, args.section)
            return

        # Default: print workspace summary + write report
        ws = _build_workspace(conn, spec_id, args.notes)
        _print_workspace(ws)

        md_path, json_path = generate_workspace(
            conn, spec_id, notes=args.notes, dry_run=False
        )
        if md_path:
            print(f"Workspace written:")
            print(f"  {md_path}")
            print(f"  {json_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
