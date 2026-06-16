#!/usr/bin/env python3
"""
Research Portfolio Constructor -- research/portfolio/portfolio_constructor.py

Question: "Among all candidates, where should human attention be focused?"

The Three-Question Test (Phase 30):
  Can it produce better evidence?   YES -- cross-portfolio state view
  Can it ask a better question?     YES -- prioritised attention queue
  Does a human still decide?        YES -- REVIEW_REQUIRED is terminal

What it does
------------
Reads every strategy across all pipeline layers and answers one question:
given limited human attention, which strategies deserve it most, and why?

Output
------
1. Attention Queue   -- ranked list of REVIEW_REQUIRED candidates
2. Portfolio Health  -- distribution across all readiness states
3. Archetype Balance -- which archetypes are well-represented or thin
4. Stale Candidates  -- strategies with no recent progress
5. Next-Action Map   -- per-strategy single most important next step

Priority scoring (0-100 base + modifiers)
------------------------------------------
  READY_FOR_HUMAN_REVIEW    80
  NEEDS_REGIME_ANALYSIS     45  (warning only -- close to ready)
  NEEDS_MONTE_CARLO         30
  NEEDS_WALK_FORWARD        20
  NEEDS_MORE_TRADES         10
  NEEDS_REAL_NT8_EXPORT      5
  REJECT_RESEARCH_CANDIDATE  0

Modifiers (applied on top of base):
  Comparison verdict LEADS    +15
  Comparison verdict TRAILS    -8
  Comparison verdict SOLE      +5
  Per strength detected        +3  (capped at +12)
  Per hard blocker             -5  (capped at -20)
  Composite score >= 80        +8
  Composite score >= 70        +4

What it does NOT do
-------------------
- Does not write to any database table
- Does not approve or reject strategies
- Does not move any strategy past REVIEW_REQUIRED
- Does not override human judgment

Usage
-----
    python -m research.portfolio.portfolio_constructor --all
    python -m research.portfolio.portfolio_constructor --top 5
    python -m research.portfolio.portfolio_constructor --all --dry-run
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

DEFAULT_DB           = _PROJECT_ROOT / "database" / "hermes_research.db"
DECISION_PKG_DIR     = _PROJECT_ROOT / "reports"  / "decision_packages"
COMPARISON_DIR       = _PROJECT_ROOT / "reports"  / "comparison"
LEARNING_DIR         = _PROJECT_ROOT / "reports"  / "learning"
CLASSIFICATION_PATH  = _PROJECT_ROOT / "research" / "archetype" / "classifications.json"
REPORTS_DIR          = _PROJECT_ROOT / "reports"  / "portfolio"

# Base priority scores by readiness status
_BASE_PRIORITY: Dict[str, int] = {
    "READY_FOR_HUMAN_REVIEW":    80,
    "NEEDS_REGIME_ANALYSIS":     45,
    "NEEDS_MONTE_CARLO":         30,
    "NEEDS_WALK_FORWARD":        20,
    "NEEDS_MORE_TRADES":         10,
    "NEEDS_REAL_NT8_EXPORT":      5,
    "REJECT_RESEARCH_CANDIDATE":  0,
}

_STATUS_LABEL: Dict[str, str] = {
    "READY_FOR_HUMAN_REVIEW":    "[READY]   ",
    "NEEDS_REGIME_ANALYSIS":     "[REGIME]  ",
    "NEEDS_MONTE_CARLO":         "[MC]      ",
    "NEEDS_WALK_FORWARD":        "[WF]      ",
    "NEEDS_MORE_TRADES":         "[TRADES]  ",
    "NEEDS_REAL_NT8_EXPORT":     "[EXPORT]  ",
    "REJECT_RESEARCH_CANDIDATE": "[REJECT]  ",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class StrategyEntry:
    spec_id:          int
    spec_name:        str
    symbol:           str
    timeframe:        str
    readiness_status: str
    composite_score:  Optional[float]
    blocker_count:    int
    warning_count:    int
    strength_count:   int
    archetype_id:     str
    archetype_label:  str
    comparison_verdict: str          # LEADS / COMPETITIVE / TRAILS / SOLE / INSUFFICIENT_DATA / UNKNOWN
    next_action:      str
    priority_score:   float
    generated_at:     str


@dataclass
class Portfolio:
    entries:          List[StrategyEntry]
    generated_at:     str
    health:           Dict[str, int]      # status -> count
    archetype_counts: Dict[str, int]      # archetype_id -> count
    attention_queue:  List[StrategyEntry] # READY_FOR_HUMAN_REVIEW, ranked
    stale:            List[StrategyEntry] # REJECT or stuck with 0 progress possible


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name)


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


def _load_decision_pkg(spec_name: str) -> Optional[Dict]:
    return _latest_json(DECISION_PKG_DIR, _safe_name(spec_name), "_decision_package_")


def _load_comparison(spec_id: int) -> Optional[Dict]:
    """
    Scan all comparison JSON files in reports/comparison/ for this spec_id.
    Returns the most recent result dict for the spec, or None.
    """
    if not COMPARISON_DIR.exists():
        return None
    candidates = sorted(COMPARISON_DIR.glob("comparison*.json"))
    if not candidates:
        return None
    latest = candidates[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        for r in data.get("results", []):
            if r.get("spec_id") == spec_id:
                return r
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_specs(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute("""
        SELECT
            s.spec_id,
            s.spec_name,
            COALESCE(s.symbol,    '') AS symbol,
            COALESCE(s.timeframe, '') AS timeframe,
            sc.composite_score
        FROM strategy_specs s
        JOIN scoring_results sc ON sc.spec_id = s.spec_id
            AND sc.scoring_id = (
                SELECT MAX(sc2.scoring_id)
                FROM scoring_results sc2
                WHERE sc2.spec_id = s.spec_id
            )
        ORDER BY s.spec_id
    """).fetchall()
    return [
        {"spec_id": r[0], "spec_name": r[1],
         "symbol": r[2], "timeframe": r[3], "composite_score": r[4]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Priority calculation
# ---------------------------------------------------------------------------

def _compute_priority(
    readiness: str,
    verdict:   str,
    blockers:  int,
    strengths: int,
    score:     Optional[float],
) -> float:
    base = float(_BASE_PRIORITY.get(readiness, 0))

    # Comparison modifier
    if verdict == "LEADS":
        base += 15
    elif verdict == "TRAILS":
        base -= 8
    elif verdict == "SOLE":
        base += 5

    # Strength bonus (capped)
    base += min(strengths * 3, 12)

    # Blocker penalty (capped)
    base -= min(blockers * 5, 20)

    # Composite score bonus
    if score is not None:
        if score >= 80:
            base += 8
        elif score >= 70:
            base += 4

    return max(base, 0.0)


# ---------------------------------------------------------------------------
# Portfolio construction
# ---------------------------------------------------------------------------

def _build_entry(
    spec:            Dict,
    classifications: Dict,
) -> StrategyEntry:
    spec_id   = spec["spec_id"]
    spec_name = spec["spec_name"]

    # Decision package
    pkg = _load_decision_pkg(spec_name) or {}
    readiness  = pkg.get("readiness_status", "NEEDS_REAL_NT8_EXPORT")
    blockers   = [b for b in pkg.get("blockers", []) if b.get("severity") == "BLOCKER"]
    warnings   = [b for b in pkg.get("blockers", []) if b.get("severity") == "WARNING"]
    strengths  = pkg.get("strengths", [])
    next_action = pkg.get("required_action", "Generate decision package first.")

    # Archetype
    clsf_map    = classifications.get("classifications", {})
    clsf        = clsf_map.get(str(spec_id), {})
    arch_id     = clsf.get("primary",       "unknown")
    arch_label  = clsf.get("primary_label", "Unknown")

    # Comparison
    cmp = _load_comparison(spec_id) or {}
    verdict = cmp.get("verdict", "UNKNOWN")

    priority = _compute_priority(
        readiness = readiness,
        verdict   = verdict,
        blockers  = len(blockers),
        strengths = len(strengths),
        score     = spec.get("composite_score"),
    )

    return StrategyEntry(
        spec_id           = spec_id,
        spec_name         = spec_name,
        symbol            = spec.get("symbol",    ""),
        timeframe         = spec.get("timeframe", ""),
        readiness_status  = readiness,
        composite_score   = spec.get("composite_score"),
        blocker_count     = len(blockers),
        warning_count     = len(warnings),
        strength_count    = len(strengths),
        archetype_id      = arch_id,
        archetype_label   = arch_label,
        comparison_verdict = verdict,
        next_action       = next_action,
        priority_score    = priority,
        generated_at      = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    )


def build_portfolio(conn: sqlite3.Connection) -> Portfolio:
    classifications = {}
    if CLASSIFICATION_PATH.exists():
        try:
            classifications = json.loads(
                CLASSIFICATION_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    specs   = _load_specs(conn)
    entries = [_build_entry(s, classifications) for s in specs]
    entries.sort(key=lambda e: e.priority_score, reverse=True)

    health: Dict[str, int] = {}
    for e in entries:
        health[e.readiness_status] = health.get(e.readiness_status, 0) + 1

    arch_counts: Dict[str, int] = {}
    for e in entries:
        arch_counts[e.archetype_id] = arch_counts.get(e.archetype_id, 0) + 1

    attention = [e for e in entries
                 if e.readiness_status == "READY_FOR_HUMAN_REVIEW"]

    stale = [e for e in entries
             if e.readiness_status == "REJECT_RESEARCH_CANDIDATE"]

    return Portfolio(
        entries          = entries,
        generated_at     = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        health           = health,
        archetype_counts = arch_counts,
        attention_queue  = attention,
        stale            = stale,
    )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

_STATUS_ORDER = [
    "READY_FOR_HUMAN_REVIEW",
    "NEEDS_REGIME_ANALYSIS",
    "NEEDS_MONTE_CARLO",
    "NEEDS_WALK_FORWARD",
    "NEEDS_MORE_TRADES",
    "NEEDS_REAL_NT8_EXPORT",
    "REJECT_RESEARCH_CANDIDATE",
]


def _render_markdown(portfolio: Portfolio, top_n: Optional[int] = None) -> str:
    lines: List[str] = []
    total = len(portfolio.entries)

    def p(s: str = "") -> None:
        lines.append(s)

    p("# Research Portfolio")
    p(f"**Generated:** {portfolio.generated_at[:10]}  |  "
      f"**Strategies:** {total}")
    p()
    p("> Among all candidates, where should human attention be focused?")
    p()
    p("---")
    p()

    # Attention queue
    queue = portfolio.attention_queue
    if top_n:
        queue = queue[:top_n]

    p("## Attention Queue")
    p()
    if queue:
        p(f"{'Rank':<5} {'Strategy':<35} {'Archetype':<20} "
          f"{'Verdict':<14} {'Score':>6} {'Priority':>8}")
        p("-" * 92)
        for i, e in enumerate(queue, 1):
            score_str   = f"{e.composite_score:.1f}" if e.composite_score else "N/A"
            p(f"{i:<5} {e.spec_name:<35} {e.archetype_label:<20} "
              f"{e.comparison_verdict:<14} {score_str:>6} {e.priority_score:>8.1f}")
        p()
        p(f"*{len(queue)} strategies ready for human review.*")
    else:
        p("*No strategies currently in READY_FOR_HUMAN_REVIEW state.*")
        p()
        p("Next steps to advance candidates:")
        for e in portfolio.entries[:3]:
            p(f"- **{e.spec_name}**: {e.next_action}")
    p()

    # Portfolio health
    p("## Portfolio Health")
    p()
    p(f"| Status | Count |")
    p(f"|--------|-------|")
    for status in _STATUS_ORDER:
        n = portfolio.health.get(status, 0)
        if n:
            label = _STATUS_LABEL.get(status, status)
            p(f"| {label} {status} | {n} |")
    p()

    # Archetype balance
    p("## Archetype Balance")
    p()
    p(f"| Archetype | Count |")
    p(f"|-----------|-------|")
    for arch_id, count in sorted(
        portfolio.archetype_counts.items(), key=lambda x: -x[1]
    ):
        label = next(
            (e.archetype_label for e in portfolio.entries
             if e.archetype_id == arch_id),
            arch_id
        )
        sole = " *(sole representative)*" if count == 1 else ""
        p(f"| {label} | {count}{sole} |")
    p()

    # Full ranked list
    p("## All Strategies (Ranked by Priority)")
    p()
    p("| # | Strategy | Status | Archetype | Blockers | Strengths | "
      "Verdict | Score | Priority | Next Action |")
    p("|---|----------|--------|-----------|----------|-----------|"
      "---------|-------|----------|-------------|")
    entries = portfolio.entries
    if top_n:
        entries = entries[:top_n]
    for i, e in enumerate(entries, 1):
        score_str = f"{e.composite_score:.1f}" if e.composite_score else "N/A"
        action    = e.next_action[:60] + ("..." if len(e.next_action) > 60 else "")
        p(f"| {i} | {e.spec_name} | {e.readiness_status} | "
          f"{e.archetype_label} | {e.blocker_count} | {e.strength_count} | "
          f"{e.comparison_verdict} | {score_str} | {e.priority_score:.1f} | "
          f"{action} |")
    p()

    # Stale / rejected
    if portfolio.stale:
        p("## Rejected Research Candidates")
        p()
        p("These strategies failed a critical gate and should be archived.")
        p()
        for e in portfolio.stale:
            p(f"- **{e.spec_name}** ({e.archetype_label}) -- {e.next_action}")
        p()

    p("---")
    p()
    p("*Read-only advisory output. No database writes. No strategy changes.*")
    p("*REVIEW\\_REQUIRED is the terminal automated state.*")
    p("*Accumulate evidence. Improve questions. Preserve authority.*")

    return "\n".join(lines)


def write_report(
    portfolio:   Portfolio,
    reports_dir: Path = REPORTS_DIR,
    top_n:       Optional[int] = None,
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = portfolio.generated_at[:10].replace("-", "")
    suffix    = f"_top{top_n}" if top_n else ""
    md_path   = reports_dir / f"portfolio{suffix}_{date_str}.md"
    json_path = reports_dir / f"portfolio{suffix}_{date_str}.json"

    md_path.write_text(
        _render_markdown(portfolio, top_n=top_n),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            {
                "generated_at":    portfolio.generated_at,
                "total_strategies": len(portfolio.entries),
                "health":          portfolio.health,
                "archetype_counts": portfolio.archetype_counts,
                "attention_queue_count": len(portfolio.attention_queue),
                "entries": [
                    {
                        "spec_id":           e.spec_id,
                        "spec_name":         e.spec_name,
                        "symbol":            e.symbol,
                        "timeframe":         e.timeframe,
                        "readiness_status":  e.readiness_status,
                        "composite_score":   e.composite_score,
                        "blocker_count":     e.blocker_count,
                        "warning_count":     e.warning_count,
                        "strength_count":    e.strength_count,
                        "archetype_id":      e.archetype_id,
                        "archetype_label":   e.archetype_label,
                        "comparison_verdict": e.comparison_verdict,
                        "priority_score":    e.priority_score,
                        "next_action":       e.next_action,
                    }
                    for e in portfolio.entries
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_portfolio(portfolio: Portfolio, top_n: Optional[int] = None) -> None:
    total = len(portfolio.entries)
    ready = len(portfolio.attention_queue)

    print(f"Research Portfolio  |  {total} strategies  |  {ready} ready for review")
    print()

    # Attention queue
    queue = portfolio.attention_queue[:top_n] if top_n else portfolio.attention_queue
    if queue:
        print("  ATTENTION QUEUE  (REVIEW_REQUIRED)")
        print()
        for i, e in enumerate(queue, 1):
            score_str = f"{e.composite_score:.1f}" if e.composite_score else "N/A"
            print(f"  {i:>2}. {e.spec_name:<35}  "
                  f"{e.archetype_label:<20}  "
                  f"{e.comparison_verdict:<14}  "
                  f"score={score_str}  priority={e.priority_score:.1f}")
        print()
    else:
        print("  No strategies in REVIEW_REQUIRED state.")
        print()

    # Health summary
    print("  PORTFOLIO HEALTH")
    print()
    for status in _STATUS_ORDER:
        n = portfolio.health.get(status, 0)
        if n:
            label = _STATUS_LABEL.get(status, status)
            print(f"  {label}  {n:>3}  {status}")
    print()

    # Archetype summary
    print("  ARCHETYPE BALANCE")
    print()
    for arch_id, count in sorted(
        portfolio.archetype_counts.items(), key=lambda x: -x[1]
    ):
        label = next(
            (e.archetype_label for e in portfolio.entries
             if e.archetype_id == arch_id),
            arch_id,
        )
        sole = "  (sole)" if count == 1 else ""
        print(f"    {label:<25}  {count}{sole}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Research Portfolio Constructor (Phase 30). "
            "Question: Among all candidates, where should human attention be focused? "
            "No DB writes. No strategy changes. REVIEW_REQUIRED is terminal."
        )
    )
    parser.add_argument("--all",      action="store_true",
                        help="Build full portfolio view")
    parser.add_argument("--top",      type=int, metavar="N",
                        help="Show top N candidates by priority")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Console output only -- no files written")
    parser.add_argument("--db",       default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR), metavar="DIR")
    args = parser.parse_args()

    if not args.all and not args.top:
        parser.error("Specify --all or --top N")

    db_path     = Path(args.db)
    reports_dir = Path(args.reports_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes Research Portfolio Constructor  [{mode}]")
    print(f"  DB       : {db_path}")
    if not args.dry_run:
        print(f"  Reports  : {reports_dir}")
    print(f"  Advisory only -- no DB writes, no strategy changes")
    print()

    conn = sqlite3.connect(str(db_path))
    try:
        portfolio = build_portfolio(conn)
    finally:
        conn.close()

    if not portfolio.entries:
        print("No scored strategies found.")
        sys.exit(0)

    _print_portfolio(portfolio, top_n=args.top)

    if not args.dry_run:
        md_path, json_path = write_report(portfolio, reports_dir, top_n=args.top)
        print(f"  Reports")
        print(f"    MD  : {md_path}")
        print(f"    JSON: {json_path}")
        print()
    else:
        print("DRY-RUN complete. No files written. No DB changes.")


if __name__ == "__main__":
    main()
