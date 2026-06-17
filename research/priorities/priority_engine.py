#!/usr/bin/env python3
"""
Research Priority Engine -- research/priorities/priority_engine.py

Phase 34

Goal: Rank research work, not strategies.

The Three-Question Test:
  Can it produce better evidence?   YES -- tells you where to look next
  Can it ask a better question?     YES -- surfaces the highest-value gap across all strategies
  Does a human still decide?        YES -- produces a ranked agenda; humans act on it

The engine aggregates every unanswered question from Phase 33 across all
strategies and groups them by type. The output is a ranked research agenda:
which evidence gaps, if resolved first, produce the largest advance across
the entire portfolio.

What it does NOT do
-------------------
- Does not answer questions
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
    python -m research.priorities.priority_engine --all
    python -m research.priorities.priority_engine --all --dry-run
    python -m research.priorities.priority_engine --top 5
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from research.questions.question_engine import (
    QuestionContext,
    ResearchQuestion,
    collect_question_context,
    identify_unknowns,
    _all_scored_specs,
    CLASSIFICATION_PATH,
    PATTERN_LIB_PATH,
)

DEFAULT_DB   = _PROJECT_ROOT / "database" / "hermes_research.db"
REPORTS_DIR  = _PROJECT_ROOT / "reports"  / "priorities"

MAX_AUTOMATED_STATE = "REVIEW_REQUIRED"


# ---------------------------------------------------------------------------
# Effort and action tables (keyed by question_id prefix)
# ---------------------------------------------------------------------------

_EFFORT: Dict[str, str] = {
    "dq_no_backtest":        "HIGH",
    "dq_no_trade_list":      "HIGH",
    "dq_audit_fails":        "MEDIUM",
    "ss_below_minimum":      "HIGH",
    "ss_below_soft":         "HIGH",
    "oos_missing":           "MEDIUM",
    "oos_fail":              "HIGH",
    "oos_warn":              "MEDIUM",
    "mc_missing":            "LOW",
    "mc_fail":               "HIGH",
    "mc_warn":               "MEDIUM",
    "regime_missing":        "LOW",
    "regime_single_window":  "HIGH",
    "pf_drawdown":           "MEDIUM",
    "param_overfit":         "MEDIUM",
    "param_high_is_no_oos":  "MEDIUM",
    "exec_assumptions":      "LOW",
    "priority_stuck":        "MEDIUM",
}

_EFFORT_DEFAULT = "MEDIUM"

_ACTION: Dict[str, str] = {
    "dq_no_backtest":        "Re-import NT8 backtest with --initial-capital flag",
    "dq_no_trade_list":      "Re-import NT8 backtest with trade list enabled",
    "dq_audit_fails":        "python -m research.audit.strategy_auditor --spec-id N",
    "ss_below_minimum":      "Extend backtest period to collect 30+ trades; re-import",
    "ss_below_soft":         "Extend backtest period to collect 100+ trades; re-import",
    "oos_missing":           "Import OOS backtest (--oos flag), then run walk-forward engine",
    "oos_fail":              "Review IS/OOS periods for regime or overfitting differences",
    "oos_warn":              "Test additional OOS windows to confirm consistency",
    "mc_missing":            "python -m research.validation.monte_carlo --spec-id N",
    "mc_fail":               "Review drawdown concentration and trade count before re-running MC",
    "mc_warn":               "Collect more trades to improve bootstrap stability",
    "regime_missing":        "python -m research.regime.regime_analyzer --spec-id N",
    "regime_single_window":  "Extend backtest period to cover multiple distinct market regimes",
    "pf_drawdown":           "Add drawdown filter or tighten per-trade stop for prop-firm compliance",
    "param_overfit":         "Run parameter sweep; compare IS vs OOS sensitivity map",
    "param_high_is_no_oos":  "Import OOS backtest (--oos), then run walk-forward engine",
    "exec_assumptions":      "Review NT8 export for slippage and commission settings",
    "priority_stuck":        "Schedule the next concrete research step with a deadline",
}

_ACTION_DEFAULT = "Investigate and resolve before advancing this strategy"

# Map archetype-specific question_ids to a group key
def _group_key(question_id: str) -> str:
    if question_id.startswith("arch_") and question_id.endswith("_weakness"):
        # arch_orb_weakness, arch_vwap_pullback_weakness -> arch_<archetype>_weakness
        # Keep archetype in group key so different archetypes remain separate priorities
        return question_id
    return question_id


def _effort_for(question_id: str) -> str:
    key = _group_key(question_id)
    if key in _EFFORT:
        return _EFFORT[key]
    # arch weakness group: varies but generally LOW (education-level action)
    if key.startswith("arch_") and key.endswith("_weakness"):
        return "LOW"
    return _EFFORT_DEFAULT


def _action_for(question_id: str, affected_names: List[str]) -> str:
    key = _group_key(question_id)
    base = _ACTION.get(key)
    if base is None:
        if key.startswith("arch_") and key.endswith("_weakness"):
            return "Review archetype weakness documentation and apply known mitigations"
        return _ACTION_DEFAULT

    if len(affected_names) == 1:
        # Replace literal placeholder " N" only if it's a standalone token (not part of NT8)
        action = base.replace(" --spec-id N", f" --spec-id (for {affected_names[0]})")
        if " --spec-id N" not in base:
            action = base + f" ({affected_names[0]})"
        return action
    else:
        strat_list = ", ".join(affected_names[:3]) + ("..." if len(affected_names) > 3 else "")
        action = base.replace(" --spec-id N", " --spec-id N (for each strategy)")
        if " --spec-id N" not in base:
            action = base + f" -- for each: {strat_list}"
        else:
            action = action + f": {strat_list}"
        return action


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ResearchPriority:
    priority_id:            str
    level:                  str         # CRITICAL / HIGH / MEDIUM / LOW
    title:                  str
    description:            str
    question_type:          str         # the question_id group key
    category:               str         # question category (DATA_QUALITY, etc.)
    affected_strategies:    List[str]   # spec names
    affected_spec_ids:      List[int]
    archetype_impact:       List[str]   # archetype labels affected
    evidence_gap_pct:       float       # fraction of portfolio missing this (0.0-1.0)
    affects_review_required: bool
    effort:                 str         # LOW / MEDIUM / HIGH
    suggested_action:       str
    research_value:         float       # 0-125 composite score
    evidence_summary:       str
    sample_question:        str
    generated_at:           str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    )


@dataclass
class PriorityReport:
    total_strategies:       int
    total_open_questions:   int
    critical_count:         int
    high_count:             int
    medium_count:           int
    low_count:              int
    priorities:             List[ResearchPriority]
    generated_at:           str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    )


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def collect_open_questions(conn: sqlite3.Connection) -> Dict[int, List[ResearchQuestion]]:
    """Read all open questions from all scored strategies. No DB writes."""
    classifications = _load_json(CLASSIFICATION_PATH)
    pattern_lib     = _load_json(PATTERN_LIB_PATH)
    specs           = _all_scored_specs(conn)
    result: Dict[int, List[ResearchQuestion]] = {}
    for row in specs:
        spec_id = row["spec_id"]
        spec    = {
            "spec_id":   spec_id,
            "spec_name": row["spec_name"],
            "symbol":    row.get("symbol", "") or "",
            "timeframe": row.get("timeframe", "") or "",
        }
        try:
            ctx = collect_question_context(conn, spec, classifications, pattern_lib)
        except Exception:
            continue
        if ctx is None:
            continue
        qs = identify_unknowns(ctx)
        if qs:
            result[spec_id] = qs
    return result


def _archetype_label(spec_id: int, classifications: Optional[Dict] = None) -> str:
    """Look up archetype label for a spec_id from classifications dict."""
    if classifications is None:
        classifications = _load_json(CLASSIFICATION_PATH)
    clsf = classifications.get("classifications", {}).get(str(spec_id), {})
    return clsf.get("primary_label", clsf.get("primary", "Unknown"))


def estimate_research_value(
    max_priority: str,
    affects_rr: bool,
    n_strategies: int,
    effort: str,
) -> float:
    """
    Composite research value score (0-125).

    Dimensions:
      priority_score  -- base urgency of the question type
      gate_bonus      -- questions that block REVIEW_REQUIRED are worth more
      strategy_bonus  -- cross-portfolio impact multiplies value
      effort_penalty  -- high-effort work is discounted (not avoided, just ranked lower)
    """
    priority_score = {"HIGH": 60, "MEDIUM": 30, "LOW": 10}.get(max_priority, 10)
    gate_bonus     = 40 if affects_rr else 0
    strategy_bonus = min(25, (n_strategies - 1) * 8)
    effort_penalty = {"HIGH": 15, "MEDIUM": 7, "LOW": 0}.get(effort, 7)
    return float(priority_score + gate_bonus + strategy_bonus - effort_penalty)


def estimate_evidence_gap(n_affected: int, n_total: int) -> float:
    """Fraction of strategies missing this evidence type (0.0-1.0)."""
    if n_total == 0:
        return 0.0
    return round(n_affected / n_total, 2)


def estimate_portfolio_impact(
    n_affected: int,
    n_total: int,
    affects_rr: bool,
) -> str:
    pct = n_affected / max(n_total, 1)
    if affects_rr and pct >= 0.5:
        return "PORTFOLIO-WIDE -- REVIEW_REQUIRED blocked for majority"
    if affects_rr and n_affected >= 2:
        return f"MULTI-STRATEGY -- REVIEW_REQUIRED blocked for {n_affected} strategies"
    if affects_rr:
        return "SINGLE-STRATEGY -- REVIEW_REQUIRED blocked"
    if pct >= 0.5:
        return f"PORTFOLIO-WIDE -- affects {n_affected} of {n_total} strategies"
    if n_affected >= 2:
        return f"MULTI-STRATEGY -- affects {n_affected} strategies"
    return "SINGLE-STRATEGY"


def _assign_level(research_value: float) -> str:
    if research_value >= 85:
        return "CRITICAL"
    if research_value >= 60:
        return "HIGH"
    if research_value >= 35:
        return "MEDIUM"
    return "LOW"


def _build_title(question_type: str, n_strategies: int, category: str) -> str:
    label_map = {
        "dq_no_backtest":        "Import missing backtests",
        "dq_no_trade_list":      "Re-export backtests with trade list",
        "dq_audit_fails":        "Resolve audit failures",
        "ss_below_minimum":      "Extend backtest for minimum trade count",
        "ss_below_soft":         "Extend backtest for statistical confidence",
        "oos_missing":           "Add out-of-sample validation",
        "oos_fail":              "Investigate OOS degradation",
        "oos_warn":              "Test additional OOS windows",
        "mc_missing":            "Run Monte Carlo validation",
        "mc_fail":               "Investigate Monte Carlo failure",
        "mc_warn":               "Strengthen Monte Carlo robustness",
        "regime_missing":        "Run regime analysis",
        "regime_single_window":  "Extend regime coverage",
        "pf_drawdown":           "Resolve prop-firm drawdown breach",
        "param_overfit":         "Investigate parameter overfit",
        "param_high_is_no_oos":  "Resolve OOS gap causing overfit flag",
        "exec_assumptions":      "Validate execution assumptions",
        "priority_stuck":        "Unblock stalled strategy",
    }
    if question_type in label_map:
        title = label_map[question_type]
    elif question_type.startswith("arch_") and question_type.endswith("_weakness"):
        arch_part = question_type[5:-9].replace("_", " ").title()
        title = f"Address {arch_part} archetype weakness"
    else:
        title = f"Resolve {question_type.replace('_', ' ')}"

    suffix = f" ({n_strategies} strategies)" if n_strategies > 1 else ""
    return title + suffix


def rank_research_priorities(conn: sqlite3.Connection) -> PriorityReport:
    """
    Aggregate all open questions across all strategies and produce a ranked
    research agenda. Groups questions by type so that the same evidence gap
    appearing in multiple strategies becomes a single cross-portfolio priority.

    Read-only. No DB writes. No strategy state changes.
    """
    all_questions   = collect_open_questions(conn)
    total_strategies = max(len(_all_scored_specs(conn)), 1)
    total_qs        = sum(len(v) for v in all_questions.values())

    # Build lookup: spec_id -> spec_name, archetype_label
    specs           = _all_scored_specs(conn)
    classifications = _load_json(CLASSIFICATION_PATH)
    spec_name_map   = {r["spec_id"]: r["spec_name"] for r in specs}
    arch_label_map  = {r["spec_id"]: _archetype_label(r["spec_id"], classifications) for r in specs}

    # Group by question_id -> list of ResearchQuestion (one per strategy)
    groups: Dict[str, List[ResearchQuestion]] = {}
    for spec_id, qs in all_questions.items():
        for q in qs:
            key = _group_key(q.question_id)
            groups.setdefault(key, []).append(q)

    priorities: List[ResearchPriority] = []

    for question_type, question_list in groups.items():
        # Deduplicate by spec_id (keep the highest-priority question per spec)
        by_spec: Dict[int, ResearchQuestion] = {}
        _rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        for q in question_list:
            sid = q.spec_id
            if sid is None:
                continue
            if sid not in by_spec or _rank.get(q.priority, 2) < _rank.get(by_spec[sid].priority, 2):
                by_spec[sid] = q

        unique_qs   = list(by_spec.values())
        if not unique_qs:
            continue

        spec_ids    = [q.spec_id for q in unique_qs]
        spec_names  = [spec_name_map.get(sid, f"spec_{sid}") for sid in spec_ids]
        arch_labels = sorted(set(arch_label_map.get(sid, "Unknown") for sid in spec_ids))

        max_priority = min(
            unique_qs, key=lambda q: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(q.priority, 2)
        ).priority
        affects_rr   = any(q.affects_review_required for q in unique_qs)
        n_affected   = len(unique_qs)
        effort       = _effort_for(question_type)
        category     = unique_qs[0].category

        rv           = estimate_research_value(max_priority, affects_rr, n_affected, effort)
        level        = _assign_level(rv)
        gap_pct      = estimate_evidence_gap(n_affected, total_strategies)
        impact_str   = estimate_portfolio_impact(n_affected, total_strategies, affects_rr)

        # Use the highest-priority question's text as the sample question
        best_q = min(unique_qs, key=lambda q: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(q.priority, 2))

        description = impact_str
        if affects_rr:
            description += " -- gate-level unknown"

        priorities.append(ResearchPriority(
            priority_id             = question_type,
            level                   = level,
            title                   = _build_title(question_type, n_affected, category),
            description             = description,
            question_type           = question_type,
            category                = category,
            affected_strategies     = spec_names,
            affected_spec_ids       = spec_ids,
            archetype_impact        = arch_labels,
            evidence_gap_pct        = gap_pct,
            affects_review_required = affects_rr,
            effort                  = effort,
            suggested_action        = _action_for(question_type, spec_names),
            research_value          = rv,
            evidence_summary        = best_q.evidence_behind_it,
            sample_question         = best_q.question,
        ))

    priorities.sort(key=lambda p: (-p.research_value, p.question_type))

    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for p in priorities:
        counts[p.level] = counts.get(p.level, 0) + 1

    return PriorityReport(
        total_strategies = total_strategies,
        total_open_questions = total_qs,
        critical_count   = counts["CRITICAL"],
        high_count       = counts["HIGH"],
        medium_count     = counts["MEDIUM"],
        low_count        = counts["LOW"],
        priorities       = priorities,
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

_LEVEL_HEADER = {
    "CRITICAL": "CRITICAL PRIORITY",
    "HIGH":     "HIGH PRIORITY",
    "MEDIUM":   "MEDIUM PRIORITY",
    "LOW":      "LOW PRIORITY",
}


def generate_priority_report(report: PriorityReport, top: Optional[int] = None) -> str:
    """Render the priority report as Markdown (ASCII-safe for Windows cp1252)."""
    lines: List[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines.append("# Research Priority Report")
    lines.append(f"Generated: {now}")
    lines.append("")
    lines.append("Goal: Rank research work, not strategies.")
    lines.append("")
    lines.append("The highest-value evidence gap, resolved first, advances")
    lines.append("the most strategies toward REVIEW_REQUIRED.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary
    lines.append("## Portfolio Summary")
    lines.append(f"- Strategies in pipeline: {report.total_strategies}")
    lines.append(f"- Open research questions: {report.total_open_questions}")
    lines.append(f"- CRITICAL priorities:     {report.critical_count}")
    lines.append(f"- HIGH priorities:         {report.high_count}")
    lines.append(f"- MEDIUM priorities:       {report.medium_count}")
    lines.append(f"- LOW priorities:          {report.low_count}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Ranked agenda
    display = report.priorities[:top] if top else report.priorities
    current_level = None

    for i, p in enumerate(display, 1):
        if p.level != current_level:
            current_level = p.level
            lines.append(f"## {_LEVEL_HEADER.get(p.level, p.level)}")
            lines.append("")

        lines.append(f"### {i}. {p.title}")
        lines.append(f"**Impact**: {p.description}")
        lines.append("")

        lines.append(f"| Field | Value |")
        lines.append(f"|-------|-------|")
        lines.append(f"| Research Value | {p.research_value:.0f} / 125 |")
        lines.append(f"| Priority Level | {p.level} |")
        lines.append(f"| Category | {p.category} |")
        lines.append(f"| Effort | {p.effort} |")
        lines.append(f"| Strategies Affected | {len(p.affected_strategies)} of {report.total_strategies} ({p.evidence_gap_pct*100:.0f}%) |")
        lines.append(f"| Gate Level (REVIEW_REQUIRED) | {'YES -- must resolve before gate' if p.affects_review_required else 'NO -- advisory improvement'} |")
        lines.append(f"| Archetypes Affected | {', '.join(p.archetype_impact) if p.archetype_impact else 'N/A'} |")
        lines.append("")

        lines.append("**Strategies:**")
        for sid, sname in zip(p.affected_spec_ids, p.affected_strategies):
            lines.append(f"- [{sid}] {sname}")
        lines.append("")

        lines.append("**Sample question:**")
        lines.append(f"> {p.sample_question}")
        lines.append("")

        lines.append("**What we know:**")
        lines.append(p.evidence_summary)
        lines.append("")

        lines.append("**Suggested action:**")
        lines.append(f"```")
        lines.append(p.suggested_action)
        lines.append(f"```")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("")
    lines.append("*Read-only research output. No strategies have been approved,*")
    lines.append("*rejected, or advanced beyond REVIEW_REQUIRED.*")
    lines.append("")
    lines.append(f"Maximum automated state: {MAX_AUTOMATED_STATE}")
    lines.append("Knowledge accumulates. Recommendations improve. Authority does not.")

    return "\n".join(lines)


def _report_to_dict(report: PriorityReport) -> Dict:
    return {
        "generated_at":       report.generated_at,
        "max_automated_state": MAX_AUTOMATED_STATE,
        "summary": {
            "total_strategies":    report.total_strategies,
            "total_open_questions": report.total_open_questions,
            "critical_count":      report.critical_count,
            "high_count":          report.high_count,
            "medium_count":        report.medium_count,
            "low_count":           report.low_count,
        },
        "priorities": [
            {
                "priority_id":             p.priority_id,
                "level":                   p.level,
                "title":                   p.title,
                "description":             p.description,
                "question_type":           p.question_type,
                "category":                p.category,
                "affected_strategies":     p.affected_strategies,
                "affected_spec_ids":       p.affected_spec_ids,
                "archetype_impact":        p.archetype_impact,
                "evidence_gap_pct":        p.evidence_gap_pct,
                "affects_review_required": p.affects_review_required,
                "effort":                  p.effort,
                "suggested_action":        p.suggested_action,
                "research_value":          p.research_value,
                "evidence_summary":        p.evidence_summary,
                "sample_question":         p.sample_question,
                "generated_at":            p.generated_at,
            }
            for p in report.priorities
        ],
    }


def write_reports(
    report: PriorityReport,
    reports_dir: Path = REPORTS_DIR,
    top: Optional[int] = None,
) -> Tuple[Path, Path]:
    """Write MD and JSON reports. Returns (md_path, json_path)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now().strftime("%Y%m%d")
    md_path  = reports_dir / f"research_priorities_{date_tag}.md"
    json_path = reports_dir / f"research_priorities_{date_tag}.json"

    md_path.write_text(
        generate_priority_report(report, top=top),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(_report_to_dict(report), indent=2),
        encoding="utf-8",
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_console(report: PriorityReport, top: int) -> None:
    display   = report.priorities[:top]
    now       = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\nResearch Priority Engine -- Phase 34")
    print(f"Generated: {now}")
    print(f"Goal: Rank research work, not strategies.")
    print("")
    print(f"Portfolio: {report.total_strategies} strategies, {report.total_open_questions} open questions")
    print(f"  CRITICAL: {report.critical_count}  HIGH: {report.high_count}  "
          f"MEDIUM: {report.medium_count}  LOW: {report.low_count}")
    print("")
    print(f"Top {min(top, len(display))} Research Priorities")
    print("=" * 60)

    current_level = None
    for i, p in enumerate(display, 1):
        if p.level != current_level:
            current_level = p.level
            print(f"\n[ {p.level} ]")

        strats = ", ".join(p.affected_strategies[:3])
        if len(p.affected_strategies) > 3:
            strats += f" +{len(p.affected_strategies)-3} more"

        print(f"\n  {i}. {p.title}")
        print(f"     Value: {p.research_value:.0f}/125  |  Effort: {p.effort}  |"
              f"  Gate: {'YES' if p.affects_review_required else 'no'}")
        print(f"     Strategies: {strats}")
        print(f"     Action: {p.suggested_action[:80]}{'...' if len(p.suggested_action)>80 else ''}")

    print("")
    print("-" * 60)
    print("Read-only output. No DB writes. No strategy state changes.")
    print(f"Max automated state: {MAX_AUTOMATED_STATE}")
    print("")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research Priority Engine (Phase 34) -- Rank research work, not strategies"
    )
    parser.add_argument("--all",     action="store_true", help="Rank all open questions across all strategies")
    parser.add_argument("--top",     type=int, default=10, help="Show top N priorities (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Console output only; write no files")
    parser.add_argument("--db",      default=str(DEFAULT_DB), help="Path to SQLite database")
    args = parser.parse_args()

    if not args.all and not args.dry_run:
        parser.print_help()
        sys.exit(0)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        report = rank_research_priorities(conn)
    finally:
        conn.close()

    _print_console(report, top=args.top)

    if not args.dry_run:
        md_path, json_path = write_reports(report, top=args.top)
        print(f"Reports written:")
        print(f"  {md_path}")
        print(f"  {json_path}")
    else:
        print("[dry-run] No files written.")


if __name__ == "__main__":
    main()
