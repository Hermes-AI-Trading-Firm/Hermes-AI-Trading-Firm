#!/usr/bin/env python3
"""
Human Decision Journal -- research/review_journal/review_journal.py

Phase 40

The eighth layer. The only one that decides.

Records what the human reviewer saw, decided, and would do next.
Committed to git as institutional memory. Every future reviewer
can learn from every prior one.

Unlike every prior phase, this module does not read from the pipeline
and produce output for the human. It receives from the human and
preserves it.

What it does NOT do
-------------------
- Does not make decisions
- Does not approve or reject strategies
- Does not change strategy state in the database
- Does not advance any strategy past REVIEW_REQUIRED

What it does
------------
- Records human decisions at the REVIEW_REQUIRED gate
- Preserves the reasoning behind each decision
- Accumulates institutional memory of what reviewers saw and why
- Surfaces patterns in how humans have decided on similar strategies

Decision values
---------------
  APPROVE_FOR_NEXT_RESEARCH_STAGE  -- evidence sufficient; advance to next stage
  REJECT                           -- strategy fails review; archive with reason
  NEED_MORE_EVIDENCE               -- return to research pipeline with specific gaps
  DEFER                            -- not ready to decide; revisit later

Storage
-------
Journal entries live in research/review_journal/ and are committed to git.
They are institutional memory. Not disposable reports.

  research/review_journal/<spec_name>_review_journal.json

Phase compliance
----------------
  [1] No DB writes
  [2] No strategy state changes (journal records only; pipeline state unchanged)
  [3] No approval automation
  [4] No path beyond REVIEW_REQUIRED
  [5] Human authority unchanged

Usage
-----
    # Record a review decision
    python -m research.review_journal.review_journal --record \\
        --spec-id 1 \\
        --reviewer "your name" \\
        --decision APPROVE_FOR_NEXT_RESEARCH_STAGE \\
        --reasoning "Strong edge with PF 2.43. OOS gap is the primary concern." \\
        --key-evidence "PF=2.43" "Sharpe=1.85" "247 trades" \\
        --concerns "No OOS validation yet" "Single regime window" \\
        --unanswered "Does edge hold OOS?" "Regime dependency?" \\
        --next-actions "Run OOS backtest" "Regime analysis" \\
        --confidence HIGH

    # Show journal for one strategy
    python -m research.review_journal.review_journal --spec-id 1

    # Show all journals across all strategies
    python -m research.review_journal.review_journal --all

    # Show decision summary patterns
    python -m research.review_journal.review_journal --summary

    # Dry-run: print to console, no files written
    python -m research.review_journal.review_journal --record ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]

JOURNAL_DIR = _PROJECT_ROOT / "research" / "review_journal"

VALID_DECISIONS = {
    "APPROVE_FOR_NEXT_RESEARCH_STAGE",
    "REJECT",
    "NEED_MORE_EVIDENCE",
    "DEFER",
}

VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}

MAX_AUTOMATED_STATE = "REVIEW_REQUIRED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe(name: str) -> str:
    import re
    return re.sub(r"[^\w\-]", "_", name)


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class JournalEntry:
    journal_id:           str
    spec_id:              int
    spec_name:            str
    reviewer:             str
    date:                 str          # reviewer-supplied date (YYYY-MM-DD)
    decision:             str          # one of VALID_DECISIONS
    reasoning:            str          # why this decision
    key_evidence:         List[str]    # what evidence mattered most
    concerns:             List[str]    # what concerns remained
    unanswered_questions: List[str]    # what would they still investigate
    next_research_actions: List[str]   # concrete actions if NEED_MORE_EVIDENCE
    confidence_level:     str          # HIGH / MEDIUM / LOW
    recorded_at:          str          # system timestamp
    recorded_by:          str = "human"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _journal_path(spec_name: str) -> Path:
    return JOURNAL_DIR / f"{_safe(spec_name)}_review_journal.json"


def _load_entries(path: Path) -> List[JournalEntry]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = []
        for d in data:
            entries.append(JournalEntry(**d))
        return entries
    except Exception:
        return []


def _save_entries(path: Path, entries: List[JournalEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(e) for e in entries], indent=2),
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# record_review
# ---------------------------------------------------------------------------

def record_review(
    spec_id:               int,
    spec_name:             str,
    reviewer:              str,
    decision:              str,
    reasoning:             str,
    key_evidence:          Optional[List[str]] = None,
    concerns:              Optional[List[str]] = None,
    unanswered_questions:  Optional[List[str]] = None,
    next_research_actions: Optional[List[str]] = None,
    confidence_level:      str = "MEDIUM",
    date:                  Optional[str] = None,
    dry_run:               bool = False,
) -> JournalEntry:
    """Record a human review decision for a strategy."""
    if decision not in VALID_DECISIONS:
        raise ValueError(
            f"Invalid decision: {decision!r}. "
            f"Must be one of: {', '.join(sorted(VALID_DECISIONS))}"
        )
    if confidence_level not in VALID_CONFIDENCE:
        raise ValueError(
            f"Invalid confidence: {confidence_level!r}. "
            f"Must be one of: {', '.join(sorted(VALID_CONFIDENCE))}"
        )

    entry = JournalEntry(
        journal_id=            str(uuid.uuid4())[:8],
        spec_id=               spec_id,
        spec_name=             spec_name,
        reviewer=              reviewer,
        date=                  date or datetime.now().strftime("%Y-%m-%d"),
        decision=              decision,
        reasoning=             reasoning,
        key_evidence=          key_evidence or [],
        concerns=              concerns or [],
        unanswered_questions=  unanswered_questions or [],
        next_research_actions= next_research_actions or [],
        confidence_level=      confidence_level,
        recorded_at=           _now(),
    )

    if not dry_run:
        path    = _journal_path(spec_name)
        entries = _load_entries(path)
        entries.append(entry)
        _save_entries(path, entries)

    return entry


# ---------------------------------------------------------------------------
# load_journal
# ---------------------------------------------------------------------------

def load_journal(spec_name: str) -> List[JournalEntry]:
    """Load all journal entries for one strategy."""
    return _load_entries(_journal_path(spec_name))


# ---------------------------------------------------------------------------
# load_all_journals
# ---------------------------------------------------------------------------

def load_all_journals() -> List[JournalEntry]:
    """Load all journal entries across all strategies."""
    entries: List[JournalEntry] = []
    if not JOURNAL_DIR.exists():
        return entries
    for path in sorted(JOURNAL_DIR.glob("*_review_journal.json")):
        entries.extend(_load_entries(path))
    entries.sort(key=lambda e: e.recorded_at)
    return entries


# ---------------------------------------------------------------------------
# summarize_decisions
# ---------------------------------------------------------------------------

@dataclass
class DecisionSummary:
    total_reviews:      int
    total_strategies:   int
    by_decision:        Dict[str, int]
    by_confidence:      Dict[str, int]
    common_concerns:    List[str]
    common_next_actions: List[str]
    generated_at:       str = field(default_factory=_now)


def summarize_decisions(entries: Optional[List[JournalEntry]] = None) -> DecisionSummary:
    """Summarize patterns across all recorded review decisions."""
    if entries is None:
        entries = load_all_journals()

    by_decision: Dict[str, int] = {}
    by_confidence: Dict[str, int] = {}
    concern_counts: Dict[str, int] = {}
    action_counts: Dict[str, int] = {}
    specs_seen: set = set()

    for e in entries:
        specs_seen.add(e.spec_name)
        by_decision[e.decision] = by_decision.get(e.decision, 0) + 1
        by_confidence[e.confidence_level] = by_confidence.get(e.confidence_level, 0) + 1
        for c in e.concerns:
            concern_counts[c] = concern_counts.get(c, 0) + 1
        for a in e.next_research_actions:
            action_counts[a] = action_counts.get(a, 0) + 1

    common_concerns = sorted(concern_counts, key=lambda k: -concern_counts[k])[:5]
    common_actions  = sorted(action_counts,  key=lambda k: -action_counts[k])[:5]

    return DecisionSummary(
        total_reviews=    len(entries),
        total_strategies= len(specs_seen),
        by_decision=      by_decision,
        by_confidence=    by_confidence,
        common_concerns=  common_concerns,
        common_next_actions= common_actions,
    )


# ---------------------------------------------------------------------------
# Console display
# ---------------------------------------------------------------------------

def _print_entry(e: JournalEntry) -> None:
    print(f"\n  [{e.date}] {e.decision}  confidence={e.confidence_level}  reviewer={e.reviewer}")
    print(f"  Reasoning: {e.reasoning}")
    if e.key_evidence:
        print(f"  Key evidence: {', '.join(e.key_evidence)}")
    if e.concerns:
        print(f"  Concerns: {', '.join(e.concerns)}")
    if e.unanswered_questions:
        print(f"  Unanswered: {', '.join(e.unanswered_questions)}")
    if e.next_research_actions:
        print(f"  Next actions: {', '.join(e.next_research_actions)}")


def _print_journal(spec_name: str, entries: List[JournalEntry]) -> None:
    print(f"\nReview Journal: {spec_name}")
    print(f"  {len(entries)} review(s) recorded\n")
    if not entries:
        print("  No reviews recorded yet.")
        return
    for e in entries:
        _print_entry(e)


def _print_summary(summary: DecisionSummary) -> None:
    print(f"\nDecision Summary")
    print(f"  Total reviews    : {summary.total_reviews}")
    print(f"  Strategies seen  : {summary.total_strategies}\n")
    print("  By decision:")
    for d, n in sorted(summary.by_decision.items()):
        print(f"    {d:<40} {n}")
    print("\n  By confidence:")
    for c, n in sorted(summary.by_confidence.items()):
        print(f"    {c:<10} {n}")
    if summary.common_concerns:
        print("\n  Most common concerns:")
        for c in summary.common_concerns:
            print(f"    - {c}")
    if summary.common_next_actions:
        print("\n  Most common next actions:")
        for a in summary.common_next_actions:
            print(f"    - {a}")


# ---------------------------------------------------------------------------
# Journal entry format (for reference)
# ---------------------------------------------------------------------------

ENTRY_FORMAT = {
    "journal_id":            "(auto)",
    "spec_id":               "(int)",
    "spec_name":             "(str)",
    "reviewer":              "(str)",
    "date":                  "YYYY-MM-DD",
    "decision":              "APPROVE_FOR_NEXT_RESEARCH_STAGE | REJECT | NEED_MORE_EVIDENCE | DEFER",
    "reasoning":             "(str) -- why this decision",
    "key_evidence":          ["(str) -- what evidence mattered most"],
    "concerns":              ["(str) -- what concerns remained"],
    "unanswered_questions":  ["(str) -- what the reviewer would still investigate"],
    "next_research_actions": ["(str) -- concrete actions if NEED_MORE_EVIDENCE"],
    "confidence_level":      "HIGH | MEDIUM | LOW",
    "recorded_at":           "(auto -- system timestamp)",
    "recorded_by":           "human",
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Human Decision Journal -- record and retrieve review decisions"
    )

    parser.add_argument("--record",   action="store_true",
                        help="Record a new review decision")
    parser.add_argument("--spec-id",  type=int,
                        help="Strategy spec ID")
    parser.add_argument("--spec-name", default="",
                        help="Strategy spec name (required for --record)")
    parser.add_argument("--reviewer", default="",
                        help="Reviewer name")
    parser.add_argument("--date",     default="",
                        help="Review date (YYYY-MM-DD, default: today)")
    parser.add_argument("--decision", choices=sorted(VALID_DECISIONS),
                        help="Review decision")
    parser.add_argument("--reasoning", default="",
                        help="Why this decision was made")
    parser.add_argument("--key-evidence",    nargs="*", default=[],
                        help="Evidence items that mattered most")
    parser.add_argument("--concerns",        nargs="*", default=[],
                        help="Concerns that remain")
    parser.add_argument("--unanswered",      nargs="*", default=[],
                        help="Questions the reviewer would still investigate")
    parser.add_argument("--next-actions",    nargs="*", default=[],
                        help="Concrete next research actions")
    parser.add_argument("--confidence",      default="MEDIUM",
                        choices=sorted(VALID_CONFIDENCE),
                        help="Reviewer confidence level (HIGH/MEDIUM/LOW)")
    parser.add_argument("--all",      action="store_true",
                        help="Show all journal entries across all strategies")
    parser.add_argument("--summary",  action="store_true",
                        help="Show decision pattern summary")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print to console; do not write files")
    args = parser.parse_args()

    if args.record:
        if not args.spec_name:
            print("--spec-name is required for --record", file=sys.stderr)
            sys.exit(1)
        if not args.reviewer:
            print("--reviewer is required for --record", file=sys.stderr)
            sys.exit(1)
        if not args.decision:
            print("--decision is required for --record", file=sys.stderr)
            sys.exit(1)
        if not args.reasoning:
            print("--reasoning is required for --record", file=sys.stderr)
            sys.exit(1)

        spec_id = args.spec_id or 0

        try:
            entry = record_review(
                spec_id=               spec_id,
                spec_name=             args.spec_name,
                reviewer=              args.reviewer,
                decision=              args.decision,
                reasoning=             args.reasoning,
                key_evidence=          args.key_evidence,
                concerns=              args.concerns,
                unanswered_questions=  args.unanswered,
                next_research_actions= args.next_actions,
                confidence_level=      args.confidence,
                date=                  args.date or None,
                dry_run=               args.dry_run,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

        print(f"\nDecision recorded: {entry.decision}")
        print(f"  Journal ID   : {entry.journal_id}")
        print(f"  Strategy     : {entry.spec_name}")
        print(f"  Reviewer     : {entry.reviewer}")
        print(f"  Date         : {entry.date}")
        print(f"  Confidence   : {entry.confidence_level}")
        print(f"  Recorded at  : {entry.recorded_at}")
        if args.dry_run:
            print("\n(dry-run: no file written)")
        else:
            print(f"\nWritten to: {_journal_path(entry.spec_name)}")
        print()
        print(f"  {MAX_AUTOMATED_STATE}. The pipeline stops here.")
        print(f"  The journal records what happened next.")
        return

    if args.summary:
        _print_summary(summarize_decisions())
        return

    if args.all:
        all_entries = load_all_journals()
        if not all_entries:
            print("\nNo review journal entries found.")
            return
        by_spec: Dict[str, List[JournalEntry]] = {}
        for e in all_entries:
            by_spec.setdefault(e.spec_name, []).append(e)
        for spec_name, entries in sorted(by_spec.items()):
            _print_journal(spec_name, entries)
        print(f"\nTotal: {len(all_entries)} review(s) across {len(by_spec)} strategy/ies")
        return

    if args.spec_name:
        entries = load_journal(args.spec_name)
        _print_journal(args.spec_name, entries)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
