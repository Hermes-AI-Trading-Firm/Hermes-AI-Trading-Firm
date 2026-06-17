#!/usr/bin/env python3
"""
Research Question Engine -- research/questions/question_engine.py

Phase 33

Question: "Given everything in the knowledge graph,
           what is the single most important unanswered question?"

The Three-Question Test:
  Can it produce better evidence?   YES -- identifies what remains unknown
  Can it ask a better question?     YES -- this IS the question-sharpening layer
  Does a human still decide?        YES -- the engine asks; it never answers

The engine reads all accumulated evidence and produces the questions
a human reviewer most needs to answer. Not recommendations. Not verdicts.
Questions.

What it does NOT do
-------------------
- Does not answer its own questions
- Does not recommend approve or reject
- Does not change strategy state
- Does not modify scores or reports
- Does not write to the database
- Does not advance any strategy past REVIEW_REQUIRED

Question categories
-------------------
  DATA_QUALITY          -- trade list, audit integrity, data coverage
  SAMPLE_SIZE           -- trade count sufficiency
  OOS_WALK_FORWARD      -- out-of-sample validation gap
  MONTE_CARLO           -- robustness under randomness
  REGIME_DEPENDENCY     -- market condition concentration
  PROP_FIRM_RISK        -- drawdown compliance
  ARCHETYPE_WEAKNESS    -- known weaknesses for this archetype
  PARAMETER_SENSITIVITY -- overfit / sensitivity concern
  EXECUTION_ASSUMPTIONS -- slippage, fill, cost assumptions
  RESEARCH_PRIORITY     -- lifecycle bottleneck or prioritization

Question fields
---------------
  question                  -- the precise question to answer
  why_it_matters            -- what answering this unlocks
  evidence_behind_it        -- what we currently know
  missing_evidence          -- what we do not know
  suggested_action          -- the most direct path to an answer
  priority                  -- HIGH / MEDIUM / LOW
  affects_review_required   -- True if this is a gate-level unknown

Usage
-----
    python -m research.questions.question_engine --spec-id N
    python -m research.questions.question_engine --all
    python -m research.questions.question_engine --global
    python -m research.questions.question_engine --dry-run
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
from typing import Dict, List, Optional, Set, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB          = _PROJECT_ROOT / "database"  / "hermes_research.db"
REPORTS_DIR         = _PROJECT_ROOT / "reports"   / "questions"
AUDIT_DIR           = _PROJECT_ROOT / "reports"   / "audits"
VALIDATION_DIR      = _PROJECT_ROOT / "reports"   / "validation"
REGIME_DIR          = _PROJECT_ROOT / "reports"   / "regime"
DECISION_PKG_DIR    = _PROJECT_ROOT / "reports"   / "decision_packages"
LEARNING_DIR        = _PROJECT_ROOT / "reports"   / "learning"
CLASSIFICATION_PATH = _PROJECT_ROOT / "research"  / "archetype" / "classifications.json"
PATTERN_LIB_PATH    = _PROJECT_ROOT / "research"  / "memory"    / "pattern_library.json"
ARCHETYPES_PATH     = _PROJECT_ROOT / "research"  / "archetype" / "archetypes.json"
APPROVED_DIR        = _PROJECT_ROOT / "research"  / "approved"
REJECTED_DIR        = _PROJECT_ROOT / "research"  / "rejected"

_PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# Gates required for REVIEW_REQUIRED
_MC_PASS_GATE = 0.85
_MC_FAIL_GATE = 0.70
_WF_PASS_GATE = 0.70
_WF_FAIL_GATE = 0.50
_MIN_TRADES   = 30
_SOFT_TRADES  = 100
_DD_PROP_LIMIT = 0.05


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class QuestionContext:
    spec_id:               int
    spec_name:             str
    symbol:                str
    timeframe:             str
    archetype_id:          str
    archetype_label:       str
    has_backtest:          bool
    has_trade_list:        bool
    trade_count:           Optional[int]
    net_profit:            Optional[float]
    profit_factor:         Optional[float]
    win_rate:              Optional[float]
    max_drawdown_pct:      Optional[float]
    has_scoring:           bool
    composite_score:       Optional[float]
    composite_grade:       Optional[str]
    wf_score:              Optional[float]
    wf_pass:               Optional[bool]
    mc_score:              Optional[float]
    mc_pass:               Optional[bool]
    mc_prob_positive:      Optional[float]
    prop_firm_supported:   Optional[bool]
    overfitting_risk:      Optional[float]
    has_audit:             bool
    audit_fail_count:      int
    audit_warn_count:      int
    audit_recommendation:  Optional[str]
    has_regime:            bool
    regime_window_count:   int
    regime_best:           Optional[str]
    regime_worst:          Optional[str]
    has_decision_pkg:      bool
    pkg_readiness:         Optional[str]
    pkg_blocker_count:     int
    has_learning:          bool
    failure_patterns:      List[str]
    strength_patterns:     List[str]
    lifecycle_state:       str
    human_approved:        bool
    human_rejected:        bool


@dataclass
class ResearchQuestion:
    question_id:             str
    category:                str
    question:                str
    why_it_matters:          str
    evidence_behind_it:      str
    missing_evidence:        str
    suggested_action:        str
    priority:                str   # HIGH / MEDIUM / LOW
    affects_review_required: bool
    spec_id:                 Optional[int]  # None for global
    spec_name:               Optional[str]
    generated_at:            str   = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))


# ---------------------------------------------------------------------------
# File helpers
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


def _any_file(directory: Path, prefix: str) -> bool:
    if not directory.exists():
        return False
    return any(directory.glob(f"{prefix}*"))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _all_scored_specs(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute("""
        SELECT DISTINCT s.spec_id, s.spec_name,
               COALESCE(s.symbol,''), COALESCE(s.timeframe,'')
        FROM strategy_specs s
        JOIN scoring_results sc ON sc.spec_id = s.spec_id
        ORDER BY s.spec_id
    """).fetchall()
    return [{"spec_id": r[0], "spec_name": r[1],
             "symbol": r[2], "timeframe": r[3]} for r in rows]


def _load_backtest(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT net_profit, profit_factor, win_rate, total_trades,
               max_drawdown_pct,
               trade_list_json IS NOT NULL AND trade_list_json != '' AS has_tl
        FROM backtests
        WHERE spec_id = ? AND is_in_sample = 1
        ORDER BY backtest_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    return {"net_profit": row[0], "profit_factor": row[1], "win_rate": row[2],
            "total_trades": row[3], "max_drawdown_pct": row[4],
            "has_trade_list": bool(row[5])}


def _load_score(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT composite_score, grade, walk_forward_score, walk_forward_pass,
               monte_carlo_score, monte_carlo_pass, prop_firm_supported,
               overfitting_risk
        FROM scoring_results
        WHERE spec_id = ? ORDER BY scoring_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    return {"composite_score": row[0], "grade": row[1],
            "wf_score": row[2], "wf_pass": bool(row[3]),
            "mc_score": row[4], "mc_pass": bool(row[5]),
            "prop_firm_supported": bool(row[6]), "overfitting_risk": row[7]}


def _infer_lifecycle(conn: sqlite3.Connection, spec_id: int,
                     safe_name: str, score: Optional[Dict]) -> str:
    if _any_file(APPROVED_DIR, safe_name): return "HUMAN_APPROVED"
    if _any_file(REJECTED_DIR, safe_name): return "HUMAN_REJECTED"
    pkg = _latest_json(DECISION_PKG_DIR, safe_name, "_decision_package_")
    if pkg:
        return "REVIEW_REQUIRED" if pkg.get("readiness_status") == "READY_FOR_HUMAN_REVIEW" \
               else "DECISION_PACKAGED"
    if _latest_json(REGIME_DIR, safe_name, "_regime_analysis_"): return "REGIME_ANALYZED"
    wf = (score or {}).get("wf_score")
    if wf is not None or _latest_json(VALIDATION_DIR, safe_name, "_walk_forward_"):
        return "VALIDATED_WF"
    mc = (score or {}).get("mc_score")
    if mc is not None or _latest_json(VALIDATION_DIR, safe_name, "_monte_carlo_"):
        return "VALIDATED_MC"
    if _latest_json(AUDIT_DIR, safe_name, "_"): return "AUDITED"
    if score: return "SCORED"
    if conn.execute("SELECT 1 FROM backtests WHERE spec_id=? LIMIT 1",
                    (spec_id,)).fetchone(): return "BACKTEST_IMPORTED"
    return "SPEC_IMPORTED"


# ---------------------------------------------------------------------------
# Context collection
# ---------------------------------------------------------------------------

def collect_question_context(
    conn:            sqlite3.Connection,
    spec:            Dict,
    classifications: Dict,
    pattern_lib:     Dict,
) -> QuestionContext:
    spec_id = spec["spec_id"]
    name    = spec["spec_name"]
    safe    = _safe(name)

    bt  = _load_backtest(conn, spec_id) or {}
    sc  = _load_score(conn, spec_id)    or {}

    clsf       = classifications.get("classifications", {}).get(str(spec_id), {})
    arch_id    = clsf.get("primary",       "unknown")
    arch_label = clsf.get("primary_label", "Unknown")

    audit  = _latest_json(AUDIT_DIR, safe, "_") or {}
    mc_d   = _latest_json(VALIDATION_DIR, safe, "_monte_carlo_") or {}
    regime = _latest_json(REGIME_DIR, safe, "_regime_analysis_") or {}
    pkg    = _latest_json(DECISION_PKG_DIR, safe, "_decision_package_") or {}
    lr     = _latest_json(LEARNING_DIR, safe, "_learning_review_") or {}

    pat_rec = (pattern_lib.get("strategy_records", {}).get(str(spec_id))
               or pattern_lib.get("strategy_records", {}).get(spec_id) or {})

    lc = _infer_lifecycle(conn, spec_id, safe, sc)

    return QuestionContext(
        spec_id             = spec_id,
        spec_name           = name,
        symbol              = spec.get("symbol", ""),
        timeframe           = spec.get("timeframe", ""),
        archetype_id        = arch_id,
        archetype_label     = arch_label,
        has_backtest        = bool(bt),
        has_trade_list      = bool(bt.get("has_trade_list")),
        trade_count         = bt.get("total_trades"),
        net_profit          = bt.get("net_profit"),
        profit_factor       = bt.get("profit_factor"),
        win_rate            = bt.get("win_rate"),
        max_drawdown_pct    = bt.get("max_drawdown_pct"),
        has_scoring         = bool(sc),
        composite_score     = sc.get("composite_score"),
        composite_grade     = sc.get("grade"),
        wf_score            = sc.get("wf_score"),
        wf_pass             = sc.get("wf_pass"),
        mc_score            = sc.get("mc_score"),
        mc_pass             = sc.get("mc_pass"),
        mc_prob_positive    = mc_d.get("probability_positive"),
        prop_firm_supported = sc.get("prop_firm_supported"),
        overfitting_risk    = sc.get("overfitting_risk"),
        has_audit           = bool(audit),
        audit_fail_count    = audit.get("fail_count", 0),
        audit_warn_count    = audit.get("warn_count", 0),
        audit_recommendation = audit.get("recommendation"),
        has_regime          = bool(regime),
        regime_window_count = len(regime.get("windows", [])),
        regime_best         = regime.get("best_window"),
        regime_worst        = regime.get("worst_window"),
        has_decision_pkg    = bool(pkg),
        pkg_readiness       = pkg.get("readiness_status"),
        pkg_blocker_count   = sum(1 for b in pkg.get("blockers", [])
                                  if b.get("severity") == "BLOCKER"),
        has_learning        = bool(lr),
        failure_patterns    = [
            p.get("pattern_id", str(p)) if isinstance(p, dict) else str(p)
            for p in pat_rec.get("failure_patterns", [])
        ],
        strength_patterns   = [
            p.get("pattern_id", str(p)) if isinstance(p, dict) else str(p)
            for p in pat_rec.get("strength_patterns", [])
        ],
        lifecycle_state     = lc,
        human_approved      = _any_file(APPROVED_DIR, safe),
        human_rejected      = _any_file(REJECTED_DIR, safe),
    )


# ---------------------------------------------------------------------------
# Question generators (per-strategy)
# ---------------------------------------------------------------------------

def _q(spec_id, spec_name, qid, cat, question, why, evidence,
       missing, action, priority, affects_rr) -> ResearchQuestion:
    return ResearchQuestion(
        question_id             = qid,
        category                = cat,
        question                = question,
        why_it_matters          = why,
        evidence_behind_it      = evidence,
        missing_evidence        = missing,
        suggested_action        = action,
        priority                = priority,
        affects_review_required = affects_rr,
        spec_id                 = spec_id,
        spec_name               = spec_name,
    )


def _gen_data_quality(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    if not ctx.has_backtest:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "dq_no_backtest", "DATA_QUALITY",
            f"Has {ctx.spec_name} been tested against real market data?",
            "Without a real backtest, no evidence exists to evaluate.",
            "No backtest imported.",
            "Trade list, entry/exit prices, and performance metrics.",
            "Import a real NT8 backtest export.",
            "HIGH", True))
    elif not ctx.has_trade_list:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "dq_no_trade_list", "DATA_QUALITY",
            f"Is the {ctx.spec_name} backtest based on real NT8 export data?",
            "Without trade_list_json, individual trade analysis and Monte Carlo are blocked.",
            f"Backtest exists but trade_list_json is missing.",
            "Individual trade records with entry, exit, P&L per trade.",
            "Re-import using the NT8 export pipeline with --initial-capital.",
            "HIGH", True))
    if ctx.has_audit and ctx.audit_fail_count > 0:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "dq_audit_fails", "DATA_QUALITY",
            f"What caused the {ctx.audit_fail_count} FAIL finding(s) in the {ctx.spec_name} audit?",
            "Unresolved audit failures are hard blockers for REVIEW_REQUIRED.",
            f"Audit found {ctx.audit_fail_count} FAIL(s), "
            f"{ctx.audit_warn_count} WARN(s). "
            f"Recommendation: {ctx.audit_recommendation or 'see audit report'}.",
            "Root cause and resolution for each FAIL finding.",
            "Run: python -m research.audit.strategy_auditor --spec-id "
            f"{ctx.spec_id} and resolve each FAIL.",
            "HIGH", True))
    return qs


def _gen_sample_size(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    tc = ctx.trade_count or 0
    if 0 < tc < _MIN_TRADES:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "ss_below_minimum", "SAMPLE_SIZE",
            f"Is {tc} trades enough to evaluate {ctx.spec_name} with any confidence?",
            f"The minimum required is {_MIN_TRADES} trades. "
            "Results below this threshold are statistically unreliable.",
            f"Current trade count: {tc}.",
            f"At least {_MIN_TRADES - tc} more trades to reach minimum; "
            f"{_SOFT_TRADES - tc} more to reach the recommended {_SOFT_TRADES}.",
            "Extend the backtest period or lower the signal threshold to increase trade frequency.",
            "HIGH", True))
    elif _MIN_TRADES <= tc < _SOFT_TRADES:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "ss_below_soft", "SAMPLE_SIZE",
            f"Does {tc} trades give {ctx.spec_name} sufficient statistical power?",
            f"{_SOFT_TRADES}+ trades are recommended. "
            "At {tc} trades, individual outlier trades have outsized influence on metrics.",
            f"Trade count: {tc}. Above hard minimum ({_MIN_TRADES}) but below recommended ({_SOFT_TRADES}).",
            f"{_SOFT_TRADES - tc} additional trades.",
            "Extend backtest period or consider whether trade frequency is a structural weakness.",
            "MEDIUM", False))
    return qs


def _gen_oos_wf(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    if ctx.wf_score is None:
        sc_str = (f"composite score {ctx.composite_score:.1f} (Grade {ctx.composite_grade})"
                  if ctx.composite_score else "no composite score")
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "oos_missing", "OOS_WALK_FORWARD",
            f"Does {ctx.spec_name} maintain its performance on data it has never seen?",
            "In-sample performance cannot be trusted without OOS validation. "
            "This is the most common source of research failure.",
            f"{ctx.spec_name} has {sc_str} in-sample but no OOS backtest has been imported.",
            "Walk-forward score, OOS profit factor, OOS trade count, IS/OOS retention ratios.",
            f"Import an OOS backtest (--oos flag) then run the walk-forward engine.",
            "HIGH", True))
    elif ctx.wf_score is not None and ctx.wf_score < _WF_PASS_GATE:
        if ctx.wf_score < _WF_FAIL_GATE:
            qs.append(_q(ctx.spec_id, ctx.spec_name,
                "oos_fail", "OOS_WALK_FORWARD",
                f"Why does {ctx.spec_name} fail walk-forward validation with score {ctx.wf_score:.4f}?",
                "A WF score below 0.50 means OOS performance is severely degraded. "
                "This is a hard rejection signal.",
                f"WF score: {ctx.wf_score:.4f} (FAIL threshold: {_WF_FAIL_GATE}). "
                "OOS performance is severely below IS performance.",
                "Root cause: overfitting, regime change, or structural strategy failure.",
                "Investigate IS vs OOS period differences. Consider whether the strategy "
                "is curve-fit to the IS period.",
                "HIGH", True))
        else:
            qs.append(_q(ctx.spec_id, ctx.spec_name,
                "oos_warn", "OOS_WALK_FORWARD",
                f"Is the walk-forward degradation in {ctx.spec_name} (score {ctx.wf_score:.4f}) acceptable?",
                "WF score is in the warning zone (0.50-0.69). "
                "Performance degrades OOS but does not fail outright.",
                f"WF score: {ctx.wf_score:.4f}. Between WARN ({_WF_FAIL_GATE}) and PASS ({_WF_PASS_GATE}) thresholds.",
                "Whether degradation is due to overfitting, regime shift, or normal IS/OOS variance.",
                "Review IS vs OOS period characteristics. Test on an additional OOS window.",
                "MEDIUM", False))
    return qs


def _gen_monte_carlo(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    if ctx.mc_score is None:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "mc_missing", "MONTE_CARLO",
            f"Is {ctx.spec_name} robust to random variation in trade order and timing?",
            "Monte Carlo tests whether results depend on a specific lucky sequence of trades. "
            "Without it, survival under adverse conditions is unknown.",
            f"{ctx.spec_name} has no Monte Carlo result. "
            f"Trade count: {ctx.trade_count or 'unknown'}.",
            "Survival rate across 1,000+ bootstrap simulations, probability of positive outcome, worst-case drawdown distribution.",
            f"Run: python -m research.validation.monte_carlo --spec-id {ctx.spec_id}",
            "HIGH", True))
    elif ctx.mc_score < _MC_FAIL_GATE:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "mc_fail", "MONTE_CARLO",
            f"Why does {ctx.spec_name} fail Monte Carlo with survival rate {ctx.mc_score:.1%}?",
            "Survival below 70% means results are highly sequence-dependent. "
            "Performance in live trading would be unreliable.",
            f"MC survival: {ctx.mc_score:.1%} (FAIL threshold: {_MC_FAIL_GATE:.0%}). "
            f"Probability positive: {ctx.mc_prob_positive:.1%}" if ctx.mc_prob_positive
            else f"MC survival: {ctx.mc_score:.1%} (FAIL threshold: {_MC_FAIL_GATE:.0%}).",
            "Root cause of sequence sensitivity.",
            "Review drawdown concentration. Consider whether a small number of trades drive all profit.",
            "HIGH", True))
    elif ctx.mc_score < _MC_PASS_GATE:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "mc_warn", "MONTE_CARLO",
            f"Is a Monte Carlo survival rate of {ctx.mc_score:.1%} acceptable for {ctx.spec_name}?",
            f"Survival is above the FAIL gate ({_MC_FAIL_GATE:.0%}) but below PASS ({_MC_PASS_GATE:.0%}). "
            "Live performance may be more variable than in-sample metrics suggest.",
            f"MC survival: {ctx.mc_score:.1%}.",
            "Whether this survival rate is acceptable given the account size and risk tolerance.",
            "Collect more trades to improve bootstrap stability. Review worst-case drawdown scenarios.",
            "MEDIUM", False))
    return qs


def _gen_regime(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    if not ctx.has_regime:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "regime_missing", "REGIME_DEPENDENCY",
            f"Under which market regimes does {ctx.spec_name} actually work?",
            "A strategy that only works in one regime is not robust. "
            "Regime dependency is invisible without explicit analysis.",
            f"{ctx.spec_name} has no regime analysis. "
            f"All {ctx.trade_count or '?'} trades are treated as a single homogeneous period.",
            "Performance breakdown by bull, bear, sideways, and transition regimes.",
            f"Run: python -m research.regime.regime_analyzer --spec-id {ctx.spec_id}",
            "MEDIUM", False))
    elif ctx.regime_window_count <= 1:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "regime_single_window", "REGIME_DEPENDENCY",
            f"Does {ctx.spec_name} work outside the single regime window analyzed?",
            "With only one regime window, there is no evidence the strategy survives a regime change.",
            f"Regime analysis exists but covers only {ctx.regime_window_count} window(s). "
            f"Best: {ctx.regime_best or '?'}.",
            "Performance across at least 2-3 distinct regime periods.",
            "Extend the backtest period to cover a broader range of market conditions.",
            "MEDIUM", False))
    return qs


def _gen_prop_firm(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    dd = ctx.max_drawdown_pct
    if dd is not None and dd > _DD_PROP_LIMIT:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "pf_drawdown", "PROP_FIRM_RISK",
            f"Does {ctx.spec_name} meet prop-firm drawdown limits with a {dd:.1%} max drawdown?",
            f"Standard prop-firm trailing drawdown limits are around {_DD_PROP_LIMIT:.0%}. "
            "Breaching this in a funded account ends the challenge.",
            f"Max drawdown: {dd:.1%}. Prop-firm limit: {_DD_PROP_LIMIT:.0%}. "
            f"Prop-firm supported: {ctx.prop_firm_supported}.",
            "Drawdown breach probability under Monte Carlo simulation across a funded account lifecycle.",
            "Add a drawdown filter rule. Test whether a tighter per-trade stop reduces drawdown below the limit.",
            "MEDIUM", False))
    return qs


def _gen_archetype(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    if ctx.archetype_id in ("unknown", "hybrid"):
        return qs

    _KNOWN_WEAKNESSES: Dict[str, str] = {
        "orb":              "OOS retention below 75 trades and regime concentration in open-session volatility",
        "vwap_pullback":    "lower profit factor but more stable MC survival than other archetypes",
        "fvg_continuation": "highest drawdown variability in the archetype",
        "mean_reversion":   "regime dependency -- underperforms in strong trending markets",
        "trend_following":  "whipsaw losses in sideways regimes",
        "liquidity_sweep":  "execution slippage sensitivity at sweep entry points",
    }
    weakness = _KNOWN_WEAKNESSES.get(ctx.archetype_id)
    if weakness:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            f"arch_{ctx.archetype_id}_weakness", "ARCHETYPE_WEAKNESS",
            f"Does {ctx.spec_name} exhibit the known weakness of {ctx.archetype_label} strategies: {weakness}?",
            f"Pattern library priors for {ctx.archetype_label} identify this as a recurring failure mode.",
            f"Archetype: {ctx.archetype_label}. Known prior: {weakness}.",
            f"Direct evidence that this strategy is or is not affected by this weakness.",
            "Review regime analysis and WF results specifically for evidence of this pattern.",
            "MEDIUM", False))
    return qs


def _gen_parameter_sensitivity(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    of_risk = ctx.overfitting_risk
    if of_risk is not None and of_risk > 0.5:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "param_overfit", "PARAMETER_SENSITIVITY",
            f"Is {ctx.spec_name} overfit to its current parameter values (risk score {of_risk:.2f})?",
            "High overfitting risk means small parameter changes may produce very different results.",
            f"Overfitting risk score: {of_risk:.2f}. Composite score: {ctx.composite_score}.",
            "Parameter sensitivity analysis showing performance across a range of values.",
            "Run a parameter sweep across the key entry/exit parameters.",
            "MEDIUM", False))
    if ctx.composite_score and ctx.composite_score >= 80 and ctx.wf_score is None:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "param_high_is_no_oos", "PARAMETER_SENSITIVITY",
            f"Is the grade {ctx.composite_grade} IS score for {ctx.spec_name} genuine edge "
            f"or in-sample optimization?",
            f"A score of {ctx.composite_score:.1f} without OOS validation is the classic overfitting signature.",
            f"Composite score: {ctx.composite_score:.1f} (Grade {ctx.composite_grade}). No OOS backtest.",
            "OOS performance on at least one unseen period.",
            f"Import OOS backtest (--oos) and run walk-forward engine.",
            "HIGH", True))
    return qs


def _gen_execution(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    if ctx.has_backtest and ctx.trade_count and ctx.trade_count >= _MIN_TRADES:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "exec_assumptions", "EXECUTION_ASSUMPTIONS",
            f"What slippage and commission assumptions are embedded in the {ctx.spec_name} backtest?",
            "Execution costs can eliminate profitable strategies in live trading. "
            "Assumptions that are too optimistic produce backtest results that cannot be replicated.",
            f"Backtest has {ctx.trade_count} trades with net profit "
            f"{'$' + f'{ctx.net_profit:,.0f}' if ctx.net_profit else 'unknown'}.",
            "Confirmed slippage model, commission rates, and fill assumptions per instrument.",
            "Review NT8 export configuration for slippage and commission settings. "
            "Compare to live market execution data.",
            "LOW", False))
    return qs


def _gen_research_priority(ctx: QuestionContext) -> List[ResearchQuestion]:
    qs = []
    stuck_states = {"SCORED", "AUDITED", "BACKTEST_IMPORTED", "SPEC_IMPORTED"}
    if ctx.lifecycle_state in stuck_states:
        qs.append(_q(ctx.spec_id, ctx.spec_name,
            "priority_stuck", "RESEARCH_PRIORITY",
            f"Why is {ctx.spec_name} still at {ctx.lifecycle_state} with no validation evidence?",
            "Strategies that stall in early lifecycle states never generate the evidence "
            "needed for human review.",
            f"Lifecycle state: {ctx.lifecycle_state}. No MC, WF, or regime evidence found.",
            "The reason this strategy has not progressed to VALIDATED_MC and beyond.",
            "Schedule the next research step and assign it a deadline.",
            "MEDIUM", False))
    return qs


# ---------------------------------------------------------------------------
# identify_unknowns / rank
# ---------------------------------------------------------------------------

_GENERATORS = [
    _gen_data_quality,
    _gen_sample_size,
    _gen_oos_wf,
    _gen_monte_carlo,
    _gen_regime,
    _gen_prop_firm,
    _gen_archetype,
    _gen_parameter_sensitivity,
    _gen_execution,
    _gen_research_priority,
]


def identify_unknowns(ctx: QuestionContext) -> List[ResearchQuestion]:
    questions: List[ResearchQuestion] = []
    for gen in _GENERATORS:
        questions.extend(gen(ctx))
    return questions


def rank_unanswered_questions(questions: List[ResearchQuestion]) -> List[ResearchQuestion]:
    def sort_key(q: ResearchQuestion) -> Tuple:
        rr_flag = 0 if q.affects_review_required else 1
        pr_rank = _PRIORITY_RANK.get(q.priority, 9)
        return (rr_flag, pr_rank, q.question_id)
    return sorted(questions, key=sort_key)


# ---------------------------------------------------------------------------
# Per-strategy entry point
# ---------------------------------------------------------------------------

def generate_strategy_questions(
    conn:            sqlite3.Connection,
    spec_id:         int,
    classifications: Dict,
    pattern_lib:     Dict,
) -> Tuple[Optional[QuestionContext], List[ResearchQuestion]]:
    row = conn.execute(
        "SELECT spec_id, spec_name, COALESCE(symbol,''), COALESCE(timeframe,'') "
        "FROM strategy_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if not row:
        return None, []
    spec = {"spec_id": row[0], "spec_name": row[1],
            "symbol": row[2], "timeframe": row[3]}
    ctx  = collect_question_context(conn, spec, classifications, pattern_lib)
    qs   = identify_unknowns(ctx)
    return ctx, rank_unanswered_questions(qs)


# ---------------------------------------------------------------------------
# Global (cross-strategy) questions
# ---------------------------------------------------------------------------

def generate_global_questions(
    all_contexts: List[QuestionContext],
) -> List[ResearchQuestion]:
    qs: List[ResearchQuestion] = []
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Shared failure patterns
    pat_to_specs: Dict[str, List[str]] = {}
    for ctx in all_contexts:
        for pat in ctx.failure_patterns:
            pat_key = str(pat)[:60]
            pat_to_specs.setdefault(pat_key, []).append(ctx.spec_name)
    for pat, names in pat_to_specs.items():
        if len(names) >= 2:
            qs.append(ResearchQuestion(
                question_id             = f"global_shared_pattern_{_safe(pat[:30])}",
                category                = "DATA_QUALITY",
                question                = (
                    f"Why do {len(names)} strategies share the failure pattern "
                    f"'{pat[:80]}'? Is this systemic or coincidental?"
                ),
                why_it_matters          = (
                    "Shared failure patterns suggest a systemic research pipeline issue, "
                    "not individual strategy weakness."
                ),
                evidence_behind_it      = f"Pattern found in: {', '.join(names)}.",
                missing_evidence        = "Root cause -- instrument, pipeline, or strategy class.",
                suggested_action        = (
                    "Review whether the pattern originates from data, the scoring pipeline, "
                    "or the strategy class itself."
                ),
                priority                = "HIGH",
                affects_review_required = False,
                spec_id                 = None,
                spec_name               = None,
                generated_at            = now,
            ))

    # Lifecycle bottlenecks
    state_counts: Dict[str, List[str]] = {}
    for ctx in all_contexts:
        state_counts.setdefault(ctx.lifecycle_state, []).append(ctx.spec_name)
    for state, names in state_counts.items():
        if len(names) >= 2 and state not in ("REVIEW_REQUIRED", "HUMAN_APPROVED",
                                               "HUMAN_REJECTED", "ARCHIVED",
                                               "DECISION_PACKAGED"):
            qs.append(ResearchQuestion(
                question_id             = f"global_bottleneck_{state}",
                category                = "RESEARCH_PRIORITY",
                question                = (
                    f"Why are {len(names)} strategies stalled at {state}: "
                    f"{', '.join(names)}? What is the bottleneck?"
                ),
                why_it_matters          = (
                    "Multiple strategies stuck at the same state suggests a pipeline "
                    "gap or resource bottleneck."
                ),
                evidence_behind_it      = f"{len(names)} strategies at {state}.",
                missing_evidence        = "The reason none have progressed beyond this state.",
                suggested_action        = (
                    f"Identify the next research step for each and assign it. "
                    f"Consider scheduling a batch pipeline run."
                ),
                priority                = "MEDIUM",
                affects_review_required = False,
                spec_id                 = None,
                spec_name               = None,
                generated_at            = now,
            ))

    # OOS coverage gap portfolio-wide
    missing_oos = [ctx.spec_name for ctx in all_contexts
                   if ctx.wf_score is None and ctx.has_backtest]
    if len(missing_oos) >= 2:
        qs.append(ResearchQuestion(
            question_id             = "global_oos_gap",
            category                = "OOS_WALK_FORWARD",
            question                = (
                f"{len(missing_oos)} strategies have no OOS validation: "
                f"{', '.join(missing_oos)}. "
                "Is this a pipeline gap or a deliberate research pause?"
            ),
            why_it_matters          = (
                "Without OOS validation, no strategy in the firm can be trusted. "
                "This is the most critical cross-portfolio gap."
            ),
            evidence_behind_it      = f"{len(missing_oos)} strategies lack OOS backtest imports.",
            missing_evidence        = "OOS performance for all affected strategies.",
            suggested_action        = (
                "Prioritise OOS backtest imports for all strategies with IS backtests. "
                "Run walk-forward engine after each import."
            ),
            priority                = "HIGH",
            affects_review_required = True,
            spec_id                 = None,
            spec_name               = None,
            generated_at            = now,
        ))

    # Archetype diversity
    archetypes_represented: Set[str] = {ctx.archetype_id for ctx in all_contexts
                                         if ctx.archetype_id not in ("unknown", "hybrid")}
    if len(archetypes_represented) <= 2 and len(all_contexts) >= 3:
        qs.append(ResearchQuestion(
            question_id             = "global_archetype_concentration",
            category                = "RESEARCH_PRIORITY",
            question                = (
                f"The research portfolio covers only {len(archetypes_represented)} archetype(s). "
                "Is archetype concentration a deliberate focus or a coverage gap?"
            ),
            why_it_matters          = (
                "Over-concentration in one archetype creates correlated drawdowns "
                "and regime dependency at the portfolio level."
            ),
            evidence_behind_it      = (
                f"{len(all_contexts)} strategies across "
                f"{len(archetypes_represented)} archetype(s): "
                f"{', '.join(archetypes_represented)}."
            ),
            missing_evidence        = "Strategies from uncovered archetypes.",
            suggested_action        = (
                "Review Market Selection Desk output for underrepresented archetypes. "
                "Consider whether current concentration is intentional."
            ),
            priority                = "LOW",
            affects_review_required = False,
            spec_id                 = None,
            spec_name               = None,
            generated_at            = now,
        ))

    return rank_unanswered_questions(qs)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _q_to_dict(q: ResearchQuestion) -> Dict:
    return {
        "question_id":             q.question_id,
        "category":                q.category,
        "priority":                q.priority,
        "affects_review_required": q.affects_review_required,
        "spec_id":                 q.spec_id,
        "spec_name":               q.spec_name,
        "question":                q.question,
        "why_it_matters":          q.why_it_matters,
        "evidence_behind_it":      q.evidence_behind_it,
        "missing_evidence":        q.missing_evidence,
        "suggested_action":        q.suggested_action,
        "generated_at":            q.generated_at,
    }


def export_questions_json(
    questions:   List[ResearchQuestion],
    path:        Path,
    scope:       str = "strategy",
    spec_name:   Optional[str] = None,
) -> None:
    path.write_text(
        json.dumps({
            "scope":      scope,
            "spec_name":  spec_name,
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "total":      len(questions),
            "high":       sum(1 for q in questions if q.priority == "HIGH"),
            "medium":     sum(1 for q in questions if q.priority == "MEDIUM"),
            "low":        sum(1 for q in questions if q.priority == "LOW"),
            "gate_level": sum(1 for q in questions if q.affects_review_required),
            "questions":  [_q_to_dict(q) for q in questions],
        }, indent=2),
        encoding="utf-8",
    )


def export_questions_markdown(
    questions:   List[ResearchQuestion],
    path:        Path,
    title:       str,
    scope:       str = "strategy",
) -> None:
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    p(f"# {title}")
    p(f"**Generated:** {datetime.now().strftime('%Y-%m-%d')}  |  "
      f"**Questions:** {len(questions)}")
    p()
    p("> The engine asks. It does not answer.")
    p("> Accumulate evidence. Sharpen the question. Human decides.")
    p()
    p("---")
    p()

    high   = [q for q in questions if q.priority == "HIGH"]
    medium = [q for q in questions if q.priority == "MEDIUM"]
    low    = [q for q in questions if q.priority == "LOW"]

    for group, label in [(high, "HIGH Priority"), (medium, "MEDIUM Priority"), (low, "LOW Priority")]:
        if not group:
            continue
        p(f"## {label} ({len(group)})")
        p()
        for i, q in enumerate(group, 1):
            rr = " -- **GATE LEVEL**" if q.affects_review_required else ""
            p(f"### {i}. [{q.category}]{rr}")
            p()
            p(f"**{q.question}**")
            p()
            p(f"**Why it matters:** {q.why_it_matters}")
            p()
            p(f"**Evidence behind it:** {q.evidence_behind_it}")
            p()
            p(f"**Missing evidence:** {q.missing_evidence}")
            p()
            p(f"**Suggested action:** {q.suggested_action}")
            p()
            if q.spec_name:
                p(f"*Strategy: {q.spec_name}  (spec_id={q.spec_id})*")
            p()

    p("---")
    p()
    p("*Read-only advisory output. No database writes. No strategy changes.*")
    p("*REVIEW\\_REQUIRED is the terminal automated state.*")
    p("*The engine asks questions. Humans answer them.*")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_strategy_report(
    questions:   List[ResearchQuestion],
    spec_name:   str,
    reports_dir: Path,
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y%m%d")
    safe      = _safe(spec_name)
    md_path   = reports_dir / f"{safe}_research_questions_{date_str}.md"
    json_path = reports_dir / f"{safe}_research_questions_{date_str}.json"
    export_questions_markdown(questions, md_path,
                              f"Research Questions: {spec_name}")
    export_questions_json(questions, json_path, scope="strategy",
                          spec_name=spec_name)
    return md_path, json_path


def write_global_report(
    questions:   List[ResearchQuestion],
    reports_dir: Path,
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y%m%d")
    md_path   = reports_dir / f"global_research_questions_{date_str}.md"
    json_path = reports_dir / f"global_research_questions_{date_str}.json"
    export_questions_markdown(questions, md_path,
                              "Global Research Questions -- Portfolio Level",
                              scope="global")
    export_questions_json(questions, json_path, scope="global")
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_questions(
    questions: List[ResearchQuestion],
    top_n:     int = 5,
    dry_run:   bool = False,
) -> None:
    tag = "  [DRY-RUN]" if dry_run else ""
    shown = questions[:top_n]
    for i, q in enumerate(shown, 1):
        rr = "  [GATE]" if q.affects_review_required else ""
        print(f"  Q{i}  [{q.priority}][{q.category}]{rr}{tag}")
        print(f"       {q.question}")
        print(f"       Evidence: {q.evidence_behind_it[:100]}")
        print(f"       Action  : {q.suggested_action[:100]}")
        print()
    if len(questions) > top_n:
        print(f"  ... and {len(questions) - top_n} more question(s). See report for full list.")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Research Question Engine (Phase 33). "
            "Given everything in the knowledge graph, "
            "what is the most important unanswered question? "
            "No DB writes. No strategy changes. REVIEW_REQUIRED is terminal."
        )
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec-id", type=int, metavar="ID",
                     help="Generate questions for one strategy")
    grp.add_argument("--all",    action="store_true",
                     help="Generate questions for all scored strategies")
    grp.add_argument("--global", dest="global_only", action="store_true",
                     help="Generate cross-strategy portfolio-level questions only")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Console output only -- no files written")
    parser.add_argument("--top",         type=int, default=5, metavar="N",
                        help="Show top N questions in console (default: 5)")
    parser.add_argument("--db",          default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR), metavar="DIR")
    args = parser.parse_args()

    db_path     = Path(args.db)
    reports_dir = Path(args.reports_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes Research Question Engine  [{mode}]")
    print(f"  DB          : {db_path}")
    if not args.dry_run:
        print(f"  Reports     : {reports_dir}")
    print(f"  Advisory only -- no DB writes, no strategy changes")
    print()

    classifications: Dict = {}
    if CLASSIFICATION_PATH.exists():
        try:
            classifications = json.loads(
                CLASSIFICATION_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    pattern_lib: Dict = {}
    if PATTERN_LIB_PATH.exists():
        try:
            pattern_lib = json.loads(
                PATTERN_LIB_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    conn = sqlite3.connect(str(db_path))
    try:
        if args.spec_id is not None:
            ctx, qs = generate_strategy_questions(
                conn, args.spec_id, classifications, pattern_lib
            )
            if not ctx:
                print(f"ERROR: spec_id={args.spec_id} not found")
                sys.exit(1)
            print(f"Strategy: {ctx.spec_name}  (spec_id={ctx.spec_id})")
            print(f"  Archetype      : {ctx.archetype_label}")
            print(f"  Lifecycle      : {ctx.lifecycle_state}")
            print(f"  Questions      : {len(qs)}  "
                  f"(HIGH={sum(1 for q in qs if q.priority=='HIGH')}  "
                  f"MEDIUM={sum(1 for q in qs if q.priority=='MEDIUM')}  "
                  f"LOW={sum(1 for q in qs if q.priority=='LOW')})")
            print(f"  Gate-level     : {sum(1 for q in qs if q.affects_review_required)}")
            print()
            print(f"  Top {args.top} unanswered questions:")
            print()
            _print_questions(qs, top_n=args.top, dry_run=args.dry_run)
            if not args.dry_run:
                md_path, json_path = write_strategy_report(qs, ctx.spec_name, reports_dir)
                print(f"  Reports")
                print(f"    MD  : {md_path}")
                print(f"    JSON: {json_path}")
                print()

        elif args.all:
            specs = _all_scored_specs(conn)
            if not specs:
                print("No scored strategies found.")
                sys.exit(0)

            all_contexts: List[QuestionContext] = []
            all_strategy_qs: List[ResearchQuestion] = []

            for spec in specs:
                ctx = collect_question_context(
                    conn, spec, classifications, pattern_lib
                )
                qs  = rank_unanswered_questions(identify_unknowns(ctx))
                all_contexts.append(ctx)
                all_strategy_qs.extend(qs)

                print(f"  {ctx.spec_name}  [{ctx.lifecycle_state}]")
                print(f"    Questions: {len(qs)}  "
                      f"HIGH={sum(1 for q in qs if q.priority=='HIGH')}  "
                      f"GATE={sum(1 for q in qs if q.affects_review_required)}")
                if qs:
                    top = qs[0]
                    print(f"    Top: [{top.priority}][{top.category}] {top.question[:90]}")
                print()
                if not args.dry_run:
                    write_strategy_report(qs, ctx.spec_name, reports_dir)

            global_qs = generate_global_questions(all_contexts)
            print(f"Global questions: {len(global_qs)}")
            print()
            _print_questions(global_qs, top_n=args.top, dry_run=args.dry_run)
            if not args.dry_run:
                md_path, json_path = write_global_report(global_qs, reports_dir)
                print(f"  Global reports")
                print(f"    MD  : {md_path}")
                print(f"    JSON: {json_path}")
                print()

        else:  # --global
            specs = _all_scored_specs(conn)
            all_contexts = [
                collect_question_context(conn, s, classifications, pattern_lib)
                for s in specs
            ]
            global_qs = generate_global_questions(all_contexts)
            print(f"Global questions: {len(global_qs)}  "
                  f"(HIGH={sum(1 for q in global_qs if q.priority=='HIGH')})")
            print()
            _print_questions(global_qs, top_n=args.top, dry_run=args.dry_run)
            if not args.dry_run:
                md_path, json_path = write_global_report(global_qs, reports_dir)
                print(f"  Reports")
                print(f"    MD  : {md_path}")
                print(f"    JSON: {json_path}")
                print()

        if args.dry_run:
            print("DRY-RUN complete. No files written. No DB changes.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
