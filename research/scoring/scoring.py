"""Strategy scoring engine.

Public API
----------
score(inp: ScoringInput) -> ScoringResult
save_scoring_result(conn, result) -> int   # returns scoring_id
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .weights import GRADE_BANDS, RECOMMENDATION_MAP, SCORING_WEIGHTS, THRESHOLDS


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))


# ---------------------------------------------------------------------------
# Component scorers — each returns a float in [0.0, 1.0] or None
# ---------------------------------------------------------------------------


def _score_profitability(bt: Dict[str, Any]) -> Optional[float]:
    pf  = bt.get("profit_factor")
    exp = bt.get("expectancy")
    wr  = bt.get("win_rate")
    if pf is None and exp is None and wr is None:
        return None
    parts, count = 0.0, 0
    if pf  is not None:
        parts += _clamp((float(pf) - 0.8) / 1.2)
        count += 1
    if exp is not None:
        parts += 1.0 if float(exp) > 0 else 0.0
        count += 1
    if wr  is not None:
        parts += _clamp(float(wr))
        count += 1
    return parts / count


def _score_drawdown(bt: Dict[str, Any]) -> Optional[float]:
    mdd = bt.get("max_drawdown")
    if mdd is None:
        return None
    mdd = abs(float(mdd))
    limit = THRESHOLDS["max_drawdown_pct"]
    if mdd <= 0.10:
        return 1.0
    if mdd >= limit:
        return 0.0
    return 1.0 - (mdd - 0.10) / (limit - 0.10)


def _score_consistency(bt: Dict[str, Any]) -> Optional[float]:
    pf = bt.get("profit_factor")
    if pf is None:
        return None
    pf = float(pf)
    if pf >= 1.5:
        return 1.0
    if pf >= 1.1:
        return 0.7
    if pf >= 1.0:
        return 0.4
    return 0.0


def _score_walk_forward(wf: Dict[str, Any]) -> Optional[float]:
    overall     = wf.get("overall_pass")
    degradation = wf.get("median_degradation")
    if overall is None and degradation is None:
        return None
    base = 0.9 if overall is True else 0.2 if overall is False else 0.5
    if degradation is not None:
        base += 0.1 if float(degradation) >= 0.8 else -0.2
    return _clamp(base)


def _score_monte_carlo(mc: Dict[str, Any]) -> Optional[float]:
    if not mc:
        return None
    base = 0.5
    if mc.get("pass_status") is True:
        base += 0.3
    elif mc.get("pass_status") is False:
        base -= 0.3
    pct5 = mc.get("pct5_ending_equity")
    if pct5 is not None:
        base += 0.2 if float(pct5) > 0 else -0.3
    prob_loss = mc.get("probability_of_loss")
    if prob_loss is not None:
        base += 0.1 if float(prob_loss) <= 0.4 else -0.2
    return _clamp(base)


def _score_regime(regime: Dict[str, Any]) -> Optional[float]:
    counts = regime.get("regime_counts") or {}
    if not counts:
        return None
    total      = max(1, sum(counts.values()))
    directional = counts.get("Bull", 0) + counts.get("Bear", 0)
    diversity   = (counts.get("Sideways", 0) / total) * 0.3
    return _clamp((directional / total) * 0.7 + diversity)


def _score_robustness(bt: Dict[str, Any]) -> Optional[float]:
    trades = bt.get("trades")
    if trades is None:
        return None
    n = int(trades)
    if n < THRESHOLDS["min_trades"]:
        return 0.0
    return _clamp(0.3 + (n - 30) / 170 * 0.7)


def _score_prop_firm(prop: Dict[str, Any]) -> Optional[float]:
    if not prop:
        return None
    score = 0.5
    mdd = prop.get("max_drawdown")
    if mdd is not None:
        score += 0.2 if abs(float(mdd)) <= 0.20 else -0.3
    breach = prop.get("drawdown_breach_probability")
    if breach is not None:
        score += 0.2 if float(breach) <= 0.20 else -0.3
    dd_limit = prop.get("trailing_drawdown_limit")
    if dd_limit is not None and mdd is not None:
        score += 0.1 if abs(float(mdd)) <= float(dd_limit) else -0.2
    return _clamp(score)


def _score_explainability(bt: Dict[str, Any]) -> float:
    return 0.9 if bt.get("rules_documented") else 0.4


def _overfit_risk(
    bt: Dict[str, Any],
    wf: Dict[str, Any],
    mc: Dict[str, Any],
    regime: Dict[str, Any],
) -> float:
    risk = 0.0
    if (bt.get("trades") or 0) < THRESHOLDS["min_trades"]:
        risk += 0.4
    if wf.get("overall_pass") is False:
        risk += 0.4
    if mc.get("pass_status") is False:
        risk += 0.3
    counts = regime.get("regime_counts") or {}
    total  = max(1, sum(counts.values()))
    if (counts.get("Bull", 0) + counts.get("Bear", 0)) / total > 0.9:
        risk += 0.3
    pf  = bt.get("profit_factor")
    mdd = bt.get("max_drawdown")
    if pf and mdd and float(pf) > 2.0 and abs(float(mdd)) >= 0.18:
        risk += 0.2
    return _clamp(risk)


# ---------------------------------------------------------------------------
# Input / output types
# ---------------------------------------------------------------------------


@dataclass
class ScoringInput:
    spec_id:   int
    backtest:  Dict[str, Any] = field(default_factory=dict)
    regime:    Dict[str, Any] = field(default_factory=dict)
    wf:        Dict[str, Any] = field(default_factory=dict)
    mc:        Dict[str, Any] = field(default_factory=dict)
    prop_firm: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoringResult:
    spec_id:             int
    composite_score:     float
    grade:               str
    recommendation:      str
    component_scores:    Dict[str, Optional[float]]
    gate_failures:       List[str]
    overfit_warnings:    List[str]
    overfitting_risk:    float
    monte_carlo_pass:    bool
    walk_forward_pass:   bool
    prop_firm_supported: bool
    prop_firm_support:   Dict[str, Any]


# ---------------------------------------------------------------------------
# Gate checks + warning strings
# ---------------------------------------------------------------------------


def _hard_gates(inp: ScoringInput) -> List[str]:
    failures: List[str] = []
    bt = inp.backtest
    pf = bt.get("profit_factor")
    if pf is not None and float(pf) < THRESHOLDS["min_profit_factor"]:
        failures.append(
            f"profit_factor {float(pf):.2f} < {THRESHOLDS['min_profit_factor']}"
        )
    mdd = bt.get("max_drawdown")
    if mdd is not None and abs(float(mdd)) > THRESHOLDS["max_drawdown_pct"]:
        failures.append(
            f"max_drawdown {abs(float(mdd)):.1%} > {THRESHOLDS['max_drawdown_pct']:.1%}"
        )
    trades = bt.get("trades")
    if trades is not None and int(trades) < int(THRESHOLDS["min_trades"]):
        failures.append(
            f"trade_count {int(trades)} < {int(THRESHOLDS['min_trades'])}"
        )
    survival = inp.mc.get("survival_rate")
    if survival is not None and float(survival) < THRESHOLDS["min_mc_survival"]:
        failures.append(
            f"mc_survival {float(survival):.1%} < {THRESHOLDS['min_mc_survival']:.1%}"
        )
    return failures


def _warnings(inp: ScoringInput) -> List[str]:
    bt, wf, mc, regime = inp.backtest, inp.wf, inp.mc, inp.regime
    msgs: List[str] = []
    if (bt.get("trades") or 0) < THRESHOLDS["min_trades"]:
        msgs.append("Profit comes from too few trades.")
    if wf.get("overall_pass") is False:
        msgs.append("Walk-forward degradation is high.")
    if mc.get("pass_status") is False:
        msgs.append("Monte Carlo failure probability is high.")
    counts = regime.get("regime_counts") or {}
    total  = max(1, sum(counts.values()))
    if (counts.get("Bull", 0) + counts.get("Bear", 0)) / total > 0.85:
        msgs.append("Strategy only works in one narrow regime.")
    mdd = bt.get("max_drawdown")
    if mdd is not None and abs(float(mdd)) > 0.20:
        msgs.append("Drawdown is too close to prop-firm limit.")
    return msgs


def _prop_firm_review(prop: Dict[str, Any]) -> Dict[str, Any]:
    account_size = float(prop.get("account_size", 50_000.0))
    dd_limit     = float(prop.get("trailing_drawdown_limit", 0.20))
    mdd          = prop.get("max_drawdown")
    return {
        "account_size":              account_size,
        "trailing_drawdown_limit":   dd_limit,
        "daily_loss_limit":          prop.get("daily_loss_limit"),
        "max_drawdown":              mdd,
        "drawdown_breach_probability": prop.get("drawdown_breach_probability"),
        "supported": mdd is not None and abs(float(mdd)) <= dd_limit,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score(inp: ScoringInput) -> ScoringResult:
    """Compute a composite score for a strategy.

    Returns a ScoringResult with grade, recommendation, component scores,
    hard-gate failures, and overfit warnings.  No DB writes.
    """
    bt, regime, wf, mc, pf_data = (
        inp.backtest, inp.regime, inp.wf, inp.mc, inp.prop_firm
    )

    components: Dict[str, Optional[float]] = {
        "profitability":  _score_profitability(bt),
        "drawdown":       _score_drawdown(bt),
        "consistency":    _score_consistency(bt),
        "walk_forward":   _score_walk_forward(wf),
        "monte_carlo":    _score_monte_carlo(mc),
        "regime":         _score_regime(regime),
        "robustness":     _score_robustness(bt),
        "prop_firm":      _score_prop_firm(pf_data),
        "explainability": _score_explainability(bt),
    }

    # Weighted composite — skip None components and renormalise weight sum
    avail_weight = sum(
        SCORING_WEIGHTS[k] for k, v in components.items()
        if v is not None and k in SCORING_WEIGHTS
    )
    weighted = sum(
        v * SCORING_WEIGHTS[k] for k, v in components.items()
        if v is not None and k in SCORING_WEIGHTS
    )
    raw_score = (weighted / avail_weight * 100.0) if avail_weight > 0 else 0.0

    # Penalise overfit risk (max 10-point deduction)
    risk      = _overfit_risk(bt, wf, mc, regime)
    composite = round(_clamp(raw_score - risk * 10.0, 0.0, 100.0), 2)

    # Hard gates override grade
    gates = _hard_gates(inp)
    if gates:
        grade          = "Reject"
        recommendation = "Reject"
    else:
        grade = next(
            (g for g, threshold in GRADE_BANDS if composite >= threshold),
            "D",
        )
        recommendation = RECOMMENDATION_MAP.get(grade, "Reject")

    components["overfitting_risk"] = risk
    review = _prop_firm_review(pf_data)

    return ScoringResult(
        spec_id=inp.spec_id,
        composite_score=composite,
        grade=grade,
        recommendation=recommendation,
        component_scores=components,
        gate_failures=gates,
        overfit_warnings=_warnings(inp),
        overfitting_risk=risk,
        monte_carlo_pass=bool(mc.get("pass_status")),
        walk_forward_pass=bool(wf.get("overall_pass")),
        prop_firm_supported=bool(review.get("supported")),
        prop_firm_support=review,
    )


def save_scoring_result(conn: sqlite3.Connection, result: ScoringResult) -> int:
    """Persist a ScoringResult to the scoring_results table.

    Returns the new scoring_id (INTEGER PRIMARY KEY).
    """
    cs  = result.component_scores
    cur = conn.execute(
        """
        INSERT INTO scoring_results (
            spec_id, composite_score, grade, recommendation,
            profitability_score, drawdown_score, consistency_score,
            walk_forward_score, monte_carlo_score, regime_score,
            robustness_score, prop_firm_score, explainability_score,
            overfitting_risk, monte_carlo_pass, walk_forward_pass,
            prop_firm_supported, prop_firm_support_json, overfit_warnings_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.spec_id,
            result.composite_score,
            result.grade,
            result.recommendation,
            cs.get("profitability"),
            cs.get("drawdown"),
            cs.get("consistency"),
            cs.get("walk_forward"),
            cs.get("monte_carlo"),
            cs.get("regime"),
            cs.get("robustness"),
            cs.get("prop_firm"),
            cs.get("explainability"),
            result.overfitting_risk,
            1 if result.monte_carlo_pass   else 0,
            1 if result.walk_forward_pass  else 0,
            1 if result.prop_firm_supported else 0,
            json.dumps(result.prop_firm_support),
            json.dumps(result.overfit_warnings),
        ),
    )
    conn.commit()
    return cur.lastrowid
