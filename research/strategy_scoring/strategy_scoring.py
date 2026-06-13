"""Reusable strategy scoring engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class StrategyScore:
    composite_score: float
    grade: str
    recommendation: str
    category_scores: Dict[str, Any]
    prop_firm_support: Dict[str, Any]
    overfit_warnings: List[str]
    notes: str = ""


# ------------------------------------------------------------------
# Category scorers
# ------------------------------------------------------------------


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_profitability(backtest: Dict[str, Any]) -> float | None:
    pf = backtest.get("profit_factor")
    expectancy = backtest.get("expectancy")
    win_rate = backtest.get("win_rate")
    if pf is None and expectancy is None and win_rate is None:
        return None
    part = 0.0
    count = 0
    if pf is not None:
        part += _clamp01((float(pf) - 0.8) / 1.2)
        count += 1
    if expectancy is not None:
        part += 1.0 if float(expectancy) > 0 else 0.0
        count += 1
    if win_rate is not None:
        part += _clamp01(float(win_rate))
        count += 1
    return part / count if count else None


def score_drawdown(backtest: Dict[str, Any]) -> float | None:
    mdd = backtest.get("max_drawdown")
    if mdd is None:
        return None
    mdd = abs(float(mdd))
    if mdd <= 0.10:
        return 1.0
    if mdd >= 0.30:
        return 0.0
    return 1.0 - (mdd - 0.10) / 0.20


def score_consistency(backtest: Dict[str, Any]) -> float | None:
    pf = backtest.get("profit_factor")
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


def score_walk_forward(wf: Dict[str, Any]) -> float | None:
    overall = wf.get("overall_pass")
    median_degradation = wf.get("median_degradation")
    if overall is None and median_degradation is None:
        return None
    base = 0.5
    if overall is True:
        base = 0.9
    elif overall is False:
        base = 0.2
    if median_degradation is not None:
        base += 0.1 if float(median_degradation) >= 0.8 else -0.2
    return _clamp01(base)


def score_monte_carlo(mc: Dict[str, Any]) -> float | None:
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
    return _clamp01(base)


def score_regime(regime: Dict[str, Any]) -> float | None:
    counts = regime.get("regime_counts") or {}
    if not counts:
        return None
    bull = counts.get("Bull", 0)
    bear = counts.get("Bear", 0)
    side = counts.get("Sideways", 0)
    total = max(1, sum(counts.values()))
    directional = bull + bear
    if directional == 0:
        return 0.2
    diversity = (side / total) * 0.3
    return _clamp01((directional / total) * 0.7 + diversity)


def score_robustness(backtest: Dict[str, Any]) -> float | None:
    trades = backtest.get("trades")
    if not trades:
        return None
    return _clamp01(0.7)


def score_prop_firm(prop: Dict[str, Any]) -> float | None:
    if not prop:
        return None
    score = 0.5
    max_dd = prop.get("max_drawdown")
    if max_dd is not None:
        score += 0.2 if abs(float(max_dd)) <= 0.20 else -0.3
    breach = prop.get("drawdown_breach_probability")
    if breach is not None:
        score += 0.2 if float(breach) <= 0.20 else -0.3
    dd_limit = prop.get("max_drawdown_limit")
    if dd_limit is not None and max_dd is not None:
        score += 0.1 if float(max_dd) <= float(dd_limit) else -0.2
    return _clamp01(score)


def score_overfitting_risk(
    backtest: Dict[str, Any],
    wf: Dict[str, Any],
    mc: Dict[str, Any],
    regime: Dict[str, Any],
) -> float:
    risk = 0.0
    if backtest.get("trades", 0) < 30:
        risk += 0.4
    if wf.get("overall_pass") is False:
        risk += 0.4
    if mc.get("pass_status") is False:
        risk += 0.3
    counts = regime.get("regime_counts") or {}
    bull = counts.get("Bull", 0)
    bear = counts.get("Bear", 0)
    total = max(1, sum(counts.values()))
    if (bull + bear) / total > 0.9:
        risk += 0.3
    pf = backtest.get("profit_factor")
    mdd = backtest.get("max_drawdown")
    if pf is not None and mdd is not None and float(pf) > 2.0 and abs(float(mdd)) >= 0.18:
        risk += 0.2
    return _clamp01(risk)


def score_explainability(backtest: Dict[str, Any]) -> float:
    return 0.9 if backtest.get("rules_documented") else 0.4


# ------------------------------------------------------------------
# Composite scoring
# ------------------------------------------------------------------


def grade_from_score(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 50:
        return "D"
    return "Reject"


def recommendation_from_score(score: float, passed: bool) -> str:
    if score < 50:
        return "Reject"
    if score < 65:
        return "Retest"
    if score < 80:
        return "Optimize"
    if score < 90:
        return "Forward Test"
    return "Live Candidate"


def scale01_weighted(components: Dict[str, float | None], weights: Dict[str, float]) -> float:
    weighted = 0.0
    total_weight = 0.0
    for name, value in components.items():
        if value is None:
            continue
        weight = weights.get(name, 1.0)
        weighted += float(value) * weight
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return weighted / total_weight


def prop_firm_review(prop: Dict[str, Any]) -> Dict[str, Any]:
    account_size = float(prop.get("account_size", 50_000.0))
    trailing_dd_limit = float(prop.get("trailing_drawdown_limit", 0.20))
    max_dd = prop.get("max_drawdown")
    return {
        "account_size": account_size,
        "trailing_drawdown_limit": trailing_dd_limit,
        "daily_loss_limit": prop.get("daily_loss_limit"),
        "max_losing_streak": prop.get("max_losing_streak"),
        "max_drawdown": max_dd,
        "drawdown_breach_probability": prop.get("drawdown_breach_probability"),
        "supported": max_dd is not None and abs(float(max_dd)) <= trailing_dd_limit,
    }


def overfit_warnings(
    backtest: Dict[str, Any],
    wf: Dict[str, Any],
    mc: Dict[str, Any],
    regime: Dict[str, Any],
) -> List[str]:
    warnings: List[str] = []
    if (backtest.get("trades") or 0) < 30:
        warnings.append("Profit comes from too few trades.")
    if wf.get("overall_pass") is False:
        warnings.append("Walk-forward degradation is high.")
    if mc.get("pass_status") is False:
        warnings.append("Monte Carlo failure probability is high.")
    counts = regime.get("regime_counts") or {}
    bull = counts.get("Bull", 0)
    bear = counts.get("Bear", 0)
    total = max(1, sum(counts.values()))
    if (bull + bear) / total > 0.85:
        warnings.append("Strategy only works in one narrow regime.")
    mdd = backtest.get("max_drawdown")
    if mdd is not None and abs(float(mdd)) > 0.20:
        warnings.append("Drawdown is too close to prop-firm limit.")
    return warnings


# ------------------------------------------------------------------
# Main entrypoint
# ------------------------------------------------------------------


def score_strategy(
    backtest: Dict[str, Any] | None = None,
    regime: Dict[str, Any] | None = None,
    wf: Dict[str, Any] | None = None,
    mc: Dict[str, Any] | None = None,
    prop_firm: Dict[str, Any] | None = None,
) -> StrategyScore:
    backtest = backtest or {}
    regime = regime or {}
    wf = wf or {}
    mc = mc or {}
    prop_firm = prop_firm or {}

    components: Dict[str, float | None] = {
        "profitability": score_profitability(backtest),
        "drawdown": score_drawdown(backtest),
        "consistency": score_consistency(backtest),
        "walk_forward": score_walk_forward(wf),
        "monte_carlo": score_monte_carlo(mc),
        "regime": score_regime(regime),
        "robustness": score_robustness(backtest),
        "prop_firm": score_prop_firm(prop_firm),
        "explainability": score_explainability(backtest),
    }

    weights: Dict[str, float] = {
        "profitability": 1.5,
        "drawdown": 1.5,
        "consistency": 1.0,
        "walk_forward": 2.0,
        "monte_carlo": 2.0,
        "regime": 1.0,
        "robustness": 1.0,
        "prop_firm": 1.0,
        "explainability": 0.8,
        "overfitting_risk": -0.5,
    }

    composite0 = scale01_weighted(components, {k: v for k, v in weights.items() if k != "overfitting_risk"})
    risk = score_overfitting_risk(backtest, wf, mc, regime)
    components["overfitting_risk"] = risk
    composite = _clamp01(composite0 - weights["overfitting_risk"] * risk) * 100.0
    composite = round(composite, 2)

    grade = grade_from_score(composite)
    passed = bool(wf.get("overall_pass") and mc.get("pass_status"))
    recommendation = recommendation_from_score(composite, passed)
    review = prop_firm_review(prop_firm)
    warnings = overfit_warnings(backtest, wf, mc, regime)

    return StrategyScore(
        composite_score=composite,
        grade=grade,
        recommendation=recommendation,
        category_scores=components,
        prop_firm_support=review,
        overfit_warnings=warnings,
    )


# ------------------------------------------------------------------
# Reporting
# ------------------------------------------------------------------


def render_report(score: StrategyScore, meta: Dict[str, Any] | None = None) -> str:
    meta = meta or {}
    lines = [
        "# Strategy Scoring Report",
        "",
        "## Composite",
        f"- Score: {score.composite_score:.2f}",
        f"- Grade: {score.grade}",
        f"- Recommendation: {score.recommendation}",
        "",
        "## Category Scores",
    ]
    for name, value in score.category_scores.items():
        if value is None:
            lines.append(f"- {name}: n/a")
        else:
            lines.append(f"- {name}: {value:.4f}")
    lines += [
        "",
        "## Prop-Firm Suitability",
        f"- Account size: {score.prop_firm_support.get('account_size')}",
        f"- Trailing drawdown limit: {score.prop_firm_support.get('trailing_drawdown_limit')}",
        f"- Supported: {'Yes' if score.prop_firm_support.get('supported') else 'No'}",
        f"- Drawdown breach probability: {score.prop_firm_support.get('drawdown_breach_probability')}",
        f"- Max losing streak: {score.prop_firm_support.get('max_losing_streak')}",
        "",
        "## Overfitting Warnings",
    ]
    if score.overfit_warnings:
        for w in score.overfit_warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- None")
    lines += [
        "",
        "## Notes",
        "- Demo only. Not based on live backtests.",
        "- Next step: attach real backtest / regime / wf / mc results.",
    ]
    return "\n".join(lines)
