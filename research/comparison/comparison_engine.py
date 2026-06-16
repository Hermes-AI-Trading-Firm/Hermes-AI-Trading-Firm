#!/usr/bin/env python3
"""
Cross-Strategy Comparison Engine -- research/comparison/comparison_engine.py

Answers one question per strategy:

    "Is this strategy better than our existing alternatives?"

Not: "Is this strategy good?" -- that is the wrong question.

The comparison is archetype-aware. A VWAP Pullback is ranked against
other VWAP Pullbacks. An ORB is ranked against other ORBs. Firm-wide
rank is secondary context, not the primary verdict.

For archetypes with only one member, the strategy is compared against
the firm-wide distribution.

What it does
------------
- Loads scoring, backtest, and readiness data for all strategies
- Reads archetype classifications from research/archetype/classifications.json
- Ranks each strategy within its archetype group AND firm-wide
- Surfaces which strategies dominate, which are dominated, and which
  are the sole representative of their archetype
- Writes reports to reports/comparison/

What it does NOT do
-------------------
- Does not write to any database table
- Does not modify strategy specs, scores, or classifications
- Does not approve or reject strategies
- Does not move any strategy past REVIEW_REQUIRED

Usage
-----
    python -m research.comparison.comparison_engine --all
    python -m research.comparison.comparison_engine --archetype vwap_pullback
    python -m research.comparison.comparison_engine --spec-id N
    python -m research.comparison.comparison_engine --all --dry-run
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

DEFAULT_DB            = _PROJECT_ROOT / "database"  / "hermes_research.db"
CLASSIFICATIONS_PATH  = _PROJECT_ROOT / "research"  / "archetype" / "classifications.json"
DECISION_DIR          = _PROJECT_ROOT / "reports"   / "decision_packages"
REPORTS_DIR           = _PROJECT_ROOT / "reports"   / "comparison"

_METRICS = [
    ("composite_score",    "Composite Score",   "higher"),
    ("walk_forward_score", "Walk-Forward Score", "higher"),
    ("monte_carlo_score",  "MC Survival",        "higher"),
    ("win_rate",           "Win Rate",           "higher"),
    ("max_drawdown_pct",   "Max Drawdown",       "lower"),
    ("total_trades",       "Trade Count",        "higher"),
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class StrategyRow:
    spec_id:            int
    spec_name:          str
    symbol:             str
    timeframe:          str
    archetype_id:       str
    archetype_label:    str
    composite_score:    Optional[float]
    walk_forward_score: Optional[float]
    monte_carlo_score:  Optional[float]
    win_rate:           Optional[float]
    max_drawdown_pct:   Optional[float]
    total_trades:       Optional[int]
    readiness_status:   Optional[str]


@dataclass
class PeerPosition:
    metric:          str
    metric_label:    str
    value:           Optional[float]
    archetype_rank:  Optional[int]   # rank within archetype (1 = best)
    archetype_total: int             # total in archetype group
    firmwide_rank:   Optional[int]   # rank firm-wide
    firmwide_total:  int
    sole_archetype:  bool            # True if only member of archetype


@dataclass
class ComparisonResult:
    spec_id:          int
    spec_name:        str
    archetype_id:     str
    archetype_label:  str
    archetype_peers:  List[str]      # names of other strategies in same archetype
    positions:        List[PeerPosition]
    verdict:          str            # LEADS / COMPETITIVE / TRAILS / SOLE / INSUFFICIENT_DATA
    verdict_reason:   str
    generated_at:     str


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_all_strategies(conn: sqlite3.Connection) -> List[StrategyRow]:
    rows = conn.execute("""
        SELECT
            s.spec_id,
            s.spec_name,
            COALESCE(s.symbol,    '') AS symbol,
            COALESCE(s.timeframe, '') AS timeframe,
            sc.composite_score,
            sc.walk_forward_score,
            sc.monte_carlo_score,
            b.win_rate,
            b.max_drawdown_pct,
            b.total_trades
        FROM strategy_specs s
        JOIN scoring_results sc ON sc.spec_id = s.spec_id
            AND sc.scoring_id = (
                SELECT MAX(sc2.scoring_id)
                FROM scoring_results sc2
                WHERE sc2.spec_id = s.spec_id
            )
        LEFT JOIN backtests b ON b.spec_id = s.spec_id
            AND b.is_in_sample = 1
            AND b.backtest_id = (
                SELECT MAX(b2.backtest_id)
                FROM backtests b2
                WHERE b2.spec_id = s.spec_id AND b2.is_in_sample = 1
            )
        ORDER BY s.spec_id
    """).fetchall()

    return [
        StrategyRow(
            spec_id            = r[0],
            spec_name          = r[1],
            symbol             = r[2],
            timeframe          = r[3],
            archetype_id       = "",        # filled from classifications
            archetype_label    = "",
            composite_score    = r[4],
            walk_forward_score = r[5],
            monte_carlo_score  = r[6],
            win_rate           = r[7],
            max_drawdown_pct   = r[8],
            total_trades       = r[9],
            readiness_status   = None,      # filled from decision packages
        )
        for r in rows
    ]


def _attach_archetypes(
    strategies: List[StrategyRow],
    classifications: Dict,
) -> None:
    clsf = classifications.get("classifications", {})
    for s in strategies:
        rec = clsf.get(str(s.spec_id), {})
        s.archetype_id    = rec.get("primary",       "unknown")
        s.archetype_label = rec.get("primary_label", "Unknown")


def _attach_readiness(strategies: List[StrategyRow]) -> None:
    if not DECISION_DIR.exists():
        return
    for s in strategies:
        safe    = re.sub(r"[^\w\-]", "_", s.spec_name)
        matches = sorted(DECISION_DIR.glob(f"{safe}_decision_package_*.json"))
        if not matches:
            continue
        try:
            data = json.loads(matches[-1].read_text(encoding="utf-8"))
            s.readiness_status = data.get("readiness_status")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------

def _rank(
    strategies: List[StrategyRow],
    metric: str,
    direction: str,
) -> Dict[int, Optional[int]]:
    """Return {spec_id: rank} for a metric. None if value is None."""
    valued = [(s.spec_id, getattr(s, metric)) for s in strategies
              if getattr(s, metric) is not None]
    reverse = (direction == "higher")
    ranked  = sorted(valued, key=lambda x: x[1], reverse=reverse)
    result  = {spec_id: i + 1 for i, (spec_id, _) in enumerate(ranked)}
    for s in strategies:
        if s.spec_id not in result:
            result[s.spec_id] = None
    return result


def _compute_positions(
    target: StrategyRow,
    archetype_group: List[StrategyRow],
    all_strategies: List[StrategyRow],
) -> List[PeerPosition]:
    sole = len(archetype_group) == 1
    positions: List[PeerPosition] = []

    for metric, label, direction in _METRICS:
        value = getattr(target, metric)

        arch_rank = None
        if not sole and value is not None:
            arch_ranks = _rank(archetype_group, metric, direction)
            arch_rank  = arch_ranks.get(target.spec_id)

        fw_ranks   = _rank(all_strategies, metric, direction)
        fw_rank    = fw_ranks.get(target.spec_id)

        positions.append(PeerPosition(
            metric          = metric,
            metric_label    = label,
            value           = value,
            archetype_rank  = arch_rank,
            archetype_total = len(archetype_group),
            firmwide_rank   = fw_rank,
            firmwide_total  = len(all_strategies),
            sole_archetype  = sole,
        ))

    return positions


def _verdict(
    target: StrategyRow,
    positions: List[PeerPosition],
) -> Tuple[str, str]:
    """Return (verdict, reason)."""
    valued = [p for p in positions if p.value is not None]
    if not valued:
        return "INSUFFICIENT_DATA", "No scored metrics available for comparison."

    if all(p.sole_archetype for p in valued):
        return (
            "SOLE",
            f"Only {target.archetype_label} strategy in research. "
            "No archetype peers to compare against yet.",
        )

    # Score by archetype rank (lower is better, 1 = leads)
    arch_valued = [p for p in valued if p.archetype_rank is not None]
    if not arch_valued:
        return (
            "INSUFFICIENT_DATA",
            "Archetype group exists but metrics are missing for comparison.",
        )

    n       = len(arch_valued)
    avg_pct = sum(p.archetype_rank / p.archetype_total for p in arch_valued) / n

    if avg_pct <= 0.35:
        verdict = "LEADS"
        reason  = (
            f"Ranks in the top third of {target.archetype_label} strategies "
            f"across {n} comparable metrics."
        )
    elif avg_pct >= 0.65:
        verdict = "TRAILS"
        reason  = (
            f"Ranks in the bottom third of {target.archetype_label} strategies "
            f"across {n} comparable metrics. "
            "Existing alternatives are stronger -- consider prioritising peers."
        )
    else:
        verdict = "COMPETITIVE"
        reason  = (
            f"Mid-range among {target.archetype_label} strategies. "
            "Not clearly better or worse than existing alternatives."
        )

    return verdict, reason


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def compare_spec(
    target_id: int,
    all_strategies: List[StrategyRow],
) -> Tuple[Optional[ComparisonResult], str]:
    target = next((s for s in all_strategies if s.spec_id == target_id), None)
    if not target:
        return None, f"spec_id={target_id} not found in scored strategies"

    archetype_group = [s for s in all_strategies
                       if s.archetype_id == target.archetype_id]
    peers           = [s.spec_name for s in archetype_group
                       if s.spec_id != target_id]

    positions       = _compute_positions(target, archetype_group, all_strategies)
    verdict, reason = _verdict(target, positions)

    return ComparisonResult(
        spec_id         = target_id,
        spec_name       = target.spec_name,
        archetype_id    = target.archetype_id,
        archetype_label = target.archetype_label,
        archetype_peers = peers,
        positions       = positions,
        verdict         = verdict,
        verdict_reason  = reason,
        generated_at    = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    ), ""


def compare_all(
    all_strategies: List[StrategyRow],
) -> List[ComparisonResult]:
    results = []
    for s in all_strategies:
        r, _ = compare_spec(s.spec_id, all_strategies)
        if r:
            results.append(r)
    return results


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

_VERDICT_TAG = {
    "LEADS":             "[+]",
    "COMPETITIVE":       "[=]",
    "TRAILS":            "[-]",
    "SOLE":              "[S]",
    "INSUFFICIENT_DATA": "[?]",
}


def _fmt_val(metric: str, val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    if metric == "total_trades":
        return str(int(val))
    if metric in ("win_rate", "max_drawdown_pct", "monte_carlo_score"):
        return f"{val:.1%}"
    return f"{val:.2f}"


def _render_markdown(
    results: List[ComparisonResult],
    all_strategies: List[StrategyRow],
    archetype_filter: Optional[str] = None,
) -> str:
    lines: List[str] = []
    total = len(all_strategies)

    def p(s: str = "") -> None:
        lines.append(s)

    title = "Cross-Strategy Comparison"
    if archetype_filter:
        title += f" -- {archetype_filter}"
    p(f"# {title}")
    p(f"**Generated:** {datetime.now().strftime('%Y-%m-%d')}  |  "
      f"**Strategies:** {total}")
    p()
    p("> The question is not 'Is this strategy good?' "
      "It is 'Is this strategy better than our existing alternatives?'")
    p()
    p("---")
    p()

    # Verdict summary
    p("## Verdict Summary")
    p()
    p("| Strategy | Archetype | Verdict | Reason |")
    p("|----------|-----------|---------|--------|")
    for r in results:
        tag = _VERDICT_TAG.get(r.verdict, "[?]")
        p(f"| {r.spec_name} | {r.archetype_label} | {tag} {r.verdict} "
          f"| {r.verdict_reason[:80]}{'...' if len(r.verdict_reason) > 80 else ''} |")
    p()

    # Firm-wide metric rankings
    p("## Firm-Wide Rankings")
    p()
    strat_names = [s.spec_name for s in sorted(all_strategies, key=lambda s: s.spec_id)]
    col_w = max(len(n) for n in strat_names) if strat_names else 30

    for metric, label, direction in _METRICS:
        fw_ranks = _rank(all_strategies, metric, direction)
        ranked_strats = sorted(
            all_strategies,
            key=lambda s: (fw_ranks.get(s.spec_id) or 9999)
        )
        p(f"### {label} {'(lower is better)' if direction == 'lower' else ''}")
        p()
        p(f"| Rank | Strategy | Value | Archetype |")
        p(f"|------|----------|-------|-----------|")
        for s in ranked_strats:
            rank = fw_ranks.get(s.spec_id)
            val  = _fmt_val(metric, getattr(s, metric))
            if rank is None:
                continue
            p(f"| {rank} | {s.spec_name} | {val} | {s.archetype_label} |")
        p()

    # Per-strategy detail
    p("## Per-Strategy Comparison Detail")
    p()
    for r in results:
        tag = _VERDICT_TAG.get(r.verdict, "[?]")
        p(f"### {r.spec_name}")
        p(f"**Archetype:** {r.archetype_label}  |  "
          f"**Verdict:** {tag} {r.verdict}")
        p()
        p(f"> {r.verdict_reason}")
        p()
        if r.archetype_peers:
            p(f"**Archetype peers:** {', '.join(r.archetype_peers)}")
        else:
            p("**Archetype peers:** (none -- sole representative)")
        p()

        # Metric table
        p("| Metric | Value | Archetype Rank | Firm-Wide Rank |")
        p("|--------|-------|----------------|----------------|")
        for pos in r.positions:
            val  = _fmt_val(pos.metric, pos.value)
            if pos.sole_archetype or pos.archetype_rank is None:
                arch = "sole" if pos.sole_archetype else "N/A"
            else:
                arch = f"{pos.archetype_rank} of {pos.archetype_total}"
            fw = (f"{pos.firmwide_rank} of {pos.firmwide_total}"
                  if pos.firmwide_rank else "N/A")
            p(f"| {pos.metric_label} | {val} | {arch} | {fw} |")
        p()

    p("---")
    p()
    p("*Read-only advisory output. No database writes. No strategy edits. "
      "REVIEW\\_REQUIRED is the terminal automated state. Humans decide.*")
    return "\n".join(lines)


def write_report(
    results: List[ComparisonResult],
    all_strategies: List[StrategyRow],
    reports_dir: Path = REPORTS_DIR,
    archetype_filter: Optional[str] = None,
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y%m%d")
    suffix    = f"_{archetype_filter}" if archetype_filter else ""
    md_path   = reports_dir / f"comparison{suffix}_{date_str}.md"
    json_path = reports_dir / f"comparison{suffix}_{date_str}.json"

    md_path.write_text(
        _render_markdown(results, all_strategies, archetype_filter),
        encoding="utf-8"
    )
    json_path.write_text(
        json.dumps(
            {
                "generated_at":    datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "archetype_filter": archetype_filter,
                "strategies":      len(all_strategies),
                "results": [
                    {
                        "spec_id":         r.spec_id,
                        "spec_name":       r.spec_name,
                        "archetype_id":    r.archetype_id,
                        "archetype_label": r.archetype_label,
                        "archetype_peers": r.archetype_peers,
                        "verdict":         r.verdict,
                        "verdict_reason":  r.verdict_reason,
                        "positions": [
                            {
                                "metric":          p.metric,
                                "metric_label":    p.metric_label,
                                "value":           p.value,
                                "archetype_rank":  p.archetype_rank,
                                "archetype_total": p.archetype_total,
                                "firmwide_rank":   p.firmwide_rank,
                                "firmwide_total":  p.firmwide_total,
                                "sole_archetype":  p.sole_archetype,
                            }
                            for p in r.positions
                        ],
                    }
                    for r in results
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

def _print_result(r: ComparisonResult, dry_run: bool = False) -> None:
    tag    = _VERDICT_TAG.get(r.verdict, "[?]")
    dr_tag = "  [DRY-RUN]" if dry_run else ""
    peers  = ", ".join(r.archetype_peers) if r.archetype_peers else "(none)"

    print(f"COMPARE: {r.spec_name}  [spec_id={r.spec_id}]{dr_tag}")
    print(f"  Archetype : {r.archetype_label}")
    print(f"  Peers     : {peers}")
    print(f"  Verdict   : {tag} {r.verdict}")
    print(f"  Reason    : {r.verdict_reason}")
    print()
    print(f"  {'Metric':<22}  {'Value':>8}  {'Archetype':>12}  {'Firm-wide':>12}")
    print(f"  {'-'*22}  {'-'*8}  {'-'*12}  {'-'*12}")
    for pos in r.positions:
        val  = _fmt_val(pos.metric, pos.value)
        if pos.sole_archetype or pos.archetype_rank is None:
            arch = "sole" if pos.sole_archetype else "N/A"
        else:
            arch = f"{pos.archetype_rank}/{pos.archetype_total}"
        fw = f"{pos.firmwide_rank}/{pos.firmwide_total}" if pos.firmwide_rank else "N/A"
        print(f"  {pos.metric_label:<22}  {val:>8}  {arch:>12}  {fw:>12}")
    print()


def _print_summary(results: List[ComparisonResult]) -> None:
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    print("Comparison Summary")
    print()
    for verdict, tag in _VERDICT_TAG.items():
        n = counts.get(verdict, 0)
        if n:
            print(f"  {tag} {verdict:<20}  {n} strategies")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-Strategy Comparison Engine. "
            "Asks: is this strategy better than our existing alternatives? "
            "No DB writes. No strategy changes."
        )
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--all",       action="store_true",
                     help="Compare all scored strategies")
    grp.add_argument("--archetype", metavar="KEY",
                     help="Compare only strategies of this archetype")
    grp.add_argument("--spec-id",   type=int, metavar="ID",
                     help="Compare one strategy against its peers")
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

    if not CLASSIFICATIONS_PATH.exists():
        print(f"ERROR: No archetype classifications found at {CLASSIFICATIONS_PATH}")
        print("Run: python -m research.archetype.archetype_classifier --all")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes Cross-Strategy Comparison  [{mode}]")
    print(f"  DB          : {db_path}")
    print(f"  Archetypes  : {CLASSIFICATIONS_PATH}")
    if not args.dry_run:
        print(f"  Reports     : {reports_dir}")
    print(f"  Advisory only -- no DB writes, no strategy changes")
    print()

    classifications = json.loads(
        CLASSIFICATIONS_PATH.read_text(encoding="utf-8")
    )

    conn = sqlite3.connect(str(db_path))
    try:
        all_strategies = _load_all_strategies(conn)
        _attach_archetypes(all_strategies, classifications)
        _attach_readiness(all_strategies)
    finally:
        conn.close()

    if not all_strategies:
        print("No scored strategies found.")
        sys.exit(0)

    # Filter and run
    archetype_filter: Optional[str] = None

    if args.spec_id:
        results = []
        r, err = compare_spec(args.spec_id, all_strategies)
        if r:
            results.append(r)
        else:
            print(f"ERROR: {err}")
            sys.exit(1)
    elif args.archetype:
        archetype_filter = args.archetype
        filtered = [s for s in all_strategies
                    if s.archetype_id == args.archetype]
        if not filtered:
            print(f"No strategies found with archetype '{args.archetype}'")
            sys.exit(1)
        results = compare_all(filtered)
        # Still rank against full firm for firmwide position
        for r in results:
            r.positions = _compute_positions(
                next(s for s in all_strategies if s.spec_id == r.spec_id),
                filtered,
                all_strategies,
            )
    else:
        results = compare_all(all_strategies)

    for r in results:
        _print_result(r, dry_run=args.dry_run)

    if len(results) > 1:
        _print_summary(results)

    if not args.dry_run:
        md_path, json_path = write_report(
            results, all_strategies, reports_dir,
            archetype_filter=archetype_filter,
        )
        print(f"  Reports")
        print(f"    MD  : {md_path}")
        print(f"    JSON: {json_path}")
        print()
    else:
        print("DRY-RUN complete. No files written. No DB changes.")


if __name__ == "__main__":
    main()
