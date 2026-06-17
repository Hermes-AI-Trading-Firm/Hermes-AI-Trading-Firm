#!/usr/bin/env python3
"""
Governance Ledger -- research/governance/governance_ledger.py

Phase 41

Records changes to the pipeline itself.

Unlike the Human Decision Journal (Phase 40) which records what the human
decided about strategies, the Governance Ledger records what changed about
the system that produces the evidence.

What it records
---------------
  PHASE_ADDED           -- a new phase entered the pipeline
  RULE_ADDED            -- a new rule was introduced
  RULE_CHANGED          -- an existing rule was modified
  PIPELINE_CHANGED      -- the pipeline structure changed
  SCORING_CHANGED       -- scoring logic or thresholds changed
  VALIDATION_CHANGED    -- validation logic or thresholds changed
  GOVERNANCE_DECISION   -- a governance decision was recorded
  HUMAN_PROCESS_CHANGE  -- how the human interacts with the pipeline changed

What it does NOT do
-------------------
- Does not make decisions
- Does not modify the pipeline
- Does not change strategy state
- Does not advance any strategy past REVIEW_REQUIRED

Storage
-------
Entries live in research/governance/governance_ledger.json and are
committed to git as institutional memory.

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
    # Record a governance event
    python -m research.governance.governance_ledger --record \\
        --type PHASE_ADDED \\
        --phase "Phase 41" \\
        --question "What changed about the pipeline and why?" \\
        --summary "Record changes to the pipeline itself." \\
        --tqt-evidence PASS \\
        --tqt-questions PASS \\
        --tqt-authority PASS \\
        --authority-impact NONE \\
        --review-required-impact UNCHANGED \\
        --author "human" \\
        --commit abc1234

    # Show full governance history
    python -m research.governance.governance_ledger --history

    # Filter by change type
    python -m research.governance.governance_ledger --history --filter PHASE_ADDED

    # Show governance evolution summary
    python -m research.governance.governance_ledger --evolution

    # Show invariant history (entries that touched REVIEW_REQUIRED or authority)
    python -m research.governance.governance_ledger --invariants

    # Dry-run: print to console, no files written
    python -m research.governance.governance_ledger --record ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]

LEDGER_PATH = _PROJECT_ROOT / "research" / "governance" / "governance_ledger.json"

VALID_CHANGE_TYPES = {
    "PHASE_ADDED",
    "RULE_ADDED",
    "RULE_CHANGED",
    "PIPELINE_CHANGED",
    "SCORING_CHANGED",
    "VALIDATION_CHANGED",
    "GOVERNANCE_DECISION",
    "HUMAN_PROCESS_CHANGE",
}

VALID_TQT = {"PASS", "FAIL", "N/A"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class GovernanceEvent:
    ledger_id:              str
    date:                   str            # YYYY-MM-DD
    change_type:            str            # one of VALID_CHANGE_TYPES
    phase:                  str            # "Phase 40" or "" if system-wide
    question:               str            # the question this change answers
    summary:                str            # why added / what changed
    three_question_test:    Dict[str, str] # better_evidence / better_questions / human_decides
    authority_impact:       str            # NONE / REDUCED / CLARIFIED / etc.
    review_required_impact: str            # UNCHANGED / MODIFIED / CLARIFIED / etc.
    affected_phases:        List[str]
    author:                 str
    commit:                 str            # git commit hash if applicable
    recorded_at:            str            # system timestamp


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _load_events() -> List[GovernanceEvent]:
    if not LEDGER_PATH.exists():
        return []
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        return [GovernanceEvent(**d) for d in data]
    except Exception:
        return []


def _save_events(events: List[GovernanceEvent]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(
        json.dumps([asdict(e) for e in events], indent=2),
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# record_governance_event
# ---------------------------------------------------------------------------

def record_governance_event(
    change_type:            str,
    summary:                str,
    phase:                  str = "",
    question:               str = "",
    tqt_evidence:           str = "N/A",
    tqt_questions:          str = "N/A",
    tqt_authority:          str = "N/A",
    authority_impact:       str = "NONE",
    review_required_impact: str = "UNCHANGED",
    affected_phases:        Optional[List[str]] = None,
    author:                 str = "human",
    commit:                 str = "",
    date:                   Optional[str] = None,
    dry_run:                bool = False,
) -> GovernanceEvent:
    """Record a governance event in the pipeline's change history."""
    if change_type not in VALID_CHANGE_TYPES:
        raise ValueError(
            f"Invalid change_type: {change_type!r}. "
            f"Must be one of: {', '.join(sorted(VALID_CHANGE_TYPES))}"
        )
    for label, val in [("tqt_evidence",  tqt_evidence),
                       ("tqt_questions", tqt_questions),
                       ("tqt_authority", tqt_authority)]:
        if val not in VALID_TQT:
            raise ValueError(
                f"Invalid {label}: {val!r}. "
                f"Must be one of: {', '.join(sorted(VALID_TQT))}"
            )

    event = GovernanceEvent(
        ledger_id=              str(uuid.uuid4())[:8],
        date=                   date or datetime.now().strftime("%Y-%m-%d"),
        change_type=            change_type,
        phase=                  phase,
        question=               question,
        summary=                summary,
        three_question_test={
            "better_evidence":  tqt_evidence,
            "better_questions": tqt_questions,
            "human_decides":    tqt_authority,
        },
        authority_impact=       authority_impact,
        review_required_impact= review_required_impact,
        affected_phases=        affected_phases or [],
        author=                 author,
        commit=                 commit,
        recorded_at=            _now(),
    )

    if not dry_run:
        events = _load_events()
        events.append(event)
        _save_events(events)

    return event


# ---------------------------------------------------------------------------
# load_governance_history
# ---------------------------------------------------------------------------

def load_governance_history(change_type: Optional[str] = None) -> List[GovernanceEvent]:
    """Load the full governance history, optionally filtered by change type."""
    events = _load_events()
    if change_type:
        events = [e for e in events if e.change_type == change_type]
    return events


# ---------------------------------------------------------------------------
# summarize_governance_evolution
# ---------------------------------------------------------------------------

@dataclass
class GovernanceEvolution:
    total_events:           int
    by_type:                Dict[str, int]
    phases_added:           List[str]
    invariant_events:       int            # events that touched authority or REVIEW_REQUIRED
    invariants_held:        bool           # True if no invariant was ever broken
    generated_at:           str = field(default_factory=_now)


def summarize_governance_evolution(
    events: Optional[List[GovernanceEvent]] = None,
) -> GovernanceEvolution:
    """Summarize how the pipeline has evolved over its governance history."""
    if events is None:
        events = _load_events()

    by_type: Dict[str, int] = {}
    phases_added: List[str] = []
    invariant_event_count = 0
    invariants_held = True

    for e in events:
        by_type[e.change_type] = by_type.get(e.change_type, 0) + 1
        if e.change_type == "PHASE_ADDED" and e.phase:
            phases_added.append(e.phase)
        touched = (
            e.authority_impact       != "NONE"      or
            e.review_required_impact != "UNCHANGED"
        )
        if touched:
            invariant_event_count += 1
            tqt = e.three_question_test
            if tqt.get("human_decides") == "FAIL":
                invariants_held = False

    return GovernanceEvolution(
        total_events=     len(events),
        by_type=          by_type,
        phases_added=     phases_added,
        invariant_events= invariant_event_count,
        invariants_held=  invariants_held,
    )


# ---------------------------------------------------------------------------
# show_invariant_history
# ---------------------------------------------------------------------------

def show_invariant_history() -> List[GovernanceEvent]:
    """
    Return all events that touched REVIEW_REQUIRED or human authority.

    These are the moments where the pipeline's core invariants were considered.
    Even if the decision was UNCHANGED / NONE -- the consideration is worth seeing.

    Invariants:
      - REVIEW_REQUIRED is the terminal automated state
      - Human authority is never diluted
    """
    events = _load_events()
    return [
        e for e in events
        if (e.authority_impact       != "NONE" or
            e.review_required_impact != "UNCHANGED")
    ]


# ---------------------------------------------------------------------------
# Console display
# ---------------------------------------------------------------------------

def _print_event(e: GovernanceEvent) -> None:
    tqt = e.three_question_test
    print(f"\n  [{e.date}] {e.change_type}  phase={e.phase or 'system-wide'}")
    if e.question:
        print(f"  Question : {e.question}")
    print(f"  Summary  : {e.summary}")
    print(f"  TQT      : evidence={tqt.get('better_evidence', 'N/A')}  "
          f"questions={tqt.get('better_questions', 'N/A')}  "
          f"authority={tqt.get('human_decides', 'N/A')}")
    print(f"  Authority impact       : {e.authority_impact}")
    print(f"  REVIEW_REQUIRED impact : {e.review_required_impact}")
    if e.affected_phases:
        print(f"  Affected phases        : {', '.join(e.affected_phases)}")
    if e.commit:
        print(f"  Commit   : {e.commit}")
    print(f"  Author   : {e.author}  recorded={e.recorded_at}")


def _print_evolution(s: GovernanceEvolution) -> None:
    print(f"\nGovernance Evolution")
    print(f"  Total events    : {s.total_events}")
    print(f"  Phases added    : {len(s.phases_added)}")
    print(f"  Invariant events: {s.invariant_events}")
    print(f"  Invariants held : {'YES' if s.invariants_held else 'NO -- review required'}\n")
    print("  By type:")
    for t, n in sorted(s.by_type.items()):
        print(f"    {t:<25} {n}")
    if s.phases_added:
        print(f"\n  Phases added (in order):")
        for p in s.phases_added:
            print(f"    {p}")


def _print_invariant_history(events: List[GovernanceEvent]) -> None:
    print(f"\nInvariant History")
    print(f"  Invariants: REVIEW_REQUIRED is terminal. Human authority is never diluted.\n")
    if not events:
        print("  No invariant events recorded.")
        print("  REVIEW_REQUIRED: UNCHANGED across all pipeline history.")
        print("  Human authority: NONE impact across all pipeline history.")
        return
    print(f"  {len(events)} event(s) touched the invariants:\n")
    for e in events:
        _print_event(e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Governance Ledger -- record and retrieve pipeline change history"
    )

    parser.add_argument("--record",        action="store_true",
                        help="Record a new governance event")
    parser.add_argument("--type",          choices=sorted(VALID_CHANGE_TYPES),
                        help="Change type")
    parser.add_argument("--phase",         default="",
                        help="Phase name (e.g. 'Phase 41')")
    parser.add_argument("--question",      default="",
                        help="The question this change answers")
    parser.add_argument("--summary",       default="",
                        help="Why this change was made / what changed")
    parser.add_argument("--tqt-evidence",  default="N/A", choices=sorted(VALID_TQT),
                        help="Three-question test: better evidence? (PASS/FAIL/N/A)")
    parser.add_argument("--tqt-questions", default="N/A", choices=sorted(VALID_TQT),
                        help="Three-question test: better questions? (PASS/FAIL/N/A)")
    parser.add_argument("--tqt-authority", default="N/A", choices=sorted(VALID_TQT),
                        help="Three-question test: human still decides? (PASS/FAIL/N/A)")
    parser.add_argument("--authority-impact",       default="NONE",
                        help="Impact on human authority (default: NONE)")
    parser.add_argument("--review-required-impact", default="UNCHANGED",
                        help="Impact on REVIEW_REQUIRED gate (default: UNCHANGED)")
    parser.add_argument("--affected",      nargs="*", default=[],
                        help="Affected phase names")
    parser.add_argument("--author",        default="human",
                        help="Who recorded this change")
    parser.add_argument("--commit",        default="",
                        help="Git commit hash")
    parser.add_argument("--date",          default="",
                        help="Change date (YYYY-MM-DD, default: today)")
    parser.add_argument("--history",       action="store_true",
                        help="Show full governance history")
    parser.add_argument("--filter",        default="",
                        choices=list(VALID_CHANGE_TYPES) + [""],
                        help="Filter history by change type")
    parser.add_argument("--evolution",     action="store_true",
                        help="Show governance evolution summary")
    parser.add_argument("--invariants",    action="store_true",
                        help="Show invariant history (entries that touched REVIEW_REQUIRED or authority)")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Print to console; do not write files")

    args = parser.parse_args()

    if args.record:
        if not args.type:
            print("--type is required for --record", file=sys.stderr)
            sys.exit(1)
        if not args.summary:
            print("--summary is required for --record", file=sys.stderr)
            sys.exit(1)

        try:
            event = record_governance_event(
                change_type=            args.type,
                summary=                args.summary,
                phase=                  args.phase,
                question=               args.question,
                tqt_evidence=           args.tqt_evidence,
                tqt_questions=          args.tqt_questions,
                tqt_authority=          args.tqt_authority,
                authority_impact=       args.authority_impact,
                review_required_impact= args.review_required_impact,
                affected_phases=        args.affected,
                author=                 args.author,
                commit=                 args.commit,
                date=                   args.date or None,
                dry_run=                args.dry_run,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

        print(f"\nGovernance event recorded: {event.change_type}")
        print(f"  Ledger ID : {event.ledger_id}")
        if event.phase:
            print(f"  Phase     : {event.phase}")
        print(f"  Summary   : {event.summary}")
        print(f"  Date      : {event.date}")
        print(f"  Author    : {event.author}")
        if args.dry_run:
            print("\n(dry-run: no file written)")
        else:
            print(f"\nWritten to: {LEDGER_PATH}")
        return

    if args.evolution:
        _print_evolution(summarize_governance_evolution())
        return

    if args.invariants:
        _print_invariant_history(show_invariant_history())
        return

    if args.history or args.filter:
        events = load_governance_history(change_type=args.filter or None)
        if not events:
            print("\nNo governance history found.")
            return
        print(f"\nGovernance History  ({len(events)} events)")
        for e in events:
            _print_event(e)
        print()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
