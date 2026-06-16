#!/usr/bin/env python3
"""
Unified Decision Package -- research/decision/decision_package.py

Consolidates all upstream research evidence for one strategy into a
single human-review document.

Sections
--------
1  Executive summary
2  Strategy identity
3  Backtest summary
4  Score / rank
5  Audit findings
6  Walk-forward result
7  Monte Carlo result
8  Regime analysis
9  Compliance status
10 Strengths
11 Blockers
12 Final readiness status
13 Required human action

Readiness statuses
------------------
  READY_FOR_HUMAN_REVIEW    -- all hard checks cleared; human may approve/reject
  NEEDS_REAL_NT8_EXPORT     -- no real backtest or trade_list_json
  NEEDS_MORE_TRADES         -- trade count below minimum (30)
  NEEDS_WALK_FORWARD        -- no OOS import or walk-forward score
  NEEDS_MONTE_CARLO         -- no Monte Carlo score
  NEEDS_REGIME_ANALYSIS     -- [warning only, not a hard blocker]
  REJECT_RESEARCH_CANDIDATE -- failed a critical validation gate

Blocker rules
-------------
  FAIL audit finding           -> BLOCKER
  Missing real backtest        -> BLOCKER
  Trade count < 30             -> BLOCKER
  walk_forward_score is null   -> BLOCKER
  monte_carlo_score is null    -> BLOCKER
  walk_forward_score < 0.50    -> BLOCKER (FAIL tier)
  monte_carlo_score < 0.70     -> BLOCKER (FAIL tier)
  Trade count < 100            -> WARNING (not BLOCKER)
  Regime analysis not run      -> WARNING (not BLOCKER)
  Prop-firm not supported      -> WARNING

No DB writes. No schema changes. No live trading.
REVIEW_REQUIRED is the terminal automated state.
Human approval required before any strategy advances.

Usage
-----
    python -m research.decision.decision_package --spec-id N
    python -m research.decision.decision_package --all
    python -m research.decision.decision_package --dry-run
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

DEFAULT_DB        = _PROJECT_ROOT / "database" / "hermes_research.db"
DEFAULT_REPORTS   = _PROJECT_ROOT / "reports" / "decision_packages"
AUDIT_REPORTS_DIR = _PROJECT_ROOT / "reports" / "audits"
REGIME_DIR        = _PROJECT_ROOT / "reports" / "regime"
VALIDATION_DIR    = _PROJECT_ROOT / "reports" / "validation"

_MIN_TRADES_HARD = 30
_MIN_TRADES_SOFT = 100

_WF_FAIL_GATE = 0.50
_WF_PASS_GATE = 0.70
_MC_FAIL_GATE = 0.70
_MC_PASS_GATE = 0.85


# ---------------------------------------------------------------------------
# Readiness statuses and actions
# ---------------------------------------------------------------------------

_REQUIRED_ACTIONS: Dict[str, str] = {
    "READY_FOR_HUMAN_REVIEW":    "Review all evidence sections and approve or reject for forward testing.",
    "NEEDS_REAL_NT8_EXPORT":     "Import a real NT8 backtest export (trade_list_json required).",
    "NEEDS_MORE_TRADES":         "Collect more trades. Minimum 30 required; 100+ recommended.",
    "NEEDS_WALK_FORWARD":        "Import an OOS backtest (--oos) and run walk-forward validation.",
    "NEEDS_MONTE_CARLO":         "Run Monte Carlo validation: python -m research.validation.monte_carlo --spec-id N",
    "NEEDS_REGIME_ANALYSIS":     "Run regime analysis: python -m research.regime.regime_analyzer --spec-id N",
    "REJECT_RESEARCH_CANDIDATE": "Archive as rejected research candidate with a documented rejection reason.",
}

_STATUS_ICON: Dict[str, str] = {
    "READY_FOR_HUMAN_REVIEW":    "[READY]",
    "NEEDS_REAL_NT8_EXPORT":     "[NEEDS]",
    "NEEDS_MORE_TRADES":         "[NEEDS]",
    "NEEDS_WALK_FORWARD":        "[NEEDS]",
    "NEEDS_MONTE_CARLO":         "[NEEDS]",
    "NEEDS_REGIME_ANALYSIS":     "[NEEDS]",
    "REJECT_RESEARCH_CANDIDATE": "[REJECT]",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DecisionPackage:
    spec_id:          int
    spec_name:        str
    symbol:           str
    timeframe:        str
    spec_status:      str
    generated_at:     str
    readiness_status: str
    required_action:  str
    blockers:         List[Dict] = field(default_factory=list)
    strengths:        List[str]  = field(default_factory=list)
    evidence:         Dict       = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fetch_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT spec_id, spec_name, symbol, timeframe, status
        FROM strategy_specs WHERE spec_id = ?
    """, (spec_id,)).fetchone()
    if not row:
        return None
    return {"spec_id": row[0], "spec_name": row[1],
            "symbol": row[2] or "", "timeframe": row[3] or "",
            "status": row[4] or ""}


def _fetch_latest_is_backtest(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT backtest_id, net_profit, profit_factor, win_rate, total_trades,
               max_drawdown_pct, data_start_date, data_end_date,
               initial_capital,
               trade_list_json IS NOT NULL AND trade_list_json != '' AS has_trade_json
        FROM backtests
        WHERE spec_id = ? AND is_in_sample = 1
        ORDER BY backtest_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    return {
        "backtest_id":      row[0],
        "net_profit":       row[1],
        "profit_factor":    row[2],
        "win_rate":         row[3],
        "total_trades":     row[4],
        "max_drawdown_pct": row[5],
        "data_start_date":  row[6],
        "data_end_date":    row[7],
        "initial_capital":  row[8],
        "has_trade_json":   bool(row[9]),
    }


def _fetch_latest_scoring(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT scoring_id, composite_score, grade, recommendation,
               walk_forward_score, walk_forward_pass,
               monte_carlo_score, monte_carlo_pass,
               prop_firm_supported, prop_firm_support_json,
               overfitting_risk, scored_at
        FROM scoring_results
        WHERE spec_id = ?
        ORDER BY scoring_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    pf_json = None
    if row[9]:
        try:
            pf_json = json.loads(row[9])
        except Exception:
            pass
    return {
        "scoring_id":          row[0],
        "composite_score":     row[1],
        "grade":               row[2],
        "recommendation":      row[3],
        "walk_forward_score":  row[4],
        "walk_forward_pass":   bool(row[5]),
        "monte_carlo_score":   row[6],
        "monte_carlo_pass":    bool(row[7]),
        "prop_firm_supported": bool(row[8]),
        "prop_firm_json":      pf_json,
        "overfitting_risk":    row[10],
        "scored_at":           row[11],
    }


def decision_queue(conn: sqlite3.Connection) -> List[int]:
    """All spec_ids that have at least one scoring result."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT spec_id FROM scoring_results ORDER BY spec_id"
    ).fetchall()]


# ---------------------------------------------------------------------------
# File-based evidence (audit / regime / validation JSON files)
# ---------------------------------------------------------------------------

def _safe_name(spec_name: str) -> str:
    return re.sub(r"[^\w\-]", "_", spec_name)


def _find_latest_json(directory: Path, prefix: str, infix: str) -> Optional[Dict]:
    """
    Find the latest JSON in `directory` matching `{prefix}{infix}*.json`.
    Returns parsed dict or None.
    """
    if not directory.exists():
        return None
    matches = sorted(directory.glob(f"{prefix}{infix}*.json"))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_audit(spec_name: str) -> Optional[Dict]:
    prefix = _safe_name(spec_name)
    return _find_latest_json(AUDIT_REPORTS_DIR, prefix, "_")


def _load_regime(spec_name: str) -> Optional[Dict]:
    prefix = _safe_name(spec_name)
    return _find_latest_json(REGIME_DIR, prefix, "_regime_analysis_")


def _load_wf_detail(spec_name: str) -> Optional[Dict]:
    prefix = _safe_name(spec_name)
    return _find_latest_json(VALIDATION_DIR, prefix, "_walk_forward_")


def _load_mc_detail(spec_name: str) -> Optional[Dict]:
    prefix = _safe_name(spec_name)
    # Named reports may include date or not
    data = _find_latest_json(VALIDATION_DIR, prefix, "_monte_carlo_2")
    if data is None:
        data = _find_latest_json(VALIDATION_DIR, prefix, "_monte_carlo.")
    return data


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------

def collect_strategy_evidence(
    conn:    sqlite3.Connection,
    spec_id: int,
) -> Optional[Dict]:
    spec = _fetch_spec(conn, spec_id)
    if not spec:
        return None
    name = spec["spec_name"]
    return {
        "spec":              spec,
        "backtest":          _fetch_latest_is_backtest(conn, spec_id),
        "scoring":           _fetch_latest_scoring(conn, spec_id),
        "audit":             _load_audit(name),
        "walk_forward":      _load_wf_detail(name),
        "monte_carlo":       _load_mc_detail(name),
        "regime":            _load_regime(name),
    }


# ---------------------------------------------------------------------------
# Readiness logic
# ---------------------------------------------------------------------------

def compute_readiness_status(evidence: Dict) -> str:
    bt      = evidence.get("backtest") or {}
    scoring = evidence.get("scoring")  or {}
    audit   = evidence.get("audit")    or {}

    # 1. No scoring result
    if not scoring:
        return "NEEDS_REAL_NT8_EXPORT"

    # 2. No IS backtest or no trade_list_json
    if not bt or not bt.get("has_trade_json"):
        return "NEEDS_REAL_NT8_EXPORT"

    # 3. Trade count below hard minimum
    trades = bt.get("total_trades") or 0
    if trades < _MIN_TRADES_HARD:
        return "NEEDS_MORE_TRADES"

    # 4. FAIL audit checks that indicate missing fundamentals
    for chk in audit.get("checks", []):
        if chk["status"] != "FAIL":
            continue
        name_lower = chk["check"].lower()
        if "trade_list" in name_lower:
            return "NEEDS_REAL_NT8_EXPORT"
        if "out-of-sample" in name_lower or "out_of_sample" in name_lower:
            return "NEEDS_WALK_FORWARD"

    # 5. Walk-forward score missing
    wf_score = scoring.get("walk_forward_score")
    if wf_score is None:
        return "NEEDS_WALK_FORWARD"

    # 6. Monte Carlo score missing
    mc_score = scoring.get("monte_carlo_score")
    if mc_score is None:
        return "NEEDS_MONTE_CARLO"

    # 7. Walk-forward FAIL tier
    if wf_score < _WF_FAIL_GATE:
        return "REJECT_RESEARCH_CANDIDATE"

    # 8. Monte Carlo FAIL tier
    if mc_score < _MC_FAIL_GATE:
        return "REJECT_RESEARCH_CANDIDATE"

    # 9. Any remaining FAIL audit checks
    for chk in audit.get("checks", []):
        if chk["status"] == "FAIL":
            return "REJECT_RESEARCH_CANDIDATE"

    return "READY_FOR_HUMAN_REVIEW"


def list_blockers(evidence: Dict) -> List[Dict]:
    blockers: List[Dict] = []
    bt      = evidence.get("backtest") or {}
    scoring = evidence.get("scoring")  or {}
    audit   = evidence.get("audit")    or {}

    def b(msg: str) -> Dict:
        return {"severity": "BLOCKER", "message": msg}

    def w(msg: str) -> Dict:
        return {"severity": "WARNING", "message": msg}

    # Backtest
    if not bt:
        blockers.append(b("No IS backtest found"))
    elif not bt.get("has_trade_json"):
        blockers.append(b("trade_list_json missing -- import real NT8 export with --initial-capital"))

    # Trade count
    trades = bt.get("total_trades") or 0
    if 0 < trades < _MIN_TRADES_HARD:
        blockers.append(b(f"{trades} trades -- minimum {_MIN_TRADES_HARD} required"))
    elif _MIN_TRADES_HARD <= trades < _MIN_TRADES_SOFT:
        blockers.append(w(f"{trades} trades -- {_MIN_TRADES_SOFT}+ recommended for statistical confidence"))

    # Walk-forward
    wf_score = scoring.get("walk_forward_score")
    wf_pass  = scoring.get("walk_forward_pass", False)
    if wf_score is None:
        blockers.append(b("walk_forward_score is null -- import OOS backtest (--oos) then run walk_forward engine"))
    elif wf_score < _WF_FAIL_GATE:
        blockers.append(b(f"Walk-forward FAIL: score={wf_score:.4f} (threshold {_WF_FAIL_GATE}) -- OOS performance severely degraded"))
    elif not wf_pass:
        blockers.append(w(f"Walk-forward WARNING: score={wf_score:.4f} (below PASS threshold {_WF_PASS_GATE})"))

    # Monte Carlo
    mc_score = scoring.get("monte_carlo_score")
    mc_pass  = scoring.get("monte_carlo_pass", False)
    if mc_score is None:
        blockers.append(b("monte_carlo_score is null -- run: python -m research.validation.monte_carlo --spec-id N"))
    elif mc_score < _MC_FAIL_GATE:
        blockers.append(b(f"Monte Carlo FAIL: score={mc_score:.4f} (threshold {_MC_FAIL_GATE}) -- sequence-dependent results"))
    elif not mc_pass:
        blockers.append(w(f"Monte Carlo WARNING: score={mc_score:.4f} (below PASS threshold {_MC_PASS_GATE})"))

    # Audit FAILs
    for chk in audit.get("checks", []):
        if chk["status"] == "FAIL":
            blockers.append(b(f"[AUDIT FAIL] {chk['category']} / {chk['check']}: {chk['detail']}"))

    # Soft warnings
    if not evidence.get("regime"):
        blockers.append(w("Regime analysis not run -- run: python -m research.regime.regime_analyzer --spec-id N"))

    if not scoring.get("prop_firm_supported"):
        pf = scoring.get("prop_firm_json") or {}
        dd_limit = pf.get("trailing_drawdown_limit")
        detail = f"trailing DD limit={dd_limit:.0%}" if dd_limit else "see prop_firm_support_json"
        blockers.append(w(f"Prop-firm compliance not confirmed ({detail})"))

    return blockers


def list_strengths(evidence: Dict) -> List[str]:
    strengths: List[str] = []
    bt      = evidence.get("backtest") or {}
    scoring = evidence.get("scoring")  or {}
    regime  = evidence.get("regime")   or {}
    mc      = evidence.get("monte_carlo") or {}

    score = scoring.get("composite_score")
    grade = scoring.get("grade")
    if score and score >= 80:
        strengths.append(f"Composite score {score:.1f} (Grade {grade}) -- strong overall metrics")
    elif score and score >= 70:
        strengths.append(f"Composite score {score:.1f} (Grade {grade})")

    mc_score = scoring.get("monte_carlo_score")
    if mc_score and mc_score >= _MC_PASS_GATE:
        sims  = mc.get("simulations", 1000)
        prob  = mc.get("probability_positive")
        extra = f", P(positive)={prob:.1%}" if prob is not None else ""
        strengths.append(f"Monte Carlo PASS: survival={mc_score:.1%} across {sims:,} bootstrap simulations{extra}")
    elif mc_score and mc_score >= _MC_FAIL_GATE:
        strengths.append(f"Monte Carlo WARNING: survival={mc_score:.1%} -- marginal but above FAIL threshold")

    wf_score = scoring.get("walk_forward_score")
    if wf_score and scoring.get("walk_forward_pass"):
        strengths.append(f"Walk-forward PASS: score={wf_score:.4f} -- OOS performance validated")
    elif wf_score and _WF_FAIL_GATE <= wf_score < _WF_PASS_GATE:
        strengths.append(f"Walk-forward WARNING: score={wf_score:.4f} -- OOS degradation present but manageable")

    pf = bt.get("profit_factor")
    if pf and pf >= 2.0:
        strengths.append(f"Profit factor {pf:.2f} -- favourable risk/reward ratio")

    wr = bt.get("win_rate")
    if wr and wr >= 0.60:
        strengths.append(f"Win rate {wr:.1%} -- consistent trade direction accuracy")

    if scoring.get("overfitting_risk") == 0:
        strengths.append("Overfitting risk 0.00 -- no parameter overfitting detected by scoring engine")

    best_w = regime.get("best_window")
    if best_w:
        wins = [w for w in regime.get("windows", []) if w.get("label") == best_w]
        if wins:
            w = wins[0]
            strengths.append(
                f"Regime best window '{best_w}' ({w.get('regime','?')}): "
                f"exp/trade=${w.get('expectancy_per_trade', 0):.2f}, "
                f"win rate={w.get('win_rate', 0):.1%}"
            )

    return strengths


# ---------------------------------------------------------------------------
# Package assembly
# ---------------------------------------------------------------------------

def generate_decision_package(
    conn:    sqlite3.Connection,
    spec_id: int,
) -> Tuple[Optional[DecisionPackage], Optional[str]]:
    evidence = collect_strategy_evidence(conn, spec_id)
    if not evidence:
        return None, f"spec_id={spec_id} not found"

    spec    = evidence["spec"]
    scoring = evidence.get("scoring") or {}

    readiness = compute_readiness_status(evidence)
    blockers  = list_blockers(evidence)
    strengths = list_strengths(evidence)
    action    = _REQUIRED_ACTIONS.get(readiness, "Review required.")

    return DecisionPackage(
        spec_id          = spec_id,
        spec_name        = spec["spec_name"],
        symbol           = spec["symbol"],
        timeframe        = spec["timeframe"],
        spec_status      = spec["status"],
        generated_at     = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        readiness_status = readiness,
        required_action  = action,
        blockers         = blockers,
        strengths        = strengths,
        evidence         = evidence,
    ), None


def generate_all_decision_packages(
    conn: sqlite3.Connection,
) -> List[Tuple[int, Optional[DecisionPackage], Optional[str]]]:
    results = []
    for spec_id in decision_queue(conn):
        pkg, err = generate_decision_package(conn, spec_id)
        results.append((spec_id, pkg, err))
    return results


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _pct(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "null"
    return f"{v:.{decimals}%}"


def _fmt(v: Optional[float], prefix: str = "", decimals: int = 2) -> str:
    if v is None:
        return "null"
    return f"{prefix}{v:,.{decimals}f}"


def _score_line(score: Optional[float], pass_gate: float, fail_gate: float) -> str:
    if score is None:
        return "null"
    if score >= pass_gate:
        icon = "[+]"
    elif score >= fail_gate:
        icon = "[!]"
    else:
        icon = "[X]"
    return f"{icon} {score:.4f}"


def _to_markdown(pkg: DecisionPackage, dry_run: bool = False) -> str:
    lines: List[str] = []
    ev      = pkg.evidence
    bt      = ev.get("backtest")   or {}
    scoring = ev.get("scoring")    or {}
    audit   = ev.get("audit")      or {}
    wf      = ev.get("walk_forward") or {}
    mc      = ev.get("monte_carlo")  or {}
    regime  = ev.get("regime")     or {}
    pf_json = scoring.get("prop_firm_json") or {}

    icon = _STATUS_ICON.get(pkg.readiness_status, "[?]")

    def p(s: str = "") -> None:
        lines.append(s)

    p(f"# Decision Package: {pkg.spec_name}")
    p(f"**Generated:** {pkg.generated_at[:10]}  |  "
      f"**Readiness:** {icon} {pkg.readiness_status}")
    if dry_run:
        p()
        p("> **DRY-RUN** -- no files written")
    p()
    p("---")
    p()

    # Executive summary
    p("## Executive Summary")
    p()
    p(f"Strategy **{pkg.spec_name}** has been assessed through the Hermes "
      f"research pipeline. Current readiness: **{pkg.readiness_status}**.")
    p()
    blocker_count = sum(1 for b in pkg.blockers if b["severity"] == "BLOCKER")
    warning_count = sum(1 for b in pkg.blockers if b["severity"] == "WARNING")
    strength_count = len(pkg.strengths)
    p(f"Evidence summary: {strength_count} strength(s), "
      f"{blocker_count} blocker(s), {warning_count} warning(s).")
    p()
    p(f"**Action Required:** {pkg.required_action}")
    p()
    p("---")
    p()

    # 1. Strategy identity
    p("## 1. Strategy Identity")
    p()
    p(f"| Field | Value |")
    p(f"|-------|-------|")
    p(f"| Spec ID | {pkg.spec_id} |")
    p(f"| Name | {pkg.spec_name} |")
    p(f"| Symbol | {pkg.symbol or '—'} |")
    p(f"| Timeframe | {pkg.timeframe or '—'} |")
    p(f"| Status | {pkg.spec_status} |")
    p()

    # 2. Backtest summary
    p("## 2. Backtest Summary")
    p()
    if bt:
        date_range = (f"{bt.get('data_start_date', '?')} to {bt.get('data_end_date', '?')}"
                      if bt.get("data_start_date") else "—")
        p(f"| Metric | Value |")
        p(f"|--------|-------|")
        p(f"| Backtest ID | {bt.get('backtest_id', '—')} |")
        p(f"| Date range | {date_range} |")
        p(f"| Trade count | {bt.get('total_trades', '—')} |")
        p(f"| Net profit | {_fmt(bt.get('net_profit'), '$')} |")
        p(f"| Profit factor | {_fmt(bt.get('profit_factor'), decimals=2)} |")
        p(f"| Win rate | {_pct(bt.get('win_rate'))} |")
        p(f"| Max drawdown | {_pct(bt.get('max_drawdown_pct'))} |")
        p(f"| Initial capital | {_fmt(bt.get('initial_capital'), '$', 0)} |")
        p(f"| Trade list | {'present' if bt.get('has_trade_json') else 'MISSING'} |")
    else:
        p("*No IS backtest found.*")
    p()

    # 3. Score / rank
    p("## 3. Score / Rank")
    p()
    if scoring:
        p(f"| Metric | Value |")
        p(f"|--------|-------|")
        p(f"| Scoring ID | {scoring.get('scoring_id', '—')} |")
        p(f"| Composite score | {scoring.get('composite_score', '—')} |")
        p(f"| Grade | {scoring.get('grade', '—')} |")
        p(f"| Recommendation | {scoring.get('recommendation', '—')} |")
        p(f"| Overfitting risk | {scoring.get('overfitting_risk', '—')} |")
        p(f"| Scored at | {scoring.get('scored_at', '—')} |")
    else:
        p("*No scoring result found.*")
    p()

    # 4. Audit findings
    p("## 4. Audit Findings")
    p()
    if audit:
        p(f"**PASS={audit.get('pass_count', 0)}  "
          f"WARN={audit.get('warn_count', 0)}  "
          f"FAIL={audit.get('fail_count', 0)}**  "
          f"|  Auditor recommendation: {audit.get('recommendation', '—')}")
        p()
        p(f"| Category | Check | Status | Detail |")
        p(f"|----------|-------|--------|--------|")
        for chk in audit.get("checks", []):
            icon_map = {"PASS": "[+]", "WARN": "[!]", "FAIL": "[X]", "INFO": "[i]"}
            status_icon = icon_map.get(chk["status"], "[ ]")
            p(f"| {chk['category']} | {chk['check']} | "
              f"{status_icon} {chk['status']} | {chk['detail']} |")
        p(f"*Audited at: {audit.get('audited_at', '—')}*")
    else:
        p("*No audit report found. Run: python -m research.audit.strategy_auditor --spec-id N*")
    p()

    # 5. Walk-forward
    p("## 5. Walk-Forward Validation")
    p()
    wf_score = scoring.get("walk_forward_score")
    wf_pass  = scoring.get("walk_forward_pass", False)
    p(f"**Score:** {_score_line(wf_score, _WF_PASS_GATE, _WF_FAIL_GATE)}  |  "
      f"**Pass:** {wf_pass if wf_score is not None else 'null'}  |  "
      f"Thresholds: PASS>={_WF_PASS_GATE}  WARN>={_WF_FAIL_GATE}  FAIL<{_WF_FAIL_GATE}")
    p()
    if wf:
        is_d  = wf.get("is")  or {}
        oos_d = wf.get("oos") or {}
        comps = wf.get("components") or {}
        p(f"| Metric | In-Sample | Out-of-Sample | Retention |")
        p(f"|--------|-----------|---------------|-----------|")
        p(f"| Date range | {is_d.get('date_range', '—')} | "
          f"{oos_d.get('date_range', '—')} | — |")
        p(f"| Trade count | {is_d.get('total_trades', '—')} | "
          f"{oos_d.get('total_trades', '—')} | — |")
        p(f"| Profit factor | {_fmt(is_d.get('profit_factor'))} | "
          f"{_fmt(oos_d.get('profit_factor'))} | "
          f"{_pct(comps.get('pf_retention'))} |")
        p(f"| Expectancy/trade | {_fmt(is_d.get('exp_per_trade'))} | "
          f"{_fmt(oos_d.get('exp_per_trade'))} | "
          f"{_pct(comps.get('expectancy_retention'))} |")
        p(f"| Max drawdown | {_pct(is_d.get('max_drawdown_pct'))} | "
          f"{_pct(oos_d.get('max_drawdown_pct'))} | "
          f"DD comp: {_pct(comps.get('dd_component'))} |")
        p(f"*Walk-forward report: reports/validation/*")
    elif wf_score is not None:
        p("*Score from database. Detailed WF report not found in reports/validation/.*")
    else:
        p("*No walk-forward data. Import OOS backtest (--oos) then run walk_forward engine.*")
    p()

    # 6. Monte Carlo
    p("## 6. Monte Carlo Validation")
    p()
    mc_score = scoring.get("monte_carlo_score")
    mc_pass  = scoring.get("monte_carlo_pass", False)
    p(f"**Score:** {_score_line(mc_score, _MC_PASS_GATE, _MC_FAIL_GATE)}  |  "
      f"**Pass:** {mc_pass if mc_score is not None else 'null'}  |  "
      f"Thresholds: PASS>={_MC_PASS_GATE}  WARN>={_MC_FAIL_GATE}  FAIL<{_MC_FAIL_GATE}")
    p()
    if mc:
        p(f"| Metric | Value |")
        p(f"|--------|-------|")
        p(f"| Method | {mc.get('method', '—')} |")
        p(f"| Simulations | {mc.get('simulations', '—'):,} |")
        p(f"| Survival rate | {_pct(mc.get('survival_rate'))} |")
        p(f"| Probability positive | {_pct(mc.get('probability_positive'))} |")
        p(f"| Ruin threshold | "
          f"${mc.get('ruin_dollar_threshold', 0):,.0f} "
          f"({_pct(mc.get('ruin_pct_threshold'))}) |")
        p(f"| Worst drawdown | {_pct(mc.get('worst_drawdown'))} |")
        p(f"| 95th pct drawdown | {_pct(mc.get('p95_drawdown'))} |")
        p(f"| Median drawdown | {_pct(mc.get('median_drawdown'))} |")
        p(f"*Monte Carlo report: reports/validation/*")
    elif mc_score is not None:
        p("*Score from database. Detailed MC report not found in reports/validation/.*")
    else:
        p("*No Monte Carlo data. Run: python -m research.validation.monte_carlo --spec-id N*")
    p()

    # 7. Regime analysis
    p("## 7. Regime Analysis")
    p()
    if regime:
        mode = regime.get("mode", "—")
        p(f"**Mode:** {mode}  |  "
          f"**Best window:** {regime.get('best_window', '—')}  |  "
          f"**Worst window:** {regime.get('worst_window', '—')}")
        p()
        windows = regime.get("windows", [])
        if windows:
            p(f"| Window | Regime | Trades | Net P&L | Win% | PF | Exp/Trade |")
            p(f"|--------|--------|--------|---------|------|----|-----------|")
            icon_map = {"Strong": "[+]", "Neutral": "[~]", "Weak": "[X]", "No Data": "[-]"}
            for w in windows:
                pf_v = w.get("profit_factor")
                pf_s = "inf" if pf_v is None else f"{pf_v:.2f}"
                badge = ""
                if w.get("label") == regime.get("best_window"):
                    badge = " **[best]**"
                elif w.get("label") == regime.get("worst_window"):
                    badge = " **[worst]**"
                ri   = icon_map.get(w.get("regime", ""), "[?]")
                p(f"| {w.get('label', '—')}{badge} | {ri} {w.get('regime', '—')} | "
                  f"{w.get('trade_count', 0)} | "
                  f"${w.get('net_pnl', 0):,.2f} | "
                  f"{_pct(w.get('win_rate'))} | "
                  f"{pf_s}x | "
                  f"${w.get('expectancy_per_trade', 0):,.2f} |")
        p(f"*Regime report: reports/regime/*")
    else:
        p("*No regime analysis found. Run: python -m research.regime.regime_analyzer --spec-id N*")
    p()

    # 8. Compliance
    p("## 8. Compliance Status")
    p()
    pf_supported = scoring.get("prop_firm_supported", False)
    p(f"**Prop-firm supported:** {'Yes' if pf_supported else 'No'}")
    p()
    if pf_json:
        p(f"| Parameter | Value |")
        p(f"|-----------|-------|")
        p(f"| Account size | {_fmt(pf_json.get('account_size'), '$', 0)} |")
        dd_lim = pf_json.get("trailing_drawdown_limit")
        p(f"| Trailing drawdown limit | {_pct(dd_lim) if dd_lim else '—'} |")
        dl = pf_json.get("daily_loss_limit")
        p(f"| Daily loss limit | {_pct(dl) if dl else '—'} |")
        md = pf_json.get("max_drawdown")
        p(f"| Max drawdown (actual) | {_pct(md) if md else '—'} |")
        dbp = pf_json.get("drawdown_breach_probability")
        p(f"| Drawdown breach probability | {_pct(dbp) if dbp else '—'} |")
    p()

    # 9. Strengths
    p("## 9. Strengths")
    p()
    if pkg.strengths:
        for s in pkg.strengths:
            p(f"- {s}")
    else:
        p("*No significant strengths identified at this stage.*")
    p()

    # 10. Blockers
    p("## 10. Blockers and Warnings")
    p()
    hard = [b for b in pkg.blockers if b["severity"] == "BLOCKER"]
    soft = [b for b in pkg.blockers if b["severity"] == "WARNING"]
    if hard:
        p(f"**Blockers ({len(hard)}):**")
        for b in hard:
            p(f"- [BLOCKER] {b['message']}")
        p()
    if soft:
        p(f"**Warnings ({len(soft)}):**")
        for b in soft:
            p(f"- [WARNING] {b['message']}")
        p()
    if not hard and not soft:
        p("*No blockers or warnings.*")
    p()

    # 11. Final readiness
    p("---")
    p()
    p("## Final Readiness")
    p()
    p(f"**Status:** {icon} {pkg.readiness_status}")
    p()
    p(f"**Required Action:** {pkg.required_action}")
    p()
    p("---")
    p()
    p("*Read-only research output. REVIEW\\_REQUIRED is the terminal automated state. "
      "No strategy advances beyond REVIEW\\_REQUIRED without explicit human approval. "
      "This package is informational only.*")

    return "\n".join(lines)


def _to_dict(pkg: DecisionPackage, dry_run: bool = False) -> Dict:
    ev      = pkg.evidence
    bt      = ev.get("backtest")   or {}
    scoring = ev.get("scoring")    or {}
    audit   = ev.get("audit")      or {}
    wf      = ev.get("walk_forward") or {}
    mc      = ev.get("monte_carlo")  or {}
    regime  = ev.get("regime")     or {}

    return {
        "spec_id":          pkg.spec_id,
        "spec_name":        pkg.spec_name,
        "symbol":           pkg.symbol,
        "timeframe":        pkg.timeframe,
        "spec_status":      pkg.spec_status,
        "generated_at":     pkg.generated_at,
        "dry_run":          dry_run,
        "readiness_status": pkg.readiness_status,
        "required_action":  pkg.required_action,
        "blockers":         pkg.blockers,
        "strengths":        pkg.strengths,
        "backtest":         bt,
        "scoring": {
            "scoring_id":         scoring.get("scoring_id"),
            "composite_score":    scoring.get("composite_score"),
            "grade":              scoring.get("grade"),
            "walk_forward_score": scoring.get("walk_forward_score"),
            "walk_forward_pass":  scoring.get("walk_forward_pass"),
            "monte_carlo_score":  scoring.get("monte_carlo_score"),
            "monte_carlo_pass":   scoring.get("monte_carlo_pass"),
            "prop_firm_supported": scoring.get("prop_firm_supported"),
            "overfitting_risk":   scoring.get("overfitting_risk"),
        },
        "audit": {
            "pass_count":     audit.get("pass_count"),
            "warn_count":     audit.get("warn_count"),
            "fail_count":     audit.get("fail_count"),
            "recommendation": audit.get("recommendation"),
            "audited_at":     audit.get("audited_at"),
            "fail_checks": [
                c for c in audit.get("checks", []) if c["status"] == "FAIL"
            ],
        } if audit else None,
        "walk_forward":  wf  or None,
        "monte_carlo":   mc  or None,
        "regime": {
            "mode":         regime.get("mode"),
            "best_window":  regime.get("best_window"),
            "worst_window": regime.get("worst_window"),
            "windows":      regime.get("windows", []),
        } if regime else None,
    }


# ---------------------------------------------------------------------------
# File output
# ---------------------------------------------------------------------------

def write_package(
    pkg:         DecisionPackage,
    reports_dir: Path,
    dry_run:     bool = False,
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = pkg.generated_at[:10].replace("-", "")
    safe      = _safe_name(pkg.spec_name)

    md_path   = reports_dir / f"{safe}_decision_package_{date_str}.md"
    json_path = reports_dir / f"{safe}_decision_package_{date_str}.json"

    md_path.write_text(_to_markdown(pkg, dry_run=dry_run), encoding="utf-8")
    json_path.write_text(
        json.dumps(_to_dict(pkg, dry_run=dry_run), indent=2), encoding="utf-8"
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_package(pkg: DecisionPackage, dry_run: bool = False) -> None:
    icon = _STATUS_ICON.get(pkg.readiness_status, "[?]")
    tag  = "  [DRY-RUN]" if dry_run else ""
    bt   = pkg.evidence.get("backtest") or {}
    scoring = pkg.evidence.get("scoring") or {}

    print(f"DECISION: {pkg.spec_name}  [spec_id={pkg.spec_id}]{tag}")
    print(f"  Symbol     : {pkg.symbol or '(none)'}  {pkg.timeframe or ''}")
    print(f"  Backtest   : {bt.get('total_trades', '?')} trades  "
          f"PF={bt.get('profit_factor') or '?'}  "
          f"WR={_pct(bt.get('win_rate'))}  "
          f"DD={_pct(bt.get('max_drawdown_pct'))}")
    print(f"  Score      : {scoring.get('composite_score', '?')}  "
          f"Grade={scoring.get('grade', '?')}")

    wf = scoring.get("walk_forward_score")
    mc = scoring.get("monte_carlo_score")
    print(f"  WF score   : {_score_line(wf, _WF_PASS_GATE, _WF_FAIL_GATE)}")
    print(f"  MC score   : {_score_line(mc, _MC_PASS_GATE, _MC_FAIL_GATE)}")
    print()
    print(f"  Strengths  : {len(pkg.strengths)}")
    for s in pkg.strengths:
        print(f"    + {s}")
    print()

    hard = [b for b in pkg.blockers if b["severity"] == "BLOCKER"]
    soft = [b for b in pkg.blockers if b["severity"] == "WARNING"]
    print(f"  Blockers   : {len(hard)}  Warnings: {len(soft)}")
    for b in hard:
        print(f"    [BLOCKER] {b['message']}")
    for b in soft:
        print(f"    [WARNING] {b['message']}")
    print()
    print(f"  Readiness  : {icon} {pkg.readiness_status}")
    print(f"  Action     : {pkg.required_action}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Unified Decision Package -- consolidates all research evidence. "
            "No DB writes. No live trading. REVIEW_REQUIRED is terminal."
        ),
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec-id", type=int, metavar="ID",
                     help="Generate decision package for one spec")
    grp.add_argument("--all", action="store_true",
                     help="Generate packages for all scored specs")
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
    print(f"Hermes Decision Package Generator  [{mode}]")
    print(f"  DB          : {db_path}")
    if not args.dry_run:
        print(f"  Reports dir : {reports_dir}")
    print()

    conn = sqlite3.connect(str(db_path))
    try:
        if args.spec_id is not None:
            spec_ids = [args.spec_id]
        else:
            spec_ids = decision_queue(conn)
            if not spec_ids:
                print("No scored specs in decision queue.")
                sys.exit(0)

        packages: List[DecisionPackage] = []
        skipped:  List[Tuple[int, str]] = []

        for sid in spec_ids:
            pkg, err = generate_decision_package(conn, sid)
            if pkg is None:
                skipped.append((sid, err or "unknown"))
                print(f"  SKIP spec_id={sid}: {err}")
                print()
                continue

            _print_package(pkg, dry_run=args.dry_run)

            if not args.dry_run:
                md_path, json_path = write_package(pkg, reports_dir)
                print(f"  Reports")
                print(f"    MD  : {md_path}")
                print(f"    JSON: {json_path}")
                print()

            packages.append(pkg)

        # --all summary table
        if args.all and packages:
            print("=" * 72)
            print("DECISION QUEUE SUMMARY")
            print("=" * 72)
            print()
            by_status: Dict[str, List[DecisionPackage]] = {}
            for p in packages:
                by_status.setdefault(p.readiness_status, []).append(p)

            order = [
                "READY_FOR_HUMAN_REVIEW",
                "NEEDS_REAL_NT8_EXPORT",
                "NEEDS_MORE_TRADES",
                "NEEDS_WALK_FORWARD",
                "NEEDS_MONTE_CARLO",
                "NEEDS_REGIME_ANALYSIS",
                "REJECT_RESEARCH_CANDIDATE",
            ]
            for status in order:
                group = by_status.get(status, [])
                if not group:
                    continue
                icon = _STATUS_ICON.get(status, "[?]")
                print(f"  {icon} {status} ({len(group)})")
                for p in group:
                    hard = sum(1 for b in p.blockers if b["severity"] == "BLOCKER")
                    print(f"    spec_id={p.spec_id}  {p.spec_name}  blockers={hard}")
                print()

            if skipped:
                print(f"  Skipped ({len(skipped)}):")
                for sid, reason in skipped:
                    print(f"    spec_id={sid}: {reason}")
                print()

        if args.dry_run:
            print("DRY-RUN complete. No files written. No DB changes.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
