#!/usr/bin/env python3
"""
AI Learning Brain -- research/learning/learning_brain.py

Advisory-only read-only layer. Reviews completed research evidence and
generates improvement suggestions for each strategy.

What it does
------------
1. Collects all available evidence (DB + JSON report files)
2. Identifies failure patterns -- signals that warrant concern
3. Identifies strength patterns -- signals that support confidence
4. Suggests next research actions -- prioritised, actionable steps
5. Writes a learning review report to reports/learning/

What it does NOT do
-------------------
- Does not write to any database table
- Does not modify strategy specs or scores
- Does not approve or reject strategies
- Does not create or cancel orders
- Does not connect to any broker
- Does not move strategies past REVIEW_REQUIRED
- Does not run optimisations automatically

Every suggestion is advisory. Human approval is required for all
decisions that advance a strategy beyond REVIEW_REQUIRED.

Usage
-----
    python -m research.learning.learning_brain --spec-id N
    python -m research.learning.learning_brain --all
    python -m research.learning.learning_brain --spec-id N --dry-run
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

from research.learning import prompts as P

DEFAULT_DB        = _PROJECT_ROOT / "database" / "hermes_research.db"
DEFAULT_REPORTS   = _PROJECT_ROOT / "reports" / "learning"
AUDIT_DIR         = _PROJECT_ROOT / "reports" / "audits"
VALIDATION_DIR    = _PROJECT_ROOT / "reports" / "validation"
REGIME_DIR        = _PROJECT_ROOT / "reports" / "regime"
DECISION_DIR      = _PROJECT_ROOT / "reports" / "decision_packages"

_MIN_TRADES_HARD  = 30
_MIN_TRADES_SOFT  = 100
_PF_OVERFIT_GATE  = 2.5
_PF_OVERFIT_TRADES = 100
_WF_PASS_GATE     = 0.70
_WF_FAIL_GATE     = 0.50
_MC_PASS_GATE     = 0.85
_MC_FAIL_GATE     = 0.70
_DD_PROP_LIMIT    = 0.05
_DD_SEVERE        = 0.10
_PROB_POS_MIN     = 0.80
_PF_RETENTION_MIN = 0.60
_DD_COMP_MIN      = 0.50


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FailurePattern:
    pattern_id:  str
    severity:    str      # HIGH / MEDIUM / LOW
    category:    str      # Data / Overfit / OOS / Robustness / Compliance / Regime
    description: str


@dataclass
class LearningReview:
    spec_id:           int
    spec_name:         str
    readiness_status:  Optional[str]
    generated_at:      str
    failure_patterns:  List[FailurePattern]  = field(default_factory=list)
    strength_patterns: List[str]             = field(default_factory=list)
    next_actions:      List[Dict]            = field(default_factory=list)


# ---------------------------------------------------------------------------
# File helpers (self-contained; no import from other research modules)
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
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


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fetch_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT spec_id, spec_name, symbol, timeframe, status "
        "FROM strategy_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if not row:
        return None
    return {"spec_id": row[0], "spec_name": row[1],
            "symbol": row[2] or "", "timeframe": row[3] or "",
            "status": row[4] or ""}


def _fetch_latest_is_bt(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT backtest_id, net_profit, profit_factor, sharpe_ratio,
               win_rate, total_trades, max_drawdown_pct,
               data_start_date, data_end_date, initial_capital,
               trade_list_json IS NOT NULL AND trade_list_json != '' AS has_trades
        FROM backtests
        WHERE spec_id = ? AND is_in_sample = 1
        ORDER BY backtest_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    return {
        "backtest_id":      row[0], "net_profit":    row[1],
        "profit_factor":    row[2], "sharpe_ratio":  row[3],
        "win_rate":         row[4], "total_trades":  row[5],
        "max_drawdown_pct": row[6], "data_start":    row[7],
        "data_end":         row[8], "initial_capital": row[9],
        "has_trades":       bool(row[10]),
    }


def _fetch_latest_scoring(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT scoring_id, composite_score, grade,
               walk_forward_score, walk_forward_pass,
               monte_carlo_score, monte_carlo_pass,
               prop_firm_supported, prop_firm_support_json,
               overfitting_risk
        FROM scoring_results WHERE spec_id = ?
        ORDER BY scoring_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    pf_json = None
    if row[8]:
        try:
            pf_json = json.loads(row[8])
        except Exception:
            pass
    return {
        "scoring_id":          row[0], "composite_score":  row[1],
        "grade":               row[2], "walk_forward_score": row[3],
        "walk_forward_pass":   bool(row[4]), "monte_carlo_score": row[5],
        "monte_carlo_pass":    bool(row[6]), "prop_firm_supported": bool(row[7]),
        "prop_firm_json":      pf_json,      "overfitting_risk":   row[9],
    }


def _scored_spec_ids(conn: sqlite3.Connection) -> List[int]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT spec_id FROM scoring_results ORDER BY spec_id"
    ).fetchall()]


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------

def collect_learning_context(
    conn: sqlite3.Connection, spec_id: int
) -> Optional[Dict]:
    spec = _fetch_spec(conn, spec_id)
    if not spec:
        return None
    name = spec["spec_name"]
    pfx  = _safe(name)
    return {
        "spec":             spec,
        "backtest":         _fetch_latest_is_bt(conn, spec_id),
        "scoring":          _fetch_latest_scoring(conn, spec_id),
        "audit":            _latest_json(AUDIT_DIR,      pfx, "_"),
        "walk_forward":     _latest_json(VALIDATION_DIR, pfx, "_walk_forward_"),
        "monte_carlo":      _latest_json(VALIDATION_DIR, pfx, "_monte_carlo_"),
        "regime":           _latest_json(REGIME_DIR,     pfx, "_regime_analysis_"),
        "decision_package": _latest_json(DECISION_DIR,   pfx, "_decision_package_"),
    }


# ---------------------------------------------------------------------------
# Pattern identification
# ---------------------------------------------------------------------------

def identify_failure_patterns(ctx: Dict) -> List[FailurePattern]:
    patterns: List[FailurePattern] = []
    bt      = ctx.get("backtest")  or {}
    scoring = ctx.get("scoring")   or {}
    audit   = ctx.get("audit")     or {}
    wf      = ctx.get("walk_forward") or {}
    mc      = ctx.get("monte_carlo")  or {}
    regime  = ctx.get("regime")    or {}

    def add(pid: str, sev: str, cat: str, desc: str) -> None:
        patterns.append(FailurePattern(pid, sev, cat, desc))

    # --- Data quality ---
    if not bt or not bt.get("has_trades"):
        add("no_trade_json", "HIGH", "Data", P.p_no_trade_json())
        return patterns   # can't assess further without trade data

    trades = bt.get("total_trades") or 0
    if trades < _MIN_TRADES_HARD:
        add("insufficient_trades", "HIGH", "Data",
            P.p_insufficient_trades(trades, _MIN_TRADES_HARD))
    elif trades < _MIN_TRADES_SOFT:
        add("low_trade_count", "MEDIUM", "Data",
            P.p_low_trade_count(trades, _MIN_TRADES_SOFT))

    # --- Overfit signals ---
    pf     = bt.get("profit_factor") or 0.0
    sharpe = bt.get("sharpe_ratio")  or 0.0
    if pf >= _PF_OVERFIT_GATE and trades < _PF_OVERFIT_TRADES:
        add("pf_overfit_risk", "MEDIUM", "Overfit", P.p_pf_overfit_risk(pf, trades))
    if sharpe >= 2.0 and trades < _PF_OVERFIT_TRADES:
        add("sharpe_overfit_risk", "MEDIUM", "Overfit",
            P.p_sharpe_overfit_risk(sharpe, trades))

    # --- OOS / Walk-forward ---
    wf_score = scoring.get("walk_forward_score")
    if wf_score is None:
        add("missing_oos", "HIGH", "OOS", P.p_missing_oos())
    elif wf_score < _WF_FAIL_GATE:
        add("wf_fail", "HIGH", "OOS", P.p_wf_fail(wf_score, _WF_FAIL_GATE))
    elif wf_score < _WF_PASS_GATE:
        add("wf_warn", "MEDIUM", "OOS", P.p_wf_warn(wf_score, _WF_PASS_GATE))

    # WF component detail (from JSON)
    comps = wf.get("components") or {}
    pf_ret = comps.get("pf_retention")
    if pf_ret is not None and pf_ret < _PF_RETENTION_MIN:
        is_d  = wf.get("is")  or {}
        oos_d = wf.get("oos") or {}
        add("wf_pf_retention", "HIGH", "OOS",
            P.p_wf_pf_retention(pf_ret,
                                 is_d.get("profit_factor", 0),
                                 oos_d.get("profit_factor", 0)))
    dd_comp = comps.get("dd_component")
    if dd_comp is not None and dd_comp < _DD_COMP_MIN:
        is_d  = wf.get("is")  or {}
        oos_d = wf.get("oos") or {}
        add("wf_dd_worse", "MEDIUM", "OOS",
            P.p_wf_dd_worse(dd_comp,
                             is_d.get("max_drawdown_pct", 0),
                             oos_d.get("max_drawdown_pct", 0)))

    # --- Monte Carlo ---
    mc_score = scoring.get("monte_carlo_score")
    if mc_score is None:
        add("missing_mc", "MEDIUM", "Robustness", P.p_missing_mc())
    elif mc_score < _MC_FAIL_GATE:
        add("mc_fail", "HIGH", "Robustness", P.p_mc_fail(mc_score))
    elif mc_score < _MC_PASS_GATE:
        add("mc_warn", "MEDIUM", "Robustness", P.p_mc_warn(mc_score, _MC_PASS_GATE))

    prob_pos = mc.get("probability_positive")
    if prob_pos is not None and prob_pos < _PROB_POS_MIN:
        add("mc_prob_low", "MEDIUM", "Robustness", P.p_mc_prob_positive_low(prob_pos))

    # --- Compliance ---
    dd = bt.get("max_drawdown_pct") or 0.0
    if dd >= _DD_SEVERE:
        add("drawdown_severe", "HIGH", "Compliance", P.p_drawdown_severe(dd))
    elif dd > _DD_PROP_LIMIT:
        add("drawdown_limit", "MEDIUM", "Compliance",
            P.p_drawdown_exceeds_limit(dd, _DD_PROP_LIMIT))

    # --- Regime ---
    if not regime:
        add("missing_regime", "LOW", "Regime", P.p_missing_regime())
    else:
        data_windows = [w for w in regime.get("windows", []) if w.get("trade_count", 0) > 0]
        if len(data_windows) == 1:
            add("single_regime", "LOW", "Regime",
                P.p_single_regime_window(data_windows[0].get("label", "?")))

    # --- Audit FAILs ---
    fail_count = audit.get("fail_count", 0)
    if fail_count:
        add("audit_fails", "HIGH" if fail_count > 2 else "MEDIUM",
            "Data", P.p_audit_fails(fail_count))

    return patterns


def identify_strength_patterns(ctx: Dict) -> List[str]:
    strengths: List[str] = []
    bt      = ctx.get("backtest")  or {}
    scoring = ctx.get("scoring")   or {}
    audit   = ctx.get("audit")     or {}
    mc      = ctx.get("monte_carlo")  or {}
    regime  = ctx.get("regime")    or {}

    score    = scoring.get("composite_score")
    grade    = scoring.get("grade")
    mc_score = scoring.get("monte_carlo_score")
    wf_score = scoring.get("walk_forward_score")
    trades   = bt.get("total_trades") or 0
    pf       = bt.get("profit_factor") or 0.0
    wr       = bt.get("win_rate") or 0.0
    dd       = bt.get("max_drawdown_pct") or 0.0

    if mc_score and mc_score >= _MC_PASS_GATE:
        sims = mc.get("simulations", 1000)
        strengths.append(P.s_mc_pass(mc_score, sims))

    prob_pos = mc.get("probability_positive")
    if prob_pos and prob_pos >= 0.90:
        strengths.append(P.s_mc_prob_positive(prob_pos))

    if wf_score and scoring.get("walk_forward_pass"):
        strengths.append(P.s_wf_pass(wf_score))
    elif wf_score and _WF_FAIL_GATE <= wf_score < _WF_PASS_GATE:
        strengths.append(P.s_wf_reasonable(wf_score))

    if score and score >= 85:
        strengths.append(P.s_high_score(score, grade or "?"))

    if pf >= 2.0 and trades >= _MIN_TRADES_SOFT:
        strengths.append(P.s_strong_pf(pf, trades))

    if wr >= 0.60 and trades >= 50:
        strengths.append(P.s_good_win_rate(wr, trades))

    if scoring.get("overfitting_risk") == 0:
        strengths.append(P.s_low_overfit())

    if 0 < dd <= 0.03:
        strengths.append(P.s_low_drawdown(dd))

    if audit and audit.get("fail_count", 0) == 0 and audit.get("pass_count", 0) > 5:
        strengths.append(P.s_clean_audit())

    best_window = regime.get("best_window")
    if best_window:
        wins = [w for w in regime.get("windows", []) if w.get("label") == best_window]
        if wins:
            strengths.append(P.s_regime_consistent(best_window, wins[0].get("regime", "?")))

    return strengths


# ---------------------------------------------------------------------------
# Next-action suggestions
# ---------------------------------------------------------------------------

def suggest_next_research_actions(ctx: Dict) -> List[Dict]:
    actions: List[Dict] = []
    spec    = ctx.get("spec")     or {}
    bt      = ctx.get("backtest") or {}
    scoring = ctx.get("scoring")  or {}
    mc      = ctx.get("monte_carlo") or {}
    regime  = ctx.get("regime")  or {}

    sid    = spec.get("spec_id", 0)
    trades = bt.get("total_trades") or 0
    pf     = bt.get("profit_factor") or 0.0
    dd     = bt.get("max_drawdown_pct") or 0.0

    wf_score = scoring.get("walk_forward_score")
    mc_score = scoring.get("monte_carlo_score")

    # Priority 1: fundamental blockers
    if not bt or not bt.get("has_trades"):
        actions.append(P.a_import_nt8(sid))
        return _sorted(actions)

    if trades < _MIN_TRADES_HARD:
        actions.append(P.a_collect_minimum_trades(sid, trades, _MIN_TRADES_HARD))
        return _sorted(actions)

    # Priority 1: critical validation failures
    if wf_score is not None and wf_score < _WF_FAIL_GATE:
        actions.append(P.a_reject_wf_fail(sid, wf_score))
        return _sorted(actions)

    if mc_score is not None and mc_score < _MC_FAIL_GATE:
        actions.append(P.a_reject_mc_fail(sid, mc_score))
        return _sorted(actions)

    # Priority 2: missing validation gates
    if wf_score is None:
        # Check if OOS backtest exists
        oos_exists = _has_oos_bt(ctx)
        if oos_exists:
            actions.append(P.a_run_walk_forward(sid))
        else:
            actions.append(P.a_run_oos(sid))

    if mc_score is None:
        actions.append(P.a_run_monte_carlo(sid))

    # Priority 2: extend sample
    if 0 < trades < _MIN_TRADES_SOFT:
        actions.append(P.a_collect_more_trades(sid, trades, _MIN_TRADES_SOFT))

    # Priority 3: marginal validation issues
    if wf_score and not scoring.get("walk_forward_pass") and wf_score >= _WF_FAIL_GATE:
        actions.append(P.a_extend_oos_period(sid))

    # Priority 3: no regime analysis
    if not regime:
        actions.append(P.a_run_regime(sid))

    # Priority 4: research improvements
    if pf >= _PF_OVERFIT_GATE and trades < _PF_OVERFIT_TRADES:
        actions.append(P.a_parameter_sweep(sid, pf, trades))

    if dd > _DD_PROP_LIMIT:
        actions.append(P.a_add_drawdown_filter(sid, dd, _DD_PROP_LIMIT))

    best_w = regime.get("best_window")
    if best_w:
        actions.append(P.a_test_regime_filter(sid, best_w))

    # Prepare for review if all hard gates cleared
    if (wf_score is not None and mc_score is not None and
            wf_score >= _WF_FAIL_GATE and mc_score >= _MC_FAIL_GATE):
        actions.append(P.a_prepare_human_review(sid))

    return _sorted(actions)


def _has_oos_bt(ctx: Dict) -> bool:
    """True if WF JSON has an OOS backtest ID (proxy for OOS imported)."""
    wf = ctx.get("walk_forward") or {}
    oos = wf.get("oos") or {}
    return bool(oos.get("backtest_id"))


def _sorted(actions: List[Dict]) -> List[Dict]:
    return sorted(actions, key=lambda a: a["priority"])


# ---------------------------------------------------------------------------
# Review assembly
# ---------------------------------------------------------------------------

def generate_learning_review(
    conn: sqlite3.Connection, spec_id: int
) -> Tuple[Optional[LearningReview], Optional[str]]:
    ctx = collect_learning_context(conn, spec_id)
    if not ctx:
        return None, f"spec_id={spec_id} not found"

    spec = ctx["spec"]
    dp   = ctx.get("decision_package") or {}

    failures  = identify_failure_patterns(ctx)
    strengths = identify_strength_patterns(ctx)
    actions   = suggest_next_research_actions(ctx)

    return LearningReview(
        spec_id           = spec_id,
        spec_name         = spec["spec_name"],
        readiness_status  = dp.get("readiness_status"),
        generated_at      = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        failure_patterns  = failures,
        strength_patterns = strengths,
        next_actions      = actions,
    ), None


def generate_all_learning_reviews(
    conn: sqlite3.Connection,
) -> List[Tuple[int, Optional[LearningReview], Optional[str]]]:
    return [
        (sid, *generate_learning_review(conn, sid))
        for sid in _scored_spec_ids(conn)
    ]


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

_SEV_ICON = {"HIGH": "[!]", "MEDIUM": "[~]", "LOW": "[-]"}
_ACT_ICON = {
    "collect_more_trades":        "[T]",
    "run_oos_test":               "[W]",
    "run_monte_carlo":            "[M]",
    "run_wider_parameter_sweep":  "[P]",
    "add_filter_for_research":    "[F]",
    "test_regime_specific_variation": "[R]",
    "reject_candidate":           "[X]",
    "prepare_for_human_review":   "[+]",
}


def _to_markdown(r: LearningReview, dry_run: bool = False) -> str:
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    high   = sum(1 for f in r.failure_patterns if f.severity == "HIGH")
    medium = sum(1 for f in r.failure_patterns if f.severity == "MEDIUM")
    low    = sum(1 for f in r.failure_patterns if f.severity == "LOW")

    p(f"# Learning Review: {r.spec_name}")
    p(f"**Generated:** {r.generated_at[:10]}")
    p(f"**spec_id:** {r.spec_id}  |  "
      f"**Readiness:** {r.readiness_status or 'unknown'}  |  "
      f"**Patterns:** {high} HIGH / {medium} MEDIUM / {low} LOW")
    if dry_run:
        p()
        p("> **DRY-RUN** -- no files written")
    p()
    p("---")
    p()

    # Failure patterns
    p("## Failure Patterns")
    p()
    if r.failure_patterns:
        grouped: Dict[str, List[FailurePattern]] = {}
        for f in r.failure_patterns:
            grouped.setdefault(f.category, []).append(f)
        for cat, pats in grouped.items():
            p(f"### {cat}")
            p()
            for fp in pats:
                icon = _SEV_ICON.get(fp.severity, "[?]")
                p(f"**{icon} {fp.severity}** — {fp.description}")
                p()
    else:
        p("*No failure patterns identified.*")
    p()

    # Strength patterns
    p("## Strength Patterns")
    p()
    if r.strength_patterns:
        for s in r.strength_patterns:
            p(f"- {s}")
    else:
        p("*No significant strengths identified at this research stage.*")
    p()

    # Next actions
    p("## Suggested Next Research Actions")
    p()
    p("*Advisory only. No changes made. Human approval required for all advancement decisions.*")
    p()
    if r.next_actions:
        for i, a in enumerate(r.next_actions, 1):
            icon = _ACT_ICON.get(a["action_type"], "[?]")
            p(f"**{i}. {icon} {a['action_type'].replace('_', ' ').title()}**")
            p(f"> {a['description']}")
            if a.get("command"):
                p(f"> `{a['command']}`")
            p()
    else:
        p("*No actions suggested.*")

    p("---")
    p()
    p("*Read-only advisory output. No database writes. No strategy edits. "
      "REVIEW\\_REQUIRED is the terminal automated state. "
      "Human approval required before any strategy advances.*")

    return "\n".join(lines)


def _to_dict(r: LearningReview, dry_run: bool = False) -> Dict:
    return {
        "spec_id":          r.spec_id,
        "spec_name":        r.spec_name,
        "readiness_status": r.readiness_status,
        "generated_at":     r.generated_at,
        "dry_run":          dry_run,
        "failure_patterns": [
            {
                "pattern_id":  f.pattern_id,
                "severity":    f.severity,
                "category":    f.category,
                "description": f.description,
            }
            for f in r.failure_patterns
        ],
        "strength_patterns": r.strength_patterns,
        "next_actions":      r.next_actions,
        "summary": {
            "high_failures":   sum(1 for f in r.failure_patterns if f.severity == "HIGH"),
            "medium_failures":  sum(1 for f in r.failure_patterns if f.severity == "MEDIUM"),
            "low_failures":    sum(1 for f in r.failure_patterns if f.severity == "LOW"),
            "strengths":       len(r.strength_patterns),
            "actions":         len(r.next_actions),
        },
    }


def write_review(
    r: LearningReview, reports_dir: Path, dry_run: bool = False
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = r.generated_at[:10].replace("-", "")
    safe      = re.sub(r"[^\w\-]", "_", r.spec_name)

    md_path   = reports_dir / f"{safe}_learning_review_{date_str}.md"
    json_path = reports_dir / f"{safe}_learning_review_{date_str}.json"

    md_path.write_text(_to_markdown(r, dry_run=dry_run), encoding="utf-8")
    json_path.write_text(
        json.dumps(_to_dict(r, dry_run=dry_run), indent=2), encoding="utf-8"
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_review(r: LearningReview, dry_run: bool = False) -> None:
    tag  = "  [DRY-RUN]" if dry_run else ""
    high = [f for f in r.failure_patterns if f.severity == "HIGH"]
    med  = [f for f in r.failure_patterns if f.severity == "MEDIUM"]
    low  = [f for f in r.failure_patterns if f.severity == "LOW"]

    print(f"LEARNING: {r.spec_name}  [spec_id={r.spec_id}]{tag}")
    print(f"  Readiness : {r.readiness_status or 'unknown'}")
    print(f"  Patterns  : {len(high)} HIGH / {len(med)} MEDIUM / {len(low)} LOW")
    print(f"  Strengths : {len(r.strength_patterns)}")
    print()

    if r.failure_patterns:
        print("  Failure patterns:")
        for f in r.failure_patterns:
            icon = _SEV_ICON.get(f.severity, "[?]")
            print(f"    {icon} [{f.severity:<6}] {f.category}: {f.description[:80]}"
                  + ("..." if len(f.description) > 80 else ""))
        print()

    if r.strength_patterns:
        print("  Strengths:")
        for s in r.strength_patterns:
            print(f"    + {s[:90]}" + ("..." if len(s) > 90 else ""))
        print()

    if r.next_actions:
        print("  Suggested next actions:")
        for a in r.next_actions:
            icon = _ACT_ICON.get(a["action_type"], "[?]")
            print(f"    {a['priority']}. {icon} {a['description'][:85]}"
                  + ("..." if len(a["description"]) > 85 else ""))
            if a.get("command"):
                print(f"       > {a['command'][:80]}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "AI Learning Brain -- advisory pattern review. "
            "No DB writes. No strategy edits. REVIEW_REQUIRED is terminal."
        ),
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec-id", type=int, metavar="ID")
    grp.add_argument("--all",     action="store_true")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Console output only -- no files written")
    parser.add_argument("--db",          default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS), metavar="DIR")
    args = parser.parse_args()

    db_path     = Path(args.db)
    reports_dir = Path(args.reports_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes AI Learning Brain  [{mode}]")
    print(f"  DB         : {db_path}")
    if not args.dry_run:
        print(f"  Reports    : {reports_dir}")
    print(f"  Advisory only -- no DB writes, no strategy changes")
    print()

    conn = sqlite3.connect(str(db_path))
    try:
        spec_ids = [args.spec_id] if args.spec_id else _scored_spec_ids(conn)
        if not spec_ids:
            print("No scored specs found.")
            sys.exit(0)

        reviews: List[LearningReview] = []
        skipped: List[Tuple[int, str]] = []

        for sid in spec_ids:
            review, err = generate_learning_review(conn, sid)
            if review is None:
                skipped.append((sid, err or "unknown"))
                print(f"  SKIP spec_id={sid}: {err}")
                print()
                continue

            _print_review(review, dry_run=args.dry_run)

            if not args.dry_run:
                md_path, json_path = write_review(review, reports_dir)
                print(f"  Reports")
                print(f"    MD  : {md_path}")
                print(f"    JSON: {json_path}")
                print()

            reviews.append(review)

        # --all summary
        if args.all and reviews:
            print("=" * 68)
            print("LEARNING BRAIN SUMMARY")
            print("=" * 68)
            print()
            w = max(len(r.spec_name) for r in reviews)
            print(f"  {'Strategy':<{w}}  HIGH  MED  LOW  Actions  Readiness")
            print(f"  {'-'*w}  ----  ---  ---  -------  ---------")
            for r in reviews:
                h = sum(1 for f in r.failure_patterns if f.severity == "HIGH")
                m = sum(1 for f in r.failure_patterns if f.severity == "MEDIUM")
                l = sum(1 for f in r.failure_patterns if f.severity == "LOW")
                a = len(r.next_actions)
                status = (r.readiness_status or "unknown")[:28]
                print(f"  {r.spec_name:<{w}}  {h:>4}  {m:>3}  {l:>3}  {a:>7}  {status}")
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
