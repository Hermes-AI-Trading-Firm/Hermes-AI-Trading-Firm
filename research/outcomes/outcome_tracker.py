#!/usr/bin/env python3
"""
Research Outcome Tracker -- research/outcomes/outcome_tracker.py

Phase 35

Question: "When we ran the experiment, what did we learn?"

The Three-Question Test:
  Can it produce better evidence?   YES -- accumulates a record of what was tried and found
  Can it ask a better question?     YES -- patterns in outcomes sharpen which questions to ask
  Does a human still decide?        YES -- outcomes are human-recorded; tracker observes only

Scope (approved):
  MAY record human-observed outcomes
  MAY summarize patterns
  MAY suggest which research questions are historically useful
  MAY NOT decide whether a strategy is approved, rejected, or advanced

What it does NOT do
-------------------
- Does not decide whether a strategy is approved, rejected, or advanced
- Does not change strategy state
- Does not write to the database
- Does not advance any strategy past REVIEW_REQUIRED
- Does not answer its own questions

Storage
-------
Outcome logs live in research/outcomes/ and are committed to git.
They are institutional memory, not disposable reports.

  research/outcomes/<spec_name>_outcomes.json   -- per-strategy outcome log

Usage
-----
    # Record a completed research action
    python -m research.outcomes.outcome_tracker --record \\
        --spec-id 1 \\
        --question-id mc_missing \\
        --action "Ran Monte Carlo with 10000 trials" \\
        --finding "MC survival 91.2%, passes 85% gate" \\
        --advanced yes \\
        --lifecycle-before VALIDATED_WF \\
        --lifecycle-after VALIDATED_WF

    # Show outcome history for one strategy
    python -m research.outcomes.outcome_tracker --spec-id 1

    # Show all outcomes + portfolio summary
    python -m research.outcomes.outcome_tracker --all

    # Show pattern analysis (which question types produce advancement)
    python -m research.outcomes.outcome_tracker --patterns

    # Suggest high-value open questions for a strategy (overlays historical patterns)
    python -m research.outcomes.outcome_tracker --suggest --spec-id 1

    # Dry-run: console output only
    python -m research.outcomes.outcome_tracker --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

OUTCOMES_DIR = _PROJECT_ROOT / "research" / "outcomes"
DEFAULT_DB   = _PROJECT_ROOT / "database" / "hermes_research.db"

MAX_AUTOMATED_STATE = "REVIEW_REQUIRED"

_PRIORITY_WEIGHT = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}

_CATEGORY_LABELS = {
    "DATA_QUALITY":          "Data Quality",
    "SAMPLE_SIZE":           "Sample Size",
    "OOS_WALK_FORWARD":      "OOS / Walk-Forward",
    "MONTE_CARLO":           "Monte Carlo",
    "REGIME_DEPENDENCY":     "Regime Dependency",
    "PROP_FIRM_RISK":        "Prop-Firm Risk",
    "ARCHETYPE_WEAKNESS":    "Archetype Weakness",
    "PARAMETER_SENSITIVITY": "Parameter Sensitivity",
    "EXECUTION_ASSUMPTIONS": "Execution Assumptions",
    "RESEARCH_PRIORITY":     "Research Priority",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OutcomeRecord:
    outcome_id:       str
    spec_id:          int
    spec_name:        str
    question_id:      str
    category:         str
    action_taken:     str
    finding:          str
    evidence_added:   str
    lifecycle_before: str
    lifecycle_after:  str
    advanced:         Optional[bool]   # True/False/None (unknown)
    new_blockers:     List[str]
    recorded_at:      str
    recorded_by:      str = "human"


@dataclass
class OutcomePattern:
    question_id:        str
    category:           str
    times_answered:     int
    times_advanced:     int
    times_blocked:      int            # new blockers discovered
    advancement_rate:   float          # 0.0-1.0
    strategies_seen:    List[str]
    last_seen:          str
    example_finding:    str


@dataclass
class SuggestedAction:
    question_id:               str
    category:                  str
    current_priority:          str
    historical_advancement_rate: float
    times_historically_answered: int
    composite_score:           float   # priority_weight * advancement_rate
    suggestion:                str
    spec_id:                   Optional[int]
    spec_name:                 Optional[str]


@dataclass
class OutcomeSummary:
    total_outcomes:                 int
    total_strategies_with_outcomes: int
    total_advanced:                 int
    total_not_advanced:             int
    total_unknown:                  int
    overall_advancement_rate:       float
    patterns:                       List[OutcomePattern]
    generated_at:                   str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    )


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name)


def _outcome_path(spec_name: str) -> Path:
    return OUTCOMES_DIR / f"{_safe(spec_name)}_outcomes.json"


def _load_spec_outcomes(spec_name: str) -> List[OutcomeRecord]:
    path = _outcome_path(spec_name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    records = []
    for d in data.get("outcomes", []):
        records.append(OutcomeRecord(
            outcome_id       = d.get("outcome_id", ""),
            spec_id          = d.get("spec_id", 0),
            spec_name        = d.get("spec_name", spec_name),
            question_id      = d.get("question_id", ""),
            category         = d.get("category", ""),
            action_taken     = d.get("action_taken", ""),
            finding          = d.get("finding", ""),
            evidence_added   = d.get("evidence_added", ""),
            lifecycle_before = d.get("lifecycle_before", "UNKNOWN"),
            lifecycle_after  = d.get("lifecycle_after", "UNKNOWN"),
            advanced         = d.get("advanced"),
            new_blockers     = d.get("new_blockers", []),
            recorded_at      = d.get("recorded_at", ""),
            recorded_by      = d.get("recorded_by", "human"),
        ))
    return records


def _save_spec_outcomes(spec_name: str, spec_id: int, records: List[OutcomeRecord]) -> Path:
    OUTCOMES_DIR.mkdir(parents=True, exist_ok=True)
    path = _outcome_path(spec_name)
    data = {
        "spec_id":   spec_id,
        "spec_name": spec_name,
        "outcomes": [
            {
                "outcome_id":       r.outcome_id,
                "spec_id":          r.spec_id,
                "spec_name":        r.spec_name,
                "question_id":      r.question_id,
                "category":         r.category,
                "action_taken":     r.action_taken,
                "finding":          r.finding,
                "evidence_added":   r.evidence_added,
                "lifecycle_before": r.lifecycle_before,
                "lifecycle_after":  r.lifecycle_after,
                "advanced":         r.advanced,
                "new_blockers":     r.new_blockers,
                "recorded_at":      r.recorded_at,
                "recorded_by":      r.recorded_by,
            }
            for r in records
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def record_outcome(
    spec_id:          int,
    spec_name:        str,
    question_id:      str,
    category:         str,
    action_taken:     str,
    finding:          str,
    evidence_added:   str = "",
    lifecycle_before: str = "UNKNOWN",
    lifecycle_after:  str = "UNKNOWN",
    advanced:         Optional[bool] = None,
    new_blockers:     Optional[List[str]] = None,
    dry_run:          bool = False,
) -> OutcomeRecord:
    """
    Record a completed research action and its finding.
    Human-triggered only. The tracker records; it does not conclude.
    """
    record = OutcomeRecord(
        outcome_id       = str(uuid.uuid4())[:8],
        spec_id          = spec_id,
        spec_name        = spec_name,
        question_id      = question_id,
        category         = category,
        action_taken     = action_taken,
        finding          = finding,
        evidence_added   = evidence_added,
        lifecycle_before = lifecycle_before,
        lifecycle_after  = lifecycle_after,
        advanced         = advanced,
        new_blockers     = new_blockers or [],
        recorded_at      = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        recorded_by      = "human",
    )
    if not dry_run:
        existing = _load_spec_outcomes(spec_name)
        existing.append(record)
        _save_spec_outcomes(spec_name, spec_id, existing)
    return record


def load_outcomes(spec_name: str) -> List[OutcomeRecord]:
    """Load outcome history for one strategy."""
    return _load_spec_outcomes(spec_name)


def load_all_outcomes() -> List[OutcomeRecord]:
    """Load all outcomes across all strategies."""
    if not OUTCOMES_DIR.exists():
        return []
    all_records: List[OutcomeRecord] = []
    for path in sorted(OUTCOMES_DIR.glob("*_outcomes.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            spec_name = data.get("spec_name", path.stem.replace("_outcomes", ""))
            for d in data.get("outcomes", []):
                all_records.append(OutcomeRecord(
                    outcome_id       = d.get("outcome_id", ""),
                    spec_id          = d.get("spec_id", 0),
                    spec_name        = spec_name,
                    question_id      = d.get("question_id", ""),
                    category         = d.get("category", ""),
                    action_taken     = d.get("action_taken", ""),
                    finding          = d.get("finding", ""),
                    evidence_added   = d.get("evidence_added", ""),
                    lifecycle_before = d.get("lifecycle_before", "UNKNOWN"),
                    lifecycle_after  = d.get("lifecycle_after", "UNKNOWN"),
                    advanced         = d.get("advanced"),
                    new_blockers     = d.get("new_blockers", []),
                    recorded_at      = d.get("recorded_at", ""),
                    recorded_by      = d.get("recorded_by", "human"),
                ))
        except Exception:
            continue
    return all_records


def identify_patterns(outcomes: List[OutcomeRecord]) -> List[OutcomePattern]:
    """
    Group outcomes by question_id and compute advancement rates.
    Which question types, when answered, most often advance strategies?
    """
    groups: Dict[str, List[OutcomeRecord]] = {}
    for r in outcomes:
        groups.setdefault(r.question_id, []).append(r)

    patterns: List[OutcomePattern] = []
    for qid, records in groups.items():
        decided   = [r for r in records if r.advanced is not None]
        advanced  = [r for r in decided if r.advanced is True]
        blocked   = [r for r in records if r.new_blockers]
        rate      = len(advanced) / len(decided) if decided else 0.5  # 0.5 = no data

        # Best example: most recent advanced outcome, or most recent overall
        examples  = [r for r in records if r.advanced] or records
        example   = sorted(examples, key=lambda r: r.recorded_at)[-1]

        patterns.append(OutcomePattern(
            question_id       = qid,
            category          = records[0].category,
            times_answered    = len(records),
            times_advanced    = len(advanced),
            times_blocked     = len(blocked),
            advancement_rate  = round(rate, 2),
            strategies_seen   = sorted(set(r.spec_name for r in records)),
            last_seen         = max(r.recorded_at for r in records),
            example_finding   = example.finding[:120] + ("..." if len(example.finding) > 120 else ""),
        ))

    patterns.sort(key=lambda p: (-p.advancement_rate, -p.times_answered, p.question_id))
    return patterns


def summarize_outcomes(outcomes: List[OutcomeRecord]) -> OutcomeSummary:
    """Portfolio-level summary of all recorded outcomes."""
    decided   = [r for r in outcomes if r.advanced is not None]
    advanced  = [r for r in decided if r.advanced is True]
    not_adv   = [r for r in decided if r.advanced is False]
    unknown   = [r for r in outcomes if r.advanced is None]
    rate      = len(advanced) / len(decided) if decided else 0.0
    strats    = set(r.spec_name for r in outcomes)
    patterns  = identify_patterns(outcomes)

    return OutcomeSummary(
        total_outcomes                 = len(outcomes),
        total_strategies_with_outcomes = len(strats),
        total_advanced                 = len(advanced),
        total_not_advanced             = len(not_adv),
        total_unknown                  = len(unknown),
        overall_advancement_rate       = round(rate, 2),
        patterns                       = patterns,
    )


def suggest_high_value_questions(
    conn:     sqlite3.Connection,
    spec_id:  int,
    patterns: List[OutcomePattern],
) -> List[SuggestedAction]:
    """
    For a given strategy, take its current open questions and overlay
    historical advancement rates from the outcome tracker.

    Returns a ranked list of suggestions. Higher composite score =
    more historically effective AND currently urgent.

    Does NOT decide what to approve. Surfaces which questions, based on
    evidence from the past, have most often unlocked advancement.
    """
    from research.questions.question_engine import (
        generate_strategy_questions,
        CLASSIFICATION_PATH,
        PATTERN_LIB_PATH,
    )

    def _load_json(p: Path) -> Dict:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    classifications = _load_json(CLASSIFICATION_PATH)
    pattern_lib     = _load_json(PATTERN_LIB_PATH)

    _, questions = generate_strategy_questions(conn, spec_id, classifications, pattern_lib)
    if not questions:
        return []

    rate_map: Dict[str, float] = {p.question_id: p.advancement_rate for p in patterns}
    times_map: Dict[str, int]  = {p.question_id: p.times_answered for p in patterns}

    suggestions: List[SuggestedAction] = []
    for q in questions:
        pw   = _PRIORITY_WEIGHT.get(q.priority, 0.3)
        rate = rate_map.get(q.question_id, 0.5)   # 0.5 = no history (neutral)
        times = times_map.get(q.question_id, 0)
        score = round(pw * rate, 3)

        if times == 0:
            hist_note = "No historical data -- effectiveness unknown"
        elif rate >= 0.75:
            hist_note = (
                f"Historically effective: {times} times answered, "
                f"{int(rate*100)}% advanced strategy"
            )
        elif rate >= 0.50:
            hist_note = (
                f"Moderate history: {times} times answered, "
                f"{int(rate*100)}% advanced strategy"
            )
        else:
            hist_note = (
                f"Low historical yield: {times} times answered, "
                f"only {int(rate*100)}% advanced strategy -- "
                f"consider whether the blocker is elsewhere"
            )

        suggestions.append(SuggestedAction(
            question_id                  = q.question_id,
            category                     = q.category,
            current_priority             = q.priority,
            historical_advancement_rate  = rate,
            times_historically_answered  = times,
            composite_score              = score,
            suggestion                   = hist_note,
            spec_id                      = q.spec_id,
            spec_name                    = q.spec_name,
        ))

    suggestions.sort(key=lambda s: (-s.composite_score, s.question_id))
    return suggestions


# ---------------------------------------------------------------------------
# Console rendering
# ---------------------------------------------------------------------------

def _render_record(r: OutcomeRecord, index: int) -> List[str]:
    adv_str = {True: "YES -- strategy advanced", False: "NO -- did not advance",
                None: "unknown"}.get(r.advanced, "unknown")
    lines = [
        f"  [{index}] {r.question_id}  ({r.category})",
        f"      Action:   {r.action_taken}",
        f"      Finding:  {r.finding}",
    ]
    if r.evidence_added:
        lines.append(f"      Evidence: {r.evidence_added}")
    lines += [
        f"      Lifecycle: {r.lifecycle_before} -> {r.lifecycle_after}",
        f"      Advanced:  {adv_str}",
    ]
    if r.new_blockers:
        lines.append(f"      New blockers: {'; '.join(r.new_blockers)}")
    lines.append(f"      Recorded:  {r.recorded_at} by {r.recorded_by}")
    return lines


def print_strategy_outcomes(spec_name: str) -> None:
    records = load_outcomes(spec_name)
    print(f"\nOutcome History: {spec_name}")
    print("=" * 60)
    if not records:
        print("  No outcomes recorded yet.")
        print("  Record one with: --record --spec-id N ...")
        return
    for i, r in enumerate(records, 1):
        for line in _render_record(r, i):
            print(line)
        print()


def print_summary(summary: OutcomeSummary) -> None:
    print("\nResearch Outcome Tracker -- Portfolio Summary")
    print("=" * 60)
    print(f"Total outcomes recorded:     {summary.total_outcomes}")
    print(f"Strategies with history:     {summary.total_strategies_with_outcomes}")
    print(f"Advanced strategy:           {summary.total_advanced}")
    print(f"Did not advance:             {summary.total_not_advanced}")
    print(f"Outcome unknown:             {summary.total_unknown}")
    if summary.total_advanced + summary.total_not_advanced > 0:
        print(f"Overall advancement rate:    {summary.overall_advancement_rate*100:.0f}%")
    print()


def print_patterns(patterns: List[OutcomePattern]) -> None:
    print("\nQuestion Effectiveness Patterns")
    print("Which question types, when answered, most often advance strategies?")
    print("=" * 60)
    if not patterns:
        print("  No patterns yet. Record more outcomes to build history.")
        return
    for p in patterns:
        rate_str = f"{p.advancement_rate*100:.0f}%" if p.times_answered > 0 else "no data"
        print(f"\n  {p.question_id}  [{p.category}]")
        print(f"    Answered {p.times_answered}x across: {', '.join(p.strategies_seen)}")
        print(f"    Advanced: {p.times_advanced} of {p.times_answered}  ({rate_str})")
        if p.times_blocked:
            print(f"    Revealed new blockers: {p.times_blocked}x")
        print(f"    Example: {p.example_finding}")
    print()


def print_suggestions(suggestions: List[SuggestedAction], spec_name: str) -> None:
    print(f"\nHigh-Value Question Suggestions: {spec_name}")
    print("Ranked by: current priority x historical advancement rate")
    print("=" * 60)
    if not suggestions:
        print("  No open questions found for this strategy.")
        return
    for i, s in enumerate(suggestions, 1):
        print(f"\n  {i}. {s.question_id}  [{s.category}]")
        print(f"     Current priority:  {s.current_priority}")
        print(f"     Historical rate:   {s.historical_advancement_rate*100:.0f}%"
              f"  ({s.times_historically_answered} times answered)")
        print(f"     Composite score:   {s.composite_score:.2f}")
        print(f"     Assessment:        {s.suggestion}")
    print()
    print("  NOTE: These are suggestions based on historical patterns.")
    print("  They do not decide whether a strategy should be approved.")
    print()


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _resolve_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Tuple[int, str]]:
    row = conn.execute(
        "SELECT spec_id, spec_name FROM strategy_specs WHERE spec_id = ?",
        (spec_id,)
    ).fetchone()
    if not row:
        return None
    return row[0], row[1]


def _question_category(question_id: str) -> str:
    prefixes = {
        "dq_":     "DATA_QUALITY",
        "ss_":     "SAMPLE_SIZE",
        "oos_":    "OOS_WALK_FORWARD",
        "mc_":     "MONTE_CARLO",
        "regime_": "REGIME_DEPENDENCY",
        "pf_":     "PROP_FIRM_RISK",
        "arch_":   "ARCHETYPE_WEAKNESS",
        "param_":  "PARAMETER_SENSITIVITY",
        "exec_":   "EXECUTION_ASSUMPTIONS",
        "priority_": "RESEARCH_PRIORITY",
        "global_": "DATA_QUALITY",
    }
    for prefix, cat in prefixes.items():
        if question_id.startswith(prefix):
            return cat
    return "DATA_QUALITY"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research Outcome Tracker (Phase 35)"
    )
    parser.add_argument("--record",          action="store_true", help="Record a new outcome")
    parser.add_argument("--spec-id",         type=int,            help="Target strategy spec_id")
    parser.add_argument("--question-id",     type=str,            help="Question type being answered")
    parser.add_argument("--action",          type=str,            help="What was done")
    parser.add_argument("--finding",         type=str,            help="What was found")
    parser.add_argument("--evidence",        type=str, default="", help="Evidence added (optional)")
    parser.add_argument("--lifecycle-before",type=str, default="UNKNOWN")
    parser.add_argument("--lifecycle-after", type=str, default="UNKNOWN")
    parser.add_argument("--advanced",        type=str, default="unknown",
                        choices=["yes", "no", "unknown"],
                        help="Did this action advance the strategy?")
    parser.add_argument("--blockers",        type=str, default="",
                        help="Comma-separated new blockers discovered")
    parser.add_argument("--all",             action="store_true", help="Show all outcomes + summary")
    parser.add_argument("--patterns",        action="store_true", help="Show pattern analysis")
    parser.add_argument("--suggest",         action="store_true",
                        help="Suggest high-value open questions (requires --spec-id)")
    parser.add_argument("--dry-run",         action="store_true", help="Console only; no files written")
    parser.add_argument("--db",              default=str(DEFAULT_DB))
    args = parser.parse_args()

    db_path = Path(args.db)

    # --record
    if args.record:
        if not all([args.spec_id, args.question_id, args.action, args.finding]):
            print("ERROR: --record requires --spec-id, --question-id, --action, --finding",
                  file=sys.stderr)
            sys.exit(1)
        if not db_path.exists():
            print(f"ERROR: database not found: {db_path}", file=sys.stderr)
            sys.exit(1)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            resolved = _resolve_spec(conn, args.spec_id)
        finally:
            conn.close()
        if not resolved:
            print(f"ERROR: spec_id {args.spec_id} not found", file=sys.stderr)
            sys.exit(1)

        sid, sname = resolved
        adv = {"yes": True, "no": False, "unknown": None}.get(args.advanced)
        blockers = [b.strip() for b in args.blockers.split(",") if b.strip()] if args.blockers else []
        cat = _question_category(args.question_id)

        record = record_outcome(
            spec_id          = sid,
            spec_name        = sname,
            question_id      = args.question_id,
            category         = cat,
            action_taken     = args.action,
            finding          = args.finding,
            evidence_added   = args.evidence,
            lifecycle_before = args.lifecycle_before,
            lifecycle_after  = args.lifecycle_after,
            advanced         = adv,
            new_blockers     = blockers,
            dry_run          = args.dry_run,
        )

        print(f"\nOutcome recorded [{record.outcome_id}]")
        print(f"  Strategy:   {sname} (spec_id={sid})")
        print(f"  Question:   {record.question_id}  [{cat}]")
        print(f"  Action:     {record.action_taken}")
        print(f"  Finding:    {record.finding}")
        adv_str = {True: "YES", False: "NO", None: "unknown"}.get(record.advanced)
        print(f"  Advanced:   {adv_str}")
        if blockers:
            print(f"  Blockers:   {'; '.join(blockers)}")
        if args.dry_run:
            print("\n  [dry-run] No file written.")
        else:
            print(f"\n  Written to: {_outcome_path(sname)}")
        return

    # --suggest
    if args.suggest:
        if not args.spec_id:
            print("ERROR: --suggest requires --spec-id", file=sys.stderr)
            sys.exit(1)
        if not db_path.exists():
            print(f"ERROR: database not found: {db_path}", file=sys.stderr)
            sys.exit(1)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            resolved = _resolve_spec(conn, args.spec_id)
            if not resolved:
                print(f"ERROR: spec_id {args.spec_id} not found", file=sys.stderr)
                sys.exit(1)
            sid, sname = resolved
            all_outcomes = load_all_outcomes()
            patterns     = identify_patterns(all_outcomes)
            suggestions  = suggest_high_value_questions(conn, sid, patterns)
        finally:
            conn.close()
        print_suggestions(suggestions, sname)
        return

    # --spec-id (history view)
    if args.spec_id and not args.all and not args.patterns:
        if not db_path.exists():
            print(f"ERROR: database not found: {db_path}", file=sys.stderr)
            sys.exit(1)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            resolved = _resolve_spec(conn, args.spec_id)
        finally:
            conn.close()
        if not resolved:
            print(f"ERROR: spec_id {args.spec_id} not found", file=sys.stderr)
            sys.exit(1)
        _, sname = resolved
        print_strategy_outcomes(sname)
        return

    # --all / --patterns
    if args.all or args.patterns:
        all_outcomes = load_all_outcomes()

        if args.all:
            if not all_outcomes:
                print("\nNo outcomes recorded yet.")
                print("Record one with: python -m research.outcomes.outcome_tracker --record ...")
            else:
                summary = summarize_outcomes(all_outcomes)
                print_summary(summary)

                strats = sorted(set(r.spec_name for r in all_outcomes))
                for sname in strats:
                    print_strategy_outcomes(sname)

        if args.patterns:
            patterns = identify_patterns(all_outcomes)
            print_patterns(patterns)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
