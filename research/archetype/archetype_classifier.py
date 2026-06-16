#!/usr/bin/env python3
"""
Strategy Archetype Classifier -- research/archetype/archetype_classifier.py

Automatically classifies strategies into one of 10 archetypes using
keyword matching against spec metadata. Classifications are stored in
research/archetype/classifications.json (committed to git).

Archetypes
----------
    ORB                 Opening Range Breakout
    VWAP Pullback       VWAP-anchored pullback
    Mean Reversion      Statistical reversion to mean
    Trend Following     Directional momentum
    FVG Continuation    Fair Value Gap entry
    Liquidity Sweep     Stop hunt / liquidity grab
    Breakout            Level or range breakout
    Options Income      Premium selling / income
    Statistical Arb     Pairs / correlation arb
    Other               Unclassified or mixed

What it does
------------
- Reads strategy spec metadata from the database
- Scores each archetype by keyword match across text fields
- Assigns a primary archetype and zero or more secondary archetypes
- Looks up matching priors from strategy_type_priors.json
- Persists classifications in research/archetype/classifications.json

What it does NOT do
-------------------
- Does not write to any database table
- Does not modify strategy specs or scores
- Does not approve or reject strategies
- Does not run any validation engine

Classification is idempotent -- re-classifying a spec_id replaces
its prior classification.

Usage
-----
    python -m research.archetype.archetype_classifier --all
    python -m research.archetype.archetype_classifier --spec-id N
    python -m research.archetype.archetype_classifier --all --dry-run
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

DEFAULT_DB          = _PROJECT_ROOT / "database"  / "hermes_research.db"
ARCHETYPES_PATH     = _PROJECT_ROOT / "research"  / "archetype" / "archetypes.json"
CLASSIFICATIONS_PATH = _PROJECT_ROOT / "research" / "archetype" / "classifications.json"
PRIORS_PATH         = _PROJECT_ROOT / "research"  / "memory"    / "strategy_type_priors.json"
REPORTS_DIR         = _PROJECT_ROOT / "reports"   / "archetypes"

# Confidence thresholds
_PRIMARY_THRESHOLD   = 0.20   # minimum score to be assigned as primary
_SECONDARY_THRESHOLD = 0.12   # minimum score for a secondary archetype
_HYBRID_GAP          = 0.08   # if top two both exceed primary and are within this gap -> Hybrid

# Field weights: how much each text source contributes to the score
_FIELD_WEIGHTS = {
    "spec_name":   3,
    "description": 2,
    "entry_rules": 2,
    "exit_rules":  1,
    "notes":       1,
}
_MAX_SCORE = sum(_FIELD_WEIGHTS.values())   # 9


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ArchetypeMatch:
    archetype_id:   str
    label:          str
    confidence:     float
    matched_fields: List[str]
    matched_kws:    List[str]
    prior_key:      Optional[str]
    priors:         Optional[Dict]


@dataclass
class Classification:
    spec_id:            int
    spec_name:          str
    symbol:             str
    timeframe:          str
    primary:            ArchetypeMatch
    secondaries:        List[ArchetypeMatch]
    all_scores:         Dict[str, float]
    classified_at:      str


# ---------------------------------------------------------------------------
# Archetype definitions
# ---------------------------------------------------------------------------

def _load_archetypes(path: Path = ARCHETYPES_PATH) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_priors(path: Path = PRIORS_PATH) -> Dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Text corpus builder
# ---------------------------------------------------------------------------

def _fetch_spec_text(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, str]]:
    """Return available text fields for a spec. Gracefully handles missing columns."""
    # Base columns always present
    row = conn.execute(
        "SELECT spec_name, symbol, timeframe FROM strategy_specs WHERE spec_id = ?",
        (spec_id,)
    ).fetchone()
    if not row:
        return None

    corpus: Dict[str, str] = {
        "spec_name": row[0] or "",
        "symbol":    row[1] or "",
        "timeframe": row[2] or "",
    }

    # Optional columns -- query pragma to discover what exists
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(strategy_specs)"
    ).fetchall()}

    for col in ("description", "entry_rules", "exit_rules", "filters", "notes"):
        if col in cols:
            val = conn.execute(
                f"SELECT {col} FROM strategy_specs WHERE spec_id = ?", (spec_id,)
            ).fetchone()
            if val and val[0]:
                corpus[col] = val[0]

    # Pull research notes as supplementary text
    notes_rows = conn.execute(
        "SELECT content FROM research_notes WHERE spec_id = ? LIMIT 5", (spec_id,)
    ).fetchall()
    if notes_rows:
        corpus["notes"] = " ".join(r[0] for r in notes_rows if r[0])

    return corpus


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score(corpus: Dict[str, str], keywords: List[str]) -> Tuple[float, List[str], List[str]]:
    """
    Returns (confidence 0.0-1.0, matched_fields, matched_keywords).
    Each field is counted once per archetype regardless of how many keywords match.
    """
    matched_fields: List[str] = []
    matched_kws:    List[str] = []
    total = 0.0

    for field, text in corpus.items():
        if not text or field in ("symbol", "timeframe"):
            continue
        text_lower = text.lower()
        w = _FIELD_WEIGHTS.get(field, 1)
        for kw in keywords:
            if kw.lower() in text_lower:
                total += w
                matched_fields.append(field)
                if kw not in matched_kws:
                    matched_kws.append(kw)
                break  # one match per field per archetype

    confidence = min(total / _MAX_SCORE, 1.0) if _MAX_SCORE > 0 else 0.0
    return confidence, matched_fields, matched_kws


def classify_spec(
    conn: sqlite3.Connection,
    spec_id: int,
    archetypes: Dict,
    priors: Dict,
) -> Tuple[Optional[Classification], str]:
    corpus = _fetch_spec_text(conn, spec_id)
    if corpus is None:
        return None, f"spec_id={spec_id} not found"

    scores:   Dict[str, float]        = {}
    matches:  Dict[str, ArchetypeMatch] = {}

    for aid, adef in archetypes.items():
        if aid in ("hybrid", "unknown"):
            continue
        keywords = adef.get("keywords", [])
        conf, fields, kws = _score(corpus, keywords)
        scores[aid] = conf
        prior_key = adef.get("prior_key")
        matches[aid] = ArchetypeMatch(
            archetype_id  = aid,
            label         = adef["label"],
            confidence    = conf,
            matched_fields = fields,
            matched_kws   = kws,
            prior_key     = prior_key,
            priors        = priors.get(prior_key) if prior_key else None,
        )

    # Sort by confidence descending
    ranked = sorted(matches.values(), key=lambda m: -m.confidence)

    # Hybrid detection: top two both clear primary threshold within _HYBRID_GAP
    if (
        len(ranked) >= 2
        and ranked[0].confidence >= _PRIMARY_THRESHOLD
        and ranked[1].confidence >= _PRIMARY_THRESHOLD
        and (ranked[0].confidence - ranked[1].confidence) <= _HYBRID_GAP
    ):
        hybrid_def = archetypes["hybrid"]
        components = [ranked[0], ranked[1]]
        primary = ArchetypeMatch(
            archetype_id   = "hybrid",
            label          = hybrid_def["label"],
            confidence     = ranked[0].confidence,
            matched_fields = ranked[0].matched_fields + ranked[1].matched_fields,
            matched_kws    = ranked[0].matched_kws + ranked[1].matched_kws,
            prior_key      = None,
            priors         = None,
        )
        secondaries = components   # expose components as secondaries
    elif ranked and ranked[0].confidence >= _PRIMARY_THRESHOLD:
        primary = ranked[0]
        secondaries = [
            m for m in ranked[1:]
            if m.confidence >= _SECONDARY_THRESHOLD
        ]
    else:
        unknown_def = archetypes["unknown"]
        primary = ArchetypeMatch(
            archetype_id   = "unknown",
            label          = unknown_def["label"],
            confidence     = 0.0,
            matched_fields = [],
            matched_kws    = [],
            prior_key      = None,
            priors         = None,
        )
        secondaries = []

    scores["hybrid"]  = 0.0
    scores["unknown"] = 0.0 if primary.archetype_id != "unknown" else 1.0

    row = conn.execute(
        "SELECT symbol, timeframe FROM strategy_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()

    return Classification(
        spec_id       = spec_id,
        spec_name     = corpus["spec_name"],
        symbol        = (row[0] if row else "") or "",
        timeframe     = (row[1] if row else "") or "",
        primary       = primary,
        secondaries   = secondaries,
        all_scores    = scores,
        classified_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    ), ""


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_classifications(path: Path = CLASSIFICATIONS_PATH) -> Dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": "1", "last_updated": None, "classifications": {}}


def _save_classifications(store: Dict, path: Path = CLASSIFICATIONS_PATH) -> None:
    store["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _store_classification(store: Dict, c: Classification) -> None:
    store["classifications"][str(c.spec_id)] = {
        "spec_id":       c.spec_id,
        "spec_name":     c.spec_name,
        "symbol":        c.symbol,
        "timeframe":     c.timeframe,
        "primary":       c.primary.archetype_id,
        "primary_label": c.primary.label,
        "primary_confidence": round(c.primary.confidence, 4),
        "secondaries":   [m.archetype_id for m in c.secondaries],
        "all_archetypes": (
            [c.primary.archetype_id] + [m.archetype_id for m in c.secondaries]
        ),
        "all_scores":    {k: round(v, 4) for k, v in c.all_scores.items()},
        "matched_kws":   c.primary.matched_kws,
        "prior_key":     c.primary.prior_key,
        "classified_at": c.classified_at,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _render_markdown(store: Dict, archetypes: Dict, priors: Dict) -> str:
    lines: List[str] = []
    clsf  = store.get("classifications", {})
    total = len(clsf)

    def p(s: str = "") -> None:
        lines.append(s)

    p("# Strategy Archetype Classifications")
    p(f"**Generated:** {datetime.now().strftime('%Y-%m-%d')}  |  "
      f"**Strategies classified:** {total}")
    p()
    p("---")
    p()

    # Distribution by primary archetype
    p("## Archetype Distribution")
    p()
    dist: Dict[str, List[str]] = {}
    for sid, rec in clsf.items():
        pa = rec["primary"]
        dist.setdefault(pa, []).append(rec["spec_name"])
    if dist:
        p("| Archetype | Count | Strategies |")
        p("|-----------|-------|------------|")
        for aid, names in sorted(dist.items(), key=lambda kv: -len(kv[1])):
            label = archetypes.get(aid, {}).get("label", aid)
            p(f"| {label} | {len(names)} | {', '.join(names)} |")
    else:
        p("*No classifications yet.*")
    p()

    # Per-strategy detail
    p("## Per-Strategy Classifications")
    p()
    for sid, rec in sorted(clsf.items(), key=lambda kv: int(kv[0])):
        conf = rec["primary_confidence"]
        p(f"### {rec['spec_name']}")
        p(f"**spec_id:** {rec['spec_id']}  |  "
          f"**Symbol:** {rec['symbol'] or '-'}  |  "
          f"**Timeframe:** {rec['timeframe'] or '-'}")
        p()
        p(f"**Primary archetype:** {rec['primary_label']} "
          f"(confidence {conf:.0%})")
        if rec["matched_kws"]:
            p(f"**Matched keywords:** {', '.join(f'`{k}`' for k in rec['matched_kws'])}")
        if rec["secondaries"]:
            sec_labels = [
                archetypes.get(s, {}).get("label", s) for s in rec["secondaries"]
            ]
            p(f"**Secondary archetypes:** {', '.join(sec_labels)}")

        # Attach priors if available
        prior_key = rec.get("prior_key")
        if prior_key and prior_key in priors:
            p()
            p(f"**Known priors for {rec['primary_label']}:**")
            for obs in priors[prior_key].get("observations", []):
                val  = obs.get("value")
                note = obs.get("note", "")
                if val is not None:
                    p(f"- {obs['metric']} = {val:.0%}  {note}")
                else:
                    p(f"- {obs['metric']}  {note}")
        p()

    # Score table
    p("## Confidence Score Matrix")
    p()
    archetype_ids = [a for a in archetypes if a != "other"]
    header = "| Strategy | " + " | ".join(
        archetypes[a]["label"] for a in archetype_ids
    ) + " | Primary |"
    sep = "|----------|" + "|".join("-" * max(len(archetypes[a]["label"]), 5)
                                     for a in archetype_ids) + "---------|"
    p(header)
    p(sep)
    for sid, rec in sorted(clsf.items(), key=lambda kv: int(kv[0])):
        scores = rec.get("all_scores", {})
        cells  = " | ".join(
            f"{scores.get(a, 0):.0%}" for a in archetype_ids
        )
        p(f"| {rec['spec_name']} | {cells} | {rec['primary_label']} |")
    p()

    p("---")
    p()
    p("*Read-only advisory output. No database writes. No strategy edits.*")
    return "\n".join(lines)


def write_report(
    store: Dict, archetypes: Dict, priors: Dict,
    reports_dir: Path = REPORTS_DIR
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y%m%d")
    md_path   = reports_dir / f"archetype_classifications_{date_str}.md"
    json_path = reports_dir / f"archetype_classifications_{date_str}.json"
    md_path.write_text(_render_markdown(store, archetypes, priors), encoding="utf-8")
    json_path.write_text(json.dumps(store, indent=2), encoding="utf-8")
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_classification(c: Classification, dry_run: bool = False) -> None:
    tag = "  [DRY-RUN]" if dry_run else ""
    sec = (", ".join(m.label for m in c.secondaries)) or "(none)"
    kws = (", ".join(c.primary.matched_kws)) or "(none)"
    print(f"ARCHETYPE: {c.spec_name}  [spec_id={c.spec_id}]{tag}")
    print(f"  Primary    : {c.primary.label}  ({c.primary.confidence:.0%} confidence)")
    print(f"  Secondary  : {sec}")
    print(f"  Keywords   : {kws}")
    if c.primary.priors:
        print(f"  Priors [{c.primary.label}]:")
        for obs in c.primary.priors.get("observations", []):
            val  = obs.get("value")
            note = obs.get("note", "")
            if val is not None:
                print(f"    {obs['metric']} = {val:.0%}  {note}")
            else:
                print(f"    {obs['metric']}  {note}")
    print()


def _print_summary(store: Dict, archetypes: Dict) -> None:
    clsf  = store.get("classifications", {})
    total = len(clsf)
    print(f"Archetype Classifier Summary  ({total} strategies)")
    print()
    dist: Dict[str, int] = {}
    for rec in clsf.values():
        dist[rec["primary"]] = dist.get(rec["primary"], 0) + 1
    if dist:
        for aid, count in sorted(dist.items(), key=lambda kv: -kv[1]):
            label = archetypes.get(aid, {}).get("label", aid)
            pct   = count / total * 100 if total else 0
            print(f"  {label:<24}  {count:>2} strategies  ({pct:.0f}%)")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Strategy Archetype Classifier. "
            "No DB writes. No strategy changes."
        )
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--all",     action="store_true",
                     help="Classify all scored strategies")
    grp.add_argument("--spec-id", type=int, metavar="ID",
                     help="Classify one strategy")
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

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes Archetype Classifier  [{mode}]")
    print(f"  DB         : {db_path}")
    print(f"  Archetypes : {ARCHETYPES_PATH}")
    if not args.dry_run:
        print(f"  Store      : {CLASSIFICATIONS_PATH}")
        print(f"  Reports    : {reports_dir}")
    print(f"  Advisory only -- no DB writes, no strategy changes")
    print()

    archetypes = _load_archetypes()
    priors     = _load_priors()
    store      = _load_classifications()
    conn       = sqlite3.connect(str(db_path))

    try:
        spec_ids = (
            [r[0] for r in conn.execute(
                "SELECT DISTINCT spec_id FROM scoring_results ORDER BY spec_id"
            ).fetchall()]
            if args.all else [args.spec_id]
        )

        classified: List[Classification] = []
        skipped:    List[Tuple[int, str]]  = []

        for sid in spec_ids:
            c, err = classify_spec(conn, sid, archetypes, priors)
            if c is None:
                skipped.append((sid, err))
                print(f"  SKIP spec_id={sid}: {err}")
                continue
            _print_classification(c, dry_run=args.dry_run)
            if not args.dry_run:
                _store_classification(store, c)
            classified.append(c)

        if not args.dry_run and classified:
            _save_classifications(store)
            print(f"  Classifications saved: {CLASSIFICATIONS_PATH}")
            print()

        if args.all and classified:
            # Build a temporary store for dry-run summary display
            display_store = store if not args.dry_run else {
                "classifications": {
                    str(c.spec_id): {
                        "primary": c.primary.archetype_id,
                        "spec_name": c.spec_name,
                    }
                    for c in classified
                }
            }
            _print_summary(display_store, archetypes)

        if not args.dry_run and classified:
            md_path, json_path = write_report(store, archetypes, priors, reports_dir)
            print(f"  Reports")
            print(f"    MD  : {md_path}")
            print(f"    JSON: {json_path}")
            print()

        if skipped:
            print(f"Skipped ({len(skipped)}):")
            for sid, reason in skipped:
                print(f"  spec_id={sid}: {reason}")
            print()

        if args.dry_run:
            print("DRY-RUN complete. No files written. No DB changes.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
