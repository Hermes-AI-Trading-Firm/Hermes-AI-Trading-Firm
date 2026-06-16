#!/usr/bin/env python3
"""
Research Memory & Pattern Library -- research/memory/pattern_library.py

Persistent cross-strategy knowledge base. Ingests completed learning reviews
and accumulates failure patterns, strength patterns, and blocker statistics
across all researched strategies.

What it does
------------
- Ingests learning review JSONs from reports/learning/
- Maintains a persistent pattern_library.json (committed to git -- this is
  research knowledge, not a generated report)
- Derives cross-strategy insights: which patterns recur, which blockers are
  most common, which strength combinations correlate with better readiness
- Writes a human-readable cross-strategy pattern report

What it does NOT do
-------------------
- Does not write to any database table
- Does not modify strategy specs, scores, or learning reviews
- Does not approve or reject strategies
- Does not change readiness status of any strategy
- Does not run any validation engine

Ingestion is idempotent: re-ingesting a spec_id replaces its prior record.

Usage
-----
    python -m research.memory.pattern_library --all
    python -m research.memory.pattern_library --spec-id N
    python -m research.memory.pattern_library --report
    python -m research.memory.pattern_library --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB       = _PROJECT_ROOT / "database" / "hermes_research.db"
LIBRARY_PATH     = _PROJECT_ROOT / "research"  / "memory" / "pattern_library.json"
PRIORS_PATH      = _PROJECT_ROOT / "research"  / "memory" / "strategy_type_priors.json"
LEARNING_DIR     = _PROJECT_ROOT / "reports"   / "learning"
REPORTS_DIR      = _PROJECT_ROOT / "reports"   / "patterns"

_LIBRARY_VERSION = "1"

# ---------------------------------------------------------------------------
# Strength classifier
# Matches the known strength string prefixes from research/learning/prompts.py
# ---------------------------------------------------------------------------

_STRENGTH_PREFIXES: List[Tuple[str, str]] = [
    ("Monte Carlo PASS",        "mc_pass"),
    ("High probability positive","high_prob_positive"),
    ("Walk-forward PASS",       "wf_pass"),
    ("Walk-forward WARNING",    "wf_warn_positive"),
    ("Composite score",         "high_composite_score"),
    ("Solid profit factor",     "strong_pf"),
    ("Win rate",                "good_win_rate"),
    ("Overfitting risk 0.00",   "low_overfit"),
    ("Low max drawdown",        "low_drawdown"),
    ("Audit passed",            "clean_audit"),
    ("Regime analysis shows",   "regime_consistent"),
]


def _classify_strength(s: str) -> str:
    for prefix, key in _STRENGTH_PREFIXES:
        if s.startswith(prefix):
            return key
    return re.sub(r"[^a-z0-9]", "_", s[:35].lower()).strip("_")


# ---------------------------------------------------------------------------
# Empty library skeleton
# ---------------------------------------------------------------------------

def _empty_library() -> Dict:
    return {
        "version":               _LIBRARY_VERSION,
        "last_updated":          None,
        "strategies_ingested":   0,
        "strategy_type_priors":  {},
        "strategy_records":      {},
        "failure_index":         {},
        "strength_index":        {},
        "action_index":          {},
        "readiness_index":       {},
        "category_index":        {},
    }


# ---------------------------------------------------------------------------
# PatternLibrary
# ---------------------------------------------------------------------------

class PatternLibrary:
    """Persistent cross-strategy research memory."""

    def __init__(self, library_path: Path = LIBRARY_PATH) -> None:
        self.path = library_path
        self.data = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> Dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return _empty_library()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.data["strategies_ingested"] = len(self.data["strategy_records"])
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(
        self,
        review: Dict,
        symbol:    str = "",
        timeframe: str = "",
    ) -> None:
        spec_id   = str(review["spec_id"])
        spec_name = review["spec_name"]

        # Remove stale record before replacing (idempotent)
        self._remove_spec(spec_id)

        failure_ids     = [f["pattern_id"] for f in review.get("failure_patterns", [])]
        strength_strings = review.get("strength_patterns", [])
        action_types    = [a["action_type"] for a in review.get("next_actions", [])]

        # Strategy record
        self.data["strategy_records"][spec_id] = {
            "spec_id":          int(spec_id),
            "spec_name":        spec_name,
            "symbol":           symbol,
            "timeframe":        timeframe,
            "readiness_status": review.get("readiness_status"),
            "ingested_at":      datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "failure_pattern_ids":  failure_ids,
            "failure_patterns":     review.get("failure_patterns", []),
            "strength_keys":        [_classify_strength(s) for s in strength_strings],
            "strength_patterns":    strength_strings,
            "action_types":         action_types,
        }

        # Rebuild all indexes from scratch (ensures correctness on re-ingest)
        self._rebuild_indexes()

    def _remove_spec(self, spec_id: str) -> None:
        self.data["strategy_records"].pop(spec_id, None)

    # ------------------------------------------------------------------
    # Strategy type priors
    # ------------------------------------------------------------------

    def ingest_priors(self, priors: Dict) -> int:
        """Merge strategy-type prior knowledge into the library. Returns count."""
        existing = self.data.setdefault("strategy_type_priors", {})
        for key, entry in priors.items():
            existing[key] = {
                "label":        entry.get("label", key),
                "observations": entry.get("observations", []),
                "updated_at":   datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
        return len(priors)

    def get_priors_for_type(self, strategy_type_key: str) -> Optional[Dict]:
        return self.data.get("strategy_type_priors", {}).get(strategy_type_key)

    def all_priors(self) -> Dict:
        return self.data.get("strategy_type_priors", {})

    def _rebuild_indexes(self) -> None:
        fi: Dict = {}   # failure_index
        si: Dict = {}   # strength_index
        ai: Dict = {}   # action_index
        ri: Dict = {}   # readiness_index
        ci: Dict = {}   # category_index

        for sid, rec in self.data["strategy_records"].items():
            iid = int(sid)

            # Failure index
            for fp in rec.get("failure_patterns", []):
                pid = fp["pattern_id"]
                if pid not in fi:
                    fi[pid] = {
                        "severity": fp["severity"],
                        "category": fp["category"],
                        "spec_ids": [],
                        "count":    0,
                    }
                if iid not in fi[pid]["spec_ids"]:
                    fi[pid]["spec_ids"].append(iid)
                    fi[pid]["count"] += 1

            # Category index
            for fp in rec.get("failure_patterns", []):
                cat = fp["category"]
                sev = fp["severity"]
                if cat not in ci:
                    ci[cat] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "total": 0}
                ci[cat][sev] += 1
                ci[cat]["total"] += 1

            # Strength index
            for key in rec.get("strength_keys", []):
                if key not in si:
                    si[key] = {"spec_ids": [], "count": 0}
                if iid not in si[key]["spec_ids"]:
                    si[key]["spec_ids"].append(iid)
                    si[key]["count"] += 1

            # Action index
            for at in rec.get("action_types", []):
                if at not in ai:
                    ai[at] = {"spec_ids": [], "count": 0}
                if iid not in ai[at]["spec_ids"]:
                    ai[at]["spec_ids"].append(iid)
                    ai[at]["count"] += 1

            # Readiness index
            rs = rec.get("readiness_status") or "unknown"
            if rs not in ri:
                ri[rs] = []
            if iid not in ri[rs]:
                ri[rs].append(iid)

        self.data["failure_index"]   = fi
        self.data["strength_index"]  = si
        self.data["action_index"]    = ai
        self.data["readiness_index"] = ri
        self.data["category_index"]  = ci

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def total(self) -> int:
        return len(self.data["strategy_records"])

    def top_failure_patterns(self, n: int = 10) -> List[Dict]:
        fi = self.data.get("failure_index", {})
        return sorted(fi.values(), key=lambda x: (-x["count"], x["severity"]))[:n]

    def top_failure_patterns_with_id(self, n: int = 10) -> List[Tuple[str, Dict]]:
        fi = self.data.get("failure_index", {})
        return sorted(fi.items(), key=lambda kv: (-kv[1]["count"], kv[1]["severity"]))[:n]

    def top_strengths(self, n: int = 10) -> List[Tuple[str, Dict]]:
        si = self.data.get("strength_index", {})
        return sorted(si.items(), key=lambda kv: -kv[1]["count"])[:n]

    def top_actions(self, n: int = 10) -> List[Tuple[str, Dict]]:
        ai = self.data.get("action_index", {})
        return sorted(ai.items(), key=lambda kv: -kv[1]["count"])[:n]

    def category_breakdown(self) -> Dict[str, Dict]:
        return self.data.get("category_index", {})

    def readiness_distribution(self) -> Dict[str, List[int]]:
        return self.data.get("readiness_index", {})

    def cross_strategy_insights(
        self, failure_pattern_ids: Optional[List[str]] = None
    ) -> List[str]:
        """
        Derive advisory insights from accumulated data.
        If failure_pattern_ids supplied, include per-pattern context.
        """
        insights: List[str] = []
        total = self.total()
        if total == 0:
            return ["No strategies ingested yet."]

        fi = self.data.get("failure_index", {})
        ri = self.data.get("readiness_index", {})
        ai = self.data.get("action_index", {})
        ci = self.data.get("category_index", {})

        # Most prevalent blockers (HIGH severity only)
        high_blockers = [
            (pid, d) for pid, d in fi.items()
            if d["severity"] == "HIGH" and d["count"] >= 2
        ]
        high_blockers.sort(key=lambda x: -x[1]["count"])
        for pid, d in high_blockers[:3]:
            pct = d["count"] / total * 100
            insights.append(
                f"{d['count']} of {total} strategies ({pct:.0f}%) share HIGH blocker "
                f"'{pid}' [{d['category']}]. This is a firm-wide gap."
            )

        # Most-requested action
        top_action = self.top_actions(1)
        if top_action:
            at, ad = top_action[0]
            pct = ad["count"] / total * 100
            insights.append(
                f"Most-needed action across the firm: '{at}' "
                f"({ad['count']} of {total} strategies, {pct:.0f}%)."
            )

        # Readiness spread
        nt8_blocked = len(ri.get("NEEDS_REAL_NT8_EXPORT", []))
        wf_blocked  = len(ri.get("NEEDS_WALK_FORWARD", []))
        if nt8_blocked:
            insights.append(
                f"{nt8_blocked} of {total} strategies are blocked on real NT8 export data. "
                "No statistical validation is possible until trade_list_json is populated."
            )
        if wf_blocked:
            insights.append(
                f"{wf_blocked} of {total} strategies have backtest data but no OOS validation. "
                "Walk-forward testing is the next priority gate for these strategies."
            )

        # Category with most failures
        if ci:
            worst_cat = max(ci.items(), key=lambda kv: kv[1]["total"])
            insights.append(
                f"Category with most failure flags firm-wide: '{worst_cat[0]}' "
                f"({worst_cat[1]['total']} occurrences across {total} strategies)."
            )

        # Strategies that cleared MC
        mc_pass_sids = self.data.get("strength_index", {}).get("mc_pass", {}).get("spec_ids", [])
        if mc_pass_sids:
            insights.append(
                f"{len(mc_pass_sids)} of {total} strategies passed Monte Carlo bootstrap validation: "
                f"spec_ids {mc_pass_sids}."
            )

        # Per-pattern context (for single-strategy advisory use)
        if failure_pattern_ids:
            for pid in failure_pattern_ids:
                if pid in fi:
                    others = [s for s in fi[pid]["spec_ids"]]
                    if len(others) >= 2:
                        insights.append(
                            f"Pattern '{pid}' seen in {len(others)} other strategies "
                            f"(spec_ids {others}). It is a recurring firm-wide issue."
                        )

        return insights

    def strategy_peer_comparison(self, spec_id: int) -> Optional[Dict]:
        """Return a brief comparison of this spec vs firm-wide averages."""
        rec = self.data["strategy_records"].get(str(spec_id))
        if not rec:
            return None
        total = self.total()
        my_failures  = len(rec.get("failure_pattern_ids", []))
        my_strengths = len(rec.get("strength_keys", []))

        all_failures  = [len(r.get("failure_pattern_ids", []))
                         for r in self.data["strategy_records"].values()]
        all_strengths = [len(r.get("strength_keys", []))
                         for r in self.data["strategy_records"].values()]

        avg_failures  = sum(all_failures)  / total if total else 0
        avg_strengths = sum(all_strengths) / total if total else 0

        return {
            "spec_id":        spec_id,
            "spec_name":      rec["spec_name"],
            "my_failures":    my_failures,
            "my_strengths":   my_strengths,
            "avg_failures":   round(avg_failures, 1),
            "avg_strengths":  round(avg_strengths, 1),
            "vs_avg_failures": my_failures  - avg_failures,
            "vs_avg_strengths": my_strengths - avg_strengths,
        }


# ---------------------------------------------------------------------------
# Ingestion helpers
# ---------------------------------------------------------------------------

def _fetch_spec_meta(conn: sqlite3.Connection, spec_id: int) -> Tuple[str, str]:
    row = conn.execute(
        "SELECT symbol, timeframe FROM strategy_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if not row:
        return "", ""
    return row[0] or "", row[1] or ""


def _latest_review_json(spec_id: int, spec_name: str) -> Optional[Dict]:
    safe    = re.sub(r"[^\w\-]", "_", spec_name)
    matches = sorted(LEARNING_DIR.glob(f"{safe}_learning_review_*.json"))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def ingest_review_for_spec(
    conn: sqlite3.Connection,
    library: PatternLibrary,
    spec_id: int,
) -> Tuple[bool, str]:
    row = conn.execute(
        "SELECT spec_name FROM strategy_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if not row:
        return False, f"spec_id={spec_id} not found in DB"

    spec_name = row[0]
    review    = _latest_review_json(spec_id, spec_name)
    if not review:
        return False, f"No learning review JSON found for {spec_name}"

    symbol, timeframe = _fetch_spec_meta(conn, spec_id)
    library.ingest(review, symbol=symbol, timeframe=timeframe)
    return True, spec_name


def ingest_all_reviews(
    conn: sqlite3.Connection, library: PatternLibrary
) -> Tuple[int, int]:
    """Ingest all learning review JSONs. Returns (ingested, skipped)."""
    spec_ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT spec_id FROM scoring_results ORDER BY spec_id"
    ).fetchall()]

    ingested = skipped = 0
    for sid in spec_ids:
        ok, msg = ingest_review_for_spec(conn, library, sid)
        if ok:
            ingested += 1
            print(f"  ingested: {msg}")
        else:
            skipped += 1
            print(f"  skip    : {msg}")
    return ingested, skipped


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render_markdown(library: PatternLibrary, dry_run: bool = False) -> str:
    lines: List[str] = []
    total = library.total()
    now   = datetime.now().strftime("%Y-%m-%d")

    def p(s: str = "") -> None:
        lines.append(s)

    p(f"# Hermes Research Pattern Library")
    p(f"**Generated:** {now}  |  **Strategies in library:** {total}")
    if dry_run:
        p()
        p("> **DRY-RUN** -- no files written")
    p()
    p("---")
    p()

    # ---- Readiness distribution
    p("## Readiness Distribution")
    p()
    rd = library.readiness_distribution()
    if rd:
        for status, sids in sorted(rd.items(), key=lambda kv: -len(kv[1])):
            pct = len(sids) / total * 100 if total else 0
            p(f"- **{status}** — {len(sids)} strategies ({pct:.0f}%)  "
              f"`spec_ids: {sids}`")
    else:
        p("*No data.*")
    p()

    # ---- Strategy type priors
    priors = library.all_priors()
    if priors:
        p("## Strategy Type Priors")
        p()
        p("*Historical research findings by strategy type. "
          "Manually maintained in `research/memory/strategy_type_priors.json`.*")
        p()
        for key, entry in sorted(priors.items()):
            p(f"### {entry['label']}")
            p()
            for obs in entry.get("observations", []):
                val  = obs.get("value")
                note = obs.get("note", "")
                if val is not None:
                    p(f"- **{obs['metric']}** = {val:.0%}  — {note}")
                else:
                    p(f"- **{obs['metric']}**  — {note}")
            p()

    # ---- Top failure patterns
    p("## Most Common Failure Patterns")
    p()
    top_f = library.top_failure_patterns_with_id(n=15)
    if top_f:
        p(f"| Pattern | Severity | Category | Count | % of Firm |")
        p(f"|---------|----------|----------|-------|-----------|")
        for pid, d in top_f:
            pct = d["count"] / total * 100 if total else 0
            p(f"| `{pid}` | {d['severity']} | {d['category']} "
              f"| {d['count']} | {pct:.0f}% |")
    else:
        p("*No failure patterns recorded.*")
    p()

    # ---- Category breakdown
    p("## Failure Category Breakdown")
    p()
    ci = library.category_breakdown()
    if ci:
        p(f"| Category | HIGH | MEDIUM | LOW | Total |")
        p(f"|----------|------|--------|-----|-------|")
        for cat, counts in sorted(ci.items(), key=lambda kv: -kv[1]["total"]):
            p(f"| {cat} | {counts['HIGH']} | {counts['MEDIUM']} "
              f"| {counts['LOW']} | {counts['total']} |")
    else:
        p("*No data.*")
    p()

    # ---- Top strengths
    p("## Most Common Strength Patterns")
    p()
    top_s = library.top_strengths(n=10)
    if top_s:
        p(f"| Strength Key | Count | % of Firm |")
        p(f"|-------------|-------|-----------|")
        for key, d in top_s:
            pct = d["count"] / total * 100 if total else 0
            p(f"| `{key}` | {d['count']} | {pct:.0f}% |")
    else:
        p("*No strength patterns recorded.*")
    p()

    # ---- Most-needed actions
    p("## Most-Needed Next Actions (Firm-Wide)")
    p()
    top_a = library.top_actions(n=8)
    if top_a:
        p(f"| Action Type | Count | % of Firm |")
        p(f"|-------------|-------|-----------|")
        for at, d in top_a:
            pct = d["count"] / total * 100 if total else 0
            p(f"| `{at}` | {d['count']} | {pct:.0f}% |")
    else:
        p("*No action data.*")
    p()

    # ---- Cross-strategy insights
    p("## Cross-Strategy Insights")
    p()
    insights = library.cross_strategy_insights()
    if insights:
        for ins in insights:
            p(f"- {ins}")
    else:
        p("*Insufficient data for cross-strategy insights.*")
    p()

    # ---- Per-strategy summary
    p("## Per-Strategy Summary")
    p()
    recs = library.data.get("strategy_records", {})
    if recs:
        max_name = max(len(r["spec_name"]) for r in recs.values())
        p(f"| Strategy | Symbol | HIGH | MED | LOW | Strengths | Readiness |")
        p(f"|----------|--------|------|-----|-----|-----------|-----------|")
        for sid, rec in sorted(recs.items(), key=lambda kv: int(kv[0])):
            fps  = rec.get("failure_patterns", [])
            high = sum(1 for f in fps if f["severity"] == "HIGH")
            med  = sum(1 for f in fps if f["severity"] == "MEDIUM")
            low  = sum(1 for f in fps if f["severity"] == "LOW")
            strs = len(rec.get("strength_keys", []))
            rs   = (rec.get("readiness_status") or "unknown")[:28]
            sym  = rec.get("symbol") or "-"
            p(f"| {rec['spec_name']} | {sym} | {high} | {med} | {low} | {strs} | {rs} |")
    else:
        p("*No strategies in library.*")
    p()

    p("---")
    p()
    p("*Read-only advisory output. No database writes. No strategy edits. "
      "Human approval required before any strategy advances past REVIEW\\_REQUIRED.*")

    return "\n".join(lines)


def write_report(
    library: PatternLibrary,
    reports_dir: Path = REPORTS_DIR,
    dry_run: bool = False,
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y%m%d")
    md_path   = reports_dir / f"pattern_report_{date_str}.md"
    json_path = reports_dir / f"pattern_report_{date_str}.json"

    md_path.write_text(_render_markdown(library, dry_run=dry_run), encoding="utf-8")
    json_path.write_text(
        json.dumps(library.data, indent=2), encoding="utf-8"
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_summary(library: PatternLibrary, dry_run: bool = False) -> None:
    total = library.total()
    tag   = "  [DRY-RUN]" if dry_run else ""
    print(f"Pattern Library{tag}")
    print(f"  Strategies : {total}")
    print(f"  Updated    : {library.data.get('last_updated') or 'not saved yet'}")
    print()

    rd = library.readiness_distribution()
    if rd:
        print("  Readiness distribution:")
        for status, sids in sorted(rd.items(), key=lambda kv: -len(kv[1])):
            print(f"    {len(sids):>2}  {status}")
        print()

    top_f = library.top_failure_patterns_with_id(n=8)
    if top_f:
        print("  Top failure patterns:")
        for pid, d in top_f:
            pct = d["count"] / total * 100 if total else 0
            sev_icon = {"HIGH": "[!]", "MEDIUM": "[~]", "LOW": "[-]"}.get(d["severity"], "[?]")
            print(f"    {sev_icon} {pid:<32}  {d['count']:>2} strategies  ({pct:.0f}%)")
        print()

    top_s = library.top_strengths(n=5)
    if top_s:
        print("  Top strength patterns:")
        for key, d in top_s:
            pct = d["count"] / total * 100 if total else 0
            print(f"    + {key:<32}  {d['count']:>2} strategies  ({pct:.0f}%)")
        print()

    priors = library.all_priors()
    if priors:
        print("  Strategy type priors:")
        for key, entry in sorted(priors.items()):
            print(f"    [{entry['label']}]")
            for obs in entry.get("observations", []):
                val  = obs.get("value")
                note = obs.get("note", "")
                if val is not None:
                    print(f"      {obs['metric']} = {val:.0%}  {note}")
                else:
                    print(f"      {obs['metric']}  {note}")
        print()

    insights = library.cross_strategy_insights()
    if insights:
        print("  Cross-strategy insights:")
        for ins in insights:
            words = ins.split()
            line  = ""
            for w in words:
                if len(line) + len(w) + 1 > 74:
                    print(f"    {line}")
                    line = w
                else:
                    line = (line + " " + w).strip()
            if line:
                print(f"    {line}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Research Memory & Pattern Library. "
            "Ingests learning reviews and accumulates cross-strategy knowledge. "
            "No DB writes. No strategy changes."
        )
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--all",          action="store_true",
                     help="Ingest all learning reviews + priors, write report")
    grp.add_argument("--spec-id",      type=int, metavar="ID",
                     help="Ingest one strategy and write report")
    grp.add_argument("--report",       action="store_true",
                     help="Write report from existing library (no ingestion)")
    grp.add_argument("--seed-priors",  action="store_true",
                     help="Load strategy_type_priors.json into library only")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Console output only -- no files written")
    parser.add_argument("--db",           default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--library-path", default=str(LIBRARY_PATH), metavar="PATH")
    parser.add_argument("--priors-path",  default=str(PRIORS_PATH),  metavar="PATH")
    parser.add_argument("--reports-dir",  default=str(REPORTS_DIR),  metavar="DIR")
    args = parser.parse_args()

    db_path      = Path(args.db)
    library_path = Path(args.library_path)
    priors_path  = Path(args.priors_path)
    reports_dir  = Path(args.reports_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes Research Memory  [{mode}]")
    print(f"  DB      : {db_path}")
    print(f"  Library : {library_path}")
    if not args.dry_run:
        print(f"  Reports : {reports_dir}")
    print(f"  Advisory only -- no DB writes, no strategy changes")
    print()

    library = PatternLibrary(library_path=library_path)
    conn    = sqlite3.connect(str(db_path))

    try:
        def _load_and_apply_priors(save: bool) -> None:
            if not priors_path.exists():
                print(f"  (no priors file at {priors_path})")
                return
            try:
                raw = json.loads(priors_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  WARNING: could not load priors file: {e}")
                return
            count = library.ingest_priors(raw)
            print(f"  Priors loaded: {count} strategy types from {priors_path.name}")
            if save and not args.dry_run:
                library.save()

        if args.report:
            print(f"Loaded library: {library.total()} strategies, "
                  f"{len(library.all_priors())} type priors")
            print()
        elif args.seed_priors:
            _load_and_apply_priors(save=True)
            if not args.dry_run:
                library.save()
                print(f"  Library saved: {library_path}")
            print()
        elif args.all:
            print("Ingesting all learning reviews...")
            ingested, skipped = ingest_all_reviews(conn, library)
            print()
            print(f"  Ingested: {ingested}  Skipped: {skipped}")
            _load_and_apply_priors(save=False)
            print()
            if not args.dry_run and (ingested or library.all_priors()):
                library.save()
                print(f"  Library saved: {library_path}")
                print()
        elif args.spec_id:
            ok, msg = ingest_review_for_spec(conn, library, args.spec_id)
            if ok:
                print(f"  Ingested: {msg}")
                if not args.dry_run:
                    library.save()
                    print(f"  Library saved: {library_path}")
            else:
                print(f"  ERROR: {msg}")
                sys.exit(1)
            print()

        _print_summary(library, dry_run=args.dry_run)

        if not args.dry_run:
            md_path, json_path = write_report(library, reports_dir=reports_dir)
            print(f"  Reports")
            print(f"    MD  : {md_path}")
            print(f"    JSON: {json_path}")
            print()
        else:
            print("DRY-RUN complete. No files written. Library not saved.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
