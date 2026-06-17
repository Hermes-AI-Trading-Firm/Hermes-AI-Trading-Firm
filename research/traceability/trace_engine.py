#!/usr/bin/env python3
"""
Belief Provenance Engine -- research/traceability/trace_engine.py

Phase 38

Question: "Why do we believe what we believe?"

The Three-Question Test:
  Can it produce better evidence?   YES -- shows which evidence is load-bearing
  Can it ask a better question?     YES -- exposes gaps in the belief chain
  Does a human still decide?        YES -- traces only; never recommends approval

Traces any current pipeline state back to the evidence that produced it.
A vertical read through all eight pipeline layers for a single strategy.

What it does NOT do
-------------------
- Does not change strategy state
- Does not write to the database
- Does not approve or reject strategies
- Does not advance any strategy past REVIEW_REQUIRED
- Does not answer its own questions

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
    python -m research.traceability.trace_engine --spec-id 1
    python -m research.traceability.trace_engine --spec-id 1 --chain
    python -m research.traceability.trace_engine --spec-id 1 --quality
    python -m research.traceability.trace_engine --spec-id 1 --priority
    python -m research.traceability.trace_engine --spec-id 1 --question mc_missing
    python -m research.traceability.trace_engine --spec-id 1 --decision-package
    python -m research.traceability.trace_engine --spec-id 1 --report
    python -m research.traceability.trace_engine --spec-id 1 --dry-run
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
REPORTS_DIR  = _PROJECT_ROOT / "reports"  / "traceability"

MAX_AUTOMATED_STATE = "REVIEW_REQUIRED"

_MIN_TRADES_STRONG = 30
_MIN_TRADES_BT     = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe(name: str) -> str:
    import re
    return re.sub(r"[^\w\-]", "_", name)


def _trunc(text: str, n: int = 100) -> str:
    if not text:
        return ""
    text = str(text)
    return text[:n] + "..." if len(text) > n else text


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EvidenceEvent:
    timestamp:  str
    event_type: str
    source:     str
    summary:    str
    detail:     Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceChain:
    spec_id:       int
    spec_name:     str
    current_state: str
    events:        List[EvidenceEvent] = field(default_factory=list)
    generated_at:  str = field(default_factory=_now)


@dataclass
class CellTrace:
    column: str
    value:  str   # OK / WEAK / --
    source: str
    detail: str


@dataclass
class EvidenceQualityTrace:
    spec_id:      int
    spec_name:    str
    cells:        List[CellTrace] = field(default_factory=list)
    overall:      str = "--"
    grade_reason: str = ""
    generated_at: str = field(default_factory=_now)


@dataclass
class PriorityTrace:
    spec_id:            int
    spec_name:          str
    open_questions:     List[Dict[str, Any]] = field(default_factory=list)
    priority_level:     str = "LOW"
    research_value:     float = 0.0
    blocking_questions: List[str] = field(default_factory=list)
    derived_from:       str = ""
    generated_at:       str = field(default_factory=_now)


@dataclass
class QuestionTrace:
    spec_id:            int
    spec_name:          str
    question_id:        str
    category:           str = ""
    generated_reason:   str = ""
    outcomes:           List[Dict[str, Any]] = field(default_factory=list)
    is_resolved:        bool = False
    resolution_summary: str = ""
    generated_at:       str = field(default_factory=_now)


@dataclass
class DecisionPackageTrace:
    spec_id:          int
    spec_name:        str
    present:          List[str] = field(default_factory=list)
    missing:          List[str] = field(default_factory=list)
    ready_for_review: bool = False
    blocking_gaps:    List[str] = field(default_factory=list)
    generated_at:     str = field(default_factory=_now)


@dataclass
class FullTrace:
    spec_id:          int
    spec_name:        str
    chain:            Optional[EvidenceChain] = None
    evidence_quality: Optional[EvidenceQualityTrace] = None
    priority:         Optional[PriorityTrace] = None
    questions:        List[QuestionTrace] = field(default_factory=list)
    decision_package: Optional[DecisionPackageTrace] = None
    generated_at:     str = field(default_factory=_now)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Tuple[str, str]]:
    row = conn.execute(
        "SELECT spec_name, status FROM strategy_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    return (row[0], row[1] or "unknown") if row else None


def _is_count(conn: sqlite3.Connection, spec_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM backtests WHERE spec_id = ? AND is_in_sample = 1",
        (spec_id,)
    ).fetchone()[0]


def _is_trades(conn: sqlite3.Connection, spec_id: int) -> Optional[int]:
    return conn.execute(
        "SELECT MAX(total_trades) FROM backtests WHERE spec_id = ? AND is_in_sample = 1",
        (spec_id,)
    ).fetchone()[0]


def _oos_count(conn: sqlite3.Connection, spec_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM backtests WHERE spec_id = ? AND is_in_sample = 0",
        (spec_id,)
    ).fetchone()[0]


def _latest_score(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT composite_score, grade, recommendation,
               walk_forward_pass, monte_carlo_pass,
               walk_forward_score, monte_carlo_score,
               overfitting_risk, scored_at
        FROM scoring_results WHERE spec_id = ?
        AND scoring_id = (SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = ?)
    """, (spec_id, spec_id)).fetchone()
    if not row:
        return None
    return {
        "composite_score":   row[0],
        "grade":             row[1],
        "recommendation":    row[2],
        "walk_forward_pass": row[3],
        "monte_carlo_pass":  row[4],
        "walk_forward_score": row[5],
        "monte_carlo_score": row[6],
        "overfitting_risk":  row[7],
        "scored_at":         row[8],
    }


def _regime_count(conn: sqlite3.Connection, spec_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM regime_analysis WHERE spec_id = ? AND status = 'completed'",
        (spec_id,)
    ).fetchone()[0]


def _load_outcomes(spec_name: str) -> List[Any]:
    try:
        from research.outcomes.outcome_tracker import load_outcomes  # type: ignore
        return load_outcomes(spec_name)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# build_evidence_chain
# ---------------------------------------------------------------------------

def build_evidence_chain(conn: sqlite3.Connection, spec_id: int) -> EvidenceChain:
    """Chronological chain of all evidence events for a strategy."""
    result = _fetch_spec(conn, spec_id)
    if result is None:
        return EvidenceChain(spec_id=spec_id, spec_name=f"spec_{spec_id}",
                             current_state="NOT_FOUND")
    spec_name, current_state = result
    events: List[EvidenceEvent] = []

    # Backtest events
    bt_rows = conn.execute("""
        SELECT backtest_id, created_at, is_in_sample, total_trades,
               profit_factor, sharpe_ratio, max_drawdown_pct, net_profit
        FROM backtests WHERE spec_id = ? ORDER BY created_at
    """, (spec_id,)).fetchall()

    for bt in bt_rows:
        label = "IS backtest" if bt[2] else "OOS backtest"
        pf = f"PF={bt[4]:.2f}" if bt[4] else "PF=?"
        sh = f"Sharpe={bt[5]:.2f}" if bt[5] else ""
        events.append(EvidenceEvent(
            timestamp=bt[1] or "",
            event_type="backtest",
            source="backtests",
            summary=f"{label} imported: {bt[3] or 0} trades, {pf} {sh}".strip(),
            detail={
                "backtest_id":    bt[0],
                "is_in_sample":   bool(bt[2]),
                "total_trades":   bt[3],
                "profit_factor":  bt[4],
                "sharpe_ratio":   bt[5],
                "max_drawdown_pct": bt[6],
                "net_profit":     bt[7],
            }
        ))

    # Scoring events (full history)
    sr_rows = conn.execute("""
        SELECT scoring_id, scored_at, composite_score, grade, recommendation,
               walk_forward_pass, monte_carlo_pass, overfitting_risk
        FROM scoring_results WHERE spec_id = ? ORDER BY scored_at
    """, (spec_id,)).fetchall()

    for sr in sr_rows:
        score_str = f"{sr[2]:.3f}" if sr[2] is not None else "?"
        events.append(EvidenceEvent(
            timestamp=sr[1] or "",
            event_type="scoring",
            source="scoring_results",
            summary=f"Score computed: {score_str} grade={sr[3]} rec={sr[4]}",
            detail={
                "scoring_id":       sr[0],
                "composite_score":  sr[2],
                "grade":            sr[3],
                "recommendation":   sr[4],
                "walk_forward_pass": bool(sr[5]),
                "monte_carlo_pass": bool(sr[6]),
                "overfitting_risk": sr[7],
            }
        ))

    # Regime analysis events
    ra_rows = conn.execute("""
        SELECT regime_analysis_id, created_at, regime_model, status,
               best_regime, worst_regime, conclusion
        FROM regime_analysis WHERE spec_id = ? ORDER BY created_at
    """, (spec_id,)).fetchall()

    for ra in ra_rows:
        events.append(EvidenceEvent(
            timestamp=ra[1] or "",
            event_type="regime_analysis",
            source="regime_analysis",
            summary=f"Regime ({ra[2]}) {ra[3]}: best={ra[4]}, worst={ra[5]}",
            detail={
                "regime_analysis_id": ra[0],
                "model":      ra[2],
                "status":     ra[3],
                "best_regime":  ra[4],
                "worst_regime": ra[5],
                "conclusion": _trunc(ra[6]),
            }
        ))

    # Outcome events (institutional memory)
    for oc in _load_outcomes(spec_name):
        events.append(EvidenceEvent(
            timestamp=oc.recorded_at,
            event_type="outcome",
            source="research/outcomes",
            summary=f"Outcome: {oc.question_id} -- {_trunc(oc.finding, 80)}",
            detail={
                "outcome_id":      oc.outcome_id,
                "question_id":     oc.question_id,
                "action_taken":    oc.action_taken,
                "finding":         oc.finding,
                "advanced":        oc.advanced,
                "lifecycle_before": oc.lifecycle_before,
                "lifecycle_after":  oc.lifecycle_after,
            }
        ))

    events.sort(key=lambda e: e.timestamp or "")

    return EvidenceChain(
        spec_id=spec_id,
        spec_name=spec_name,
        current_state=current_state,
        events=events,
    )


# ---------------------------------------------------------------------------
# trace_evidence_quality
# ---------------------------------------------------------------------------

def trace_evidence_quality(conn: sqlite3.Connection, spec_id: int) -> EvidenceQualityTrace:
    """Trace how each evidence quality cell was derived, and why the overall grade is what it is."""
    result = _fetch_spec(conn, spec_id)
    if result is None:
        return EvidenceQualityTrace(spec_id=spec_id, spec_name=f"spec_{spec_id}",
                                    overall="INCOMPLETE", grade_reason="Strategy not found")
    spec_name, _ = result
    cells: List[CellTrace] = []

    n_is     = _is_count(conn, spec_id)
    n_trades = _is_trades(conn, spec_id)
    n_oos    = _oos_count(conn, spec_id)
    sr       = _latest_score(conn, spec_id)
    n_regime = _regime_count(conn, spec_id)

    # BT cell
    if n_is == 0:
        bt_val    = "--"
        bt_detail = "No in-sample backtest row found (backtests.is_in_sample=1)"
    elif (n_trades or 0) < _MIN_TRADES_BT:
        bt_val    = "WEAK"
        bt_detail = (f"IS backtest present but only {n_trades} trades "
                     f"(minimum for OK: {_MIN_TRADES_BT})")
    else:
        bt_val    = "OK"
        bt_detail = f"{n_is} IS backtest(s), {n_trades} trades"
    cells.append(CellTrace("bt", bt_val, "backtests (is_in_sample=1)", bt_detail))

    # OOS cell
    oos_val    = "OK" if n_oos > 0 else "--"
    oos_detail = (f"{n_oos} OOS backtest(s) found" if n_oos
                  else "No OOS backtest found (backtests.is_in_sample=0)")
    cells.append(CellTrace("oos", oos_val, "backtests (is_in_sample=0)", oos_detail))

    # WF cell
    if n_is == 0:
        wf_val    = "--"
        wf_detail = "No IS backtest -- WF not applicable"
    elif sr is None:
        wf_val    = "--"
        wf_detail = "No scoring row -- WF not computed"
    elif sr["walk_forward_pass"] == 1:
        wf_val    = "OK"
        wf_detail = (f"walk_forward_pass=1, score={sr['walk_forward_score']:.3f}"
                     f" (scored {str(sr['scored_at'] or '')[:10]})")
    elif (sr["walk_forward_score"] or 0) > 0:
        wf_val    = "WEAK"
        wf_detail = (f"WF tested but failed: walk_forward_pass=0, "
                     f"score={sr['walk_forward_score']:.3f}")
    else:
        wf_val    = "--"
        wf_detail = "walk_forward_score=0 or NULL -- WF not included in this scoring run"
    cells.append(CellTrace("wf", wf_val, "scoring_results.walk_forward_pass", wf_detail))

    # MC cell
    if n_is == 0:
        mc_val    = "--"
        mc_detail = "No IS backtest -- MC not applicable"
    elif sr is None:
        mc_val    = "--"
        mc_detail = "No scoring row -- MC not computed"
    elif sr["monte_carlo_pass"] == 1:
        mc_val    = "OK"
        mc_detail = (f"monte_carlo_pass=1, score={sr['monte_carlo_score']:.3f}"
                     f" (scored {str(sr['scored_at'] or '')[:10]})")
    elif (sr["monte_carlo_score"] or 0) > 0:
        mc_val    = "WEAK"
        mc_detail = (f"MC tested but failed: monte_carlo_pass=0, "
                     f"score={sr['monte_carlo_score']:.3f}")
    else:
        mc_val    = "--"
        mc_detail = "monte_carlo_score=0 or NULL -- MC not included in this scoring run"
    cells.append(CellTrace("mc", mc_val, "scoring_results.monte_carlo_pass", mc_detail))

    # Regime cell
    regime_val    = "OK" if n_regime > 0 else "--"
    regime_detail = (f"{n_regime} completed regime_analysis row(s)" if n_regime
                     else "No completed regime_analysis found")
    cells.append(CellTrace("regime", regime_val,
                            "regime_analysis (status=completed)", regime_detail))

    # Cert cell (file-based, always --)
    cells.append(CellTrace(
        "cert", "--",
        "reports/certification/ (ephemeral, not checked)",
        "Certification reports are gitignored -- check reports/certification/ manually"
    ))

    # Overall grade
    if n_is == 0:
        overall = "INCOMPLETE"
        reason  = "No IS backtest -- evidence base is absent"
    elif n_oos == 0:
        overall = "WEAK"
        reason  = "Missing OOS backtest -- in-sample evidence only, robustness unverified"
    else:
        wf_present = sr is not None and (sr["walk_forward_score"] or 0) > 0
        mc_present = sr is not None and (sr["monte_carlo_score"] or 0) > 0
        if not wf_present or not mc_present:
            missing = []
            if not wf_present: missing.append("WF")
            if not mc_present: missing.append("MC")
            overall = "WEAK"
            reason  = f"Validation gates not run: {', '.join(missing)}"
        elif (sr["walk_forward_pass"] == 1
              and sr["monte_carlo_pass"] == 1
              and (n_trades or 0) >= _MIN_TRADES_STRONG):
            overall = "STRONG"
            reason  = (f"All gates cleared: WF pass, MC pass, "
                       f"{n_trades} trades (>= {_MIN_TRADES_STRONG})")
        else:
            gaps = []
            if sr["walk_forward_pass"] != 1:
                gaps.append("WF not passing")
            if sr["monte_carlo_pass"] != 1:
                gaps.append("MC not passing")
            if (n_trades or 0) < _MIN_TRADES_STRONG:
                gaps.append(f"only {n_trades} trades (< {_MIN_TRADES_STRONG})")
            overall = "MODERATE"
            reason  = "Evidence present but not all gates cleared: " + "; ".join(gaps)

    return EvidenceQualityTrace(
        spec_id=spec_id,
        spec_name=spec_name,
        cells=cells,
        overall=overall,
        grade_reason=reason,
    )


# ---------------------------------------------------------------------------
# trace_priority
# ---------------------------------------------------------------------------

def trace_priority(conn: sqlite3.Connection, spec_id: int) -> PriorityTrace:
    """Trace why this strategy's research questions have the priority they do."""
    result = _fetch_spec(conn, spec_id)
    if result is None:
        return PriorityTrace(spec_id=spec_id, spec_name=f"spec_{spec_id}",
                             derived_from="Strategy not found")
    spec_name, _ = result

    try:
        from research.priorities.priority_engine import rank_research_priorities  # type: ignore
        report = rank_research_priorities(conn)
        relevant = [p for p in report.priorities if spec_id in (p.affected_spec_ids or [])]

        if not relevant:
            return PriorityTrace(
                spec_id=spec_id,
                spec_name=spec_name,
                derived_from="No open research priorities found for this spec",
            )

        top = relevant[0]
        blocking = [
            p.question_type for p in relevant if p.affects_review_required
        ]

        return PriorityTrace(
            spec_id=spec_id,
            spec_name=spec_name,
            open_questions=[
                {
                    "question_type":          p.question_type,
                    "level":                  p.level,
                    "research_value":         p.research_value,
                    "affects_review_required": p.affects_review_required,
                    "effort":                 p.effort,
                    "suggested_action":       p.suggested_action,
                    "evidence_gap_pct":       p.evidence_gap_pct,
                }
                for p in relevant
            ],
            priority_level=top.level,
            research_value=top.research_value,
            blocking_questions=blocking,
            derived_from=(f"research.priorities.priority_engine: "
                          f"{len(relevant)} open question type(s), "
                          f"top level={top.level}, value={top.research_value:.1f}"),
        )

    except Exception as exc:
        return PriorityTrace(
            spec_id=spec_id,
            spec_name=spec_name,
            derived_from=f"Priority engine unavailable: {exc}",
        )


# ---------------------------------------------------------------------------
# trace_question
# ---------------------------------------------------------------------------

def trace_question(conn: sqlite3.Connection,
                   spec_id: int,
                   question_id: str) -> QuestionTrace:
    """Trace a specific research question: what generated it, what we did about it."""
    result = _fetch_spec(conn, spec_id)
    if result is None:
        return QuestionTrace(spec_id=spec_id, spec_name=f"spec_{spec_id}",
                             question_id=question_id,
                             generated_reason="Strategy not found")
    spec_name, _ = result

    generated_reason = ""
    category = ""

    # Ask the question engine what it knows about this question for this spec
    try:
        from research.questions.question_engine import (  # type: ignore
            collect_question_context,
            identify_unknowns,
            _all_scored_specs,
            CLASSIFICATION_PATH,
            PATTERN_LIB_PATH,
        )

        def _load_json(path: Path) -> Dict:
            try:
                return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except Exception:
                return {}

        classifications = _load_json(CLASSIFICATION_PATH)
        pattern_lib    = _load_json(PATTERN_LIB_PATH)

        spec_rows = _all_scored_specs(conn)
        spec_row  = next((r for r in spec_rows if r["spec_id"] == spec_id), None)

        if spec_row:
            ctx = collect_question_context(conn, spec_row, classifications, pattern_lib)
            questions = identify_unknowns(ctx)
            match = next((q for q in questions if q.question_id == question_id), None)
            if match:
                category         = match.category
                generated_reason = (
                    f"{match.question} | "
                    f"Why it matters: {_trunc(match.why_it_matters, 120)} | "
                    f"Missing evidence: {_trunc(match.missing_evidence, 120)}"
                )
            else:
                generated_reason = (
                    f"Question '{question_id}' not currently open for this spec "
                    f"(may have been resolved or is not applicable)"
                )
    except Exception as exc:
        generated_reason = f"Question engine unavailable: {exc}"

    # Load outcomes recorded for this question_id
    all_outcomes = _load_outcomes(spec_name)
    matching     = [oc for oc in all_outcomes if oc.question_id == question_id]
    is_resolved  = any(oc.advanced for oc in matching if oc.advanced is not None)

    if matching:
        last = matching[-1]
        res_summary = _trunc(last.finding, 200)
        if last.advanced:
            res_summary += " -- strategy advanced after this finding"
        elif last.advanced is False:
            res_summary += " -- strategy did not advance; new blockers may have been added"
    else:
        res_summary = "No outcomes recorded yet for this question"

    return QuestionTrace(
        spec_id=spec_id,
        spec_name=spec_name,
        question_id=question_id,
        category=category,
        generated_reason=generated_reason,
        outcomes=[
            {
                "outcome_id":      oc.outcome_id,
                "action_taken":    oc.action_taken,
                "finding":         oc.finding,
                "advanced":        oc.advanced,
                "lifecycle_before": oc.lifecycle_before,
                "lifecycle_after":  oc.lifecycle_after,
                "recorded_at":     oc.recorded_at,
            }
            for oc in matching
        ],
        is_resolved=is_resolved,
        resolution_summary=res_summary,
    )


# ---------------------------------------------------------------------------
# trace_decision_package
# ---------------------------------------------------------------------------

def trace_decision_package(conn: sqlite3.Connection, spec_id: int) -> DecisionPackageTrace:
    """Trace what evidence is present or missing for a human decision."""
    result = _fetch_spec(conn, spec_id)
    if result is None:
        return DecisionPackageTrace(spec_id=spec_id, spec_name=f"spec_{spec_id}",
                                    missing=["Strategy not found"])
    spec_name, _ = result

    present: List[str] = []
    missing: List[str] = []
    blocking: List[str] = []

    n_is     = _is_count(conn, spec_id)
    n_trades = _is_trades(conn, spec_id)
    n_oos    = _oos_count(conn, spec_id)
    sr       = _latest_score(conn, spec_id)
    n_regime = _regime_count(conn, spec_id)

    # IS backtest
    if n_is > 0 and (n_trades or 0) >= _MIN_TRADES_BT:
        present.append(f"IS backtest ({n_trades} trades)")
    elif n_is > 0:
        present.append(f"IS backtest ({n_trades} trades -- below {_MIN_TRADES_BT} threshold)")
        missing.append(f"Sufficient trades (have {n_trades}, need {_MIN_TRADES_BT})")
        blocking.append("Insufficient trade count for statistical confidence")
    else:
        missing.append("IS backtest")
        blocking.append("No backtest -- no evidence base for any decision")

    # OOS backtest
    if n_oos > 0:
        present.append(f"OOS backtest ({n_oos} run(s))")
    else:
        missing.append("OOS backtest")
        blocking.append("Missing OOS -- in-sample performance unverified")

    # Scoring
    if sr:
        present.append(f"Scoring result: grade={sr['grade']}, score={sr['composite_score']:.3f}")
    else:
        missing.append("Scoring result")
        blocking.append("No score -- strategy has not been evaluated")

    # Walk-forward
    if sr and sr["walk_forward_pass"] == 1:
        present.append(f"Walk-forward: PASS (score={sr['walk_forward_score']:.3f})")
    elif sr and (sr["walk_forward_score"] or 0) > 0:
        missing.append("Walk-forward: tested but FAILED")
        blocking.append("Walk-forward failure -- OOS degradation too high")
    else:
        missing.append("Walk-forward: not run")
        blocking.append("Walk-forward not completed")

    # Monte Carlo
    if sr and sr["monte_carlo_pass"] == 1:
        present.append(f"Monte Carlo: PASS (score={sr['monte_carlo_score']:.3f})")
    elif sr and (sr["monte_carlo_score"] or 0) > 0:
        missing.append("Monte Carlo: tested but FAILED")
        blocking.append("Monte Carlo failure -- survival rate below threshold")
    else:
        missing.append("Monte Carlo: not run")
        blocking.append("Monte Carlo not completed")

    # Regime analysis
    if n_regime > 0:
        present.append(f"Regime analysis ({n_regime} completed run(s))")
    else:
        missing.append("Regime analysis")

    # Open blockers from outcome tracker
    outcomes = _load_outcomes(spec_name)
    unresolved_blockers = []
    for oc in outcomes:
        for b in (oc.new_blockers or []):
            unresolved_blockers.append(b)
    if unresolved_blockers:
        blocking.extend(unresolved_blockers)

    ready = (
        n_is > 0
        and (n_trades or 0) >= _MIN_TRADES_STRONG
        and n_oos > 0
        and sr is not None
        and sr["walk_forward_pass"] == 1
        and sr["monte_carlo_pass"] == 1
    )

    return DecisionPackageTrace(
        spec_id=spec_id,
        spec_name=spec_name,
        present=present,
        missing=missing,
        ready_for_review=ready,
        blocking_gaps=blocking,
    )


# ---------------------------------------------------------------------------
# generate_trace_report
# ---------------------------------------------------------------------------

def generate_trace_report(
    conn:        sqlite3.Connection,
    spec_id:     int,
    reports_dir: Path = REPORTS_DIR,
    dry_run:     bool = False,
) -> Tuple[Optional[Path], Optional[Path]]:
    """Assemble all traces for a spec and write Markdown + JSON reports."""
    result = _fetch_spec(conn, spec_id)
    spec_name = result[0] if result else f"spec_{spec_id}"

    chain    = build_evidence_chain(conn, spec_id)
    quality  = trace_evidence_quality(conn, spec_id)
    priority = trace_priority(conn, spec_id)
    pkg      = trace_decision_package(conn, spec_id)

    full = FullTrace(
        spec_id=spec_id,
        spec_name=spec_name,
        chain=chain,
        evidence_quality=quality,
        priority=priority,
        decision_package=pkg,
    )

    md  = _render_markdown(full)
    obj = _to_dict(full)

    if dry_run:
        print(md)
        return None, None

    reports_dir.mkdir(parents=True, exist_ok=True)
    date   = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem   = f"trace_{_safe(spec_name)}_{date}"
    md_path  = reports_dir / f"{stem}.md"
    json_path = reports_dir / f"{stem}.json"

    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    return md_path, json_path


def _to_dict(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_dict(i) for i in obj]
    return obj


def _render_markdown(full: FullTrace) -> str:
    lines = []

    lines.append(f"# Belief Provenance: {full.spec_name}")
    lines.append(f"\nGenerated: {full.generated_at}")
    lines.append(f"\n> Why do we believe what we believe about `{full.spec_name}`?\n")

    # Evidence quality
    q = full.evidence_quality
    if q:
        lines.append("## Evidence Quality\n")
        lines.append(f"**Overall: {q.overall}**")
        lines.append(f"\nReason: {q.grade_reason}\n")
        lines.append("| Column | Value | Source | Detail |")
        lines.append("|--------|-------|--------|--------|")
        for c in q.cells:
            lines.append(f"| {c.column} | {c.value} | {c.source} | {c.detail} |")
        lines.append("")

    # Evidence chain
    ch = full.chain
    if ch and ch.events:
        lines.append("## Evidence Chain\n")
        lines.append(f"Current state: `{ch.current_state}`")
        lines.append(f"\n{len(ch.events)} event(s) in chronological order:\n")
        for ev in ch.events:
            ts = str(ev.timestamp or "")[:16].replace("T", " ")
            lines.append(f"- `{ts}` [{ev.event_type}] {ev.summary}")
        lines.append("")

    # Priority
    pr = full.priority
    if pr:
        lines.append("## Research Priority\n")
        lines.append(f"**Level: {pr.priority_level}**  |  Research value: {pr.research_value:.1f}")
        if pr.blocking_questions:
            lines.append(f"\nBlocking gates: {', '.join(pr.blocking_questions)}")
        lines.append(f"\nDerived from: {pr.derived_from}")
        if pr.open_questions:
            lines.append("\n| Question type | Level | Value | Blocks gate |")
            lines.append("|---------------|-------|-------|-------------|")
            for oq in pr.open_questions:
                lines.append(
                    f"| {oq['question_type']} | {oq['level']} "
                    f"| {oq['research_value']:.1f} | {oq['affects_review_required']} |"
                )
        lines.append("")

    # Decision package
    dp = full.decision_package
    if dp:
        lines.append("## Decision Package Readiness\n")
        ready_str = "YES -- ready for human review" if dp.ready_for_review else "NO -- gaps remain"
        lines.append(f"**Ready for review: {ready_str}**\n")
        if dp.present:
            lines.append("Present:")
            for item in dp.present:
                lines.append(f"  - {item}")
        if dp.missing:
            lines.append("\nMissing:")
            for item in dp.missing:
                lines.append(f"  - {item}")
        if dp.blocking_gaps:
            lines.append("\nBlocking gaps:")
            for b in dp.blocking_gaps:
                lines.append(f"  - {b}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("REVIEW_REQUIRED. The pipeline stops here. Human authority begins.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Console display helpers
# ---------------------------------------------------------------------------

def _print_chain(chain: EvidenceChain) -> None:
    print(f"\nEvidence Chain: {chain.spec_name} (state: {chain.current_state})")
    print(f"  {len(chain.events)} event(s)\n")
    for ev in chain.events:
        ts = str(ev.timestamp or "")[:16].replace("T", " ")
        print(f"  {ts}  [{ev.event_type:<18}]  {ev.summary}")


def _print_quality(q: EvidenceQualityTrace) -> None:
    print(f"\nEvidence Quality: {q.spec_name}")
    print(f"  Overall : {q.overall}")
    print(f"  Reason  : {q.grade_reason}\n")
    for c in q.cells:
        print(f"  {c.column:<8}  {c.value:<6}  {c.source}")
        print(f"           {c.detail}")


def _print_priority(pr: PriorityTrace) -> None:
    print(f"\nResearch Priority: {pr.spec_name}")
    print(f"  Level          : {pr.priority_level}")
    print(f"  Research value : {pr.research_value:.1f}")
    print(f"  Derived from   : {pr.derived_from}")
    if pr.blocking_questions:
        print(f"  Blocking gates : {', '.join(pr.blocking_questions)}")
    for oq in pr.open_questions:
        print(f"    {oq['level']:<8} {oq['question_type']:<35} value={oq['research_value']:.1f}")


def _print_question(qt: QuestionTrace) -> None:
    print(f"\nQuestion Trace: {qt.spec_name} / {qt.question_id}")
    print(f"  Category         : {qt.category or '(unknown)'}")
    print(f"  Generated reason : {qt.generated_reason}")
    print(f"  Resolved         : {qt.is_resolved}")
    print(f"  Resolution       : {qt.resolution_summary}")
    if qt.outcomes:
        print(f"  Outcomes ({len(qt.outcomes)}):")
        for oc in qt.outcomes:
            print(f"    [{oc['recorded_at'][:10]}] {_trunc(oc['finding'], 80)}")
            print(f"      advanced={oc['advanced']}")


def _print_package(dp: DecisionPackageTrace) -> None:
    ready_str = "YES" if dp.ready_for_review else "NO"
    print(f"\nDecision Package: {dp.spec_name}")
    print(f"  Ready for review: {ready_str}\n")
    for item in dp.present:
        print(f"  [PRESENT] {item}")
    for item in dp.missing:
        print(f"  [MISSING] {item}")
    if dp.blocking_gaps:
        print()
        for b in dp.blocking_gaps:
            print(f"  [BLOCKING] {b}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _open_db(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Belief Provenance Engine -- trace pipeline state to its evidence"
    )
    parser.add_argument("--spec-id",  type=int, required=True,
                        help="Strategy spec ID to trace")
    parser.add_argument("--chain",    action="store_true",
                        help="Show evidence chain (chronological events)")
    parser.add_argument("--quality",  action="store_true",
                        help="Trace evidence quality cells")
    parser.add_argument("--priority", action="store_true",
                        help="Trace research priority")
    parser.add_argument("--question", metavar="QUESTION_ID",
                        help="Trace a specific research question")
    parser.add_argument("--decision-package", action="store_true",
                        help="Trace decision package readiness")
    parser.add_argument("--report",   action="store_true",
                        help="Generate full Markdown + JSON trace report")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print to console; do not write files")
    parser.add_argument("--db",       default=str(DEFAULT_DB),
                        help="Path to SQLite database")
    args = parser.parse_args()

    show_all = not any([
        args.chain, args.quality, args.priority,
        args.question, args.decision_package, args.report
    ])

    try:
        conn = _open_db(Path(args.db))
    except Exception as exc:
        print(f"Cannot open database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        spec_id = args.spec_id

        if show_all or args.chain:
            _print_chain(build_evidence_chain(conn, spec_id))

        if show_all or args.quality:
            _print_quality(trace_evidence_quality(conn, spec_id))

        if show_all or args.priority:
            _print_priority(trace_priority(conn, spec_id))

        if show_all or args.decision_package:
            _print_package(trace_decision_package(conn, spec_id))

        if args.question:
            _print_question(trace_question(conn, spec_id, args.question))

        if args.report:
            md_path, json_path = generate_trace_report(
                conn, spec_id, dry_run=args.dry_run
            )
            if md_path:
                print(f"\nTrace report written:")
                print(f"  {md_path}")
                print(f"  {json_path}")
            else:
                print("\n(dry-run: no files written)")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
