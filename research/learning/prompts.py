"""
prompts.py -- text templates for the AI Learning Brain.

Each function returns a human-readable string (pattern description)
or a structured dict (next-action suggestion).

Nothing here writes to any file, database, or external system.
"""

from __future__ import annotations
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Failure pattern descriptions
# ---------------------------------------------------------------------------

def p_no_trade_json() -> str:
    return (
        "No trade_list_json: individual trade data is missing. "
        "Detailed pattern analysis is not possible without real NT8 export data."
    )

def p_insufficient_trades(trades: int, minimum: int) -> str:
    return (
        f"Insufficient trades: {trades} recorded, minimum {minimum} required. "
        "All statistical metrics are unreliable below this threshold."
    )

def p_low_trade_count(trades: int, recommended: int) -> str:
    return (
        f"Low trade count: {trades} trades. "
        f"{recommended}+ recommended for statistically reliable performance metrics."
    )

def p_pf_overfit_risk(pf: float, trades: int) -> str:
    return (
        f"Overfit signal: PF={pf:.2f} with only {trades} trades. "
        "Elevated profit factors on small samples are frequently a product of chance, "
        "not edge. Validate with 100+ trades before trusting this metric."
    )

def p_sharpe_overfit_risk(sharpe: float, trades: int) -> str:
    return (
        f"Overfit signal: Sharpe={sharpe:.2f} with only {trades} trades. "
        "High Sharpe ratios on small samples are unreliable."
    )

def p_missing_oos() -> str:
    return (
        "No out-of-sample backtest: in-sample results cannot be trusted for "
        "live performance without OOS validation. Walk-forward test required."
    )

def p_missing_mc() -> str:
    return (
        "Monte Carlo not run: sequence risk is unknown. "
        "Bootstrap resampling is required to assess robustness to trade order variation."
    )

def p_missing_regime() -> str:
    return (
        "Regime analysis not run: unknown whether strategy performance "
        "holds across different market conditions."
    )

def p_wf_fail(score: float, threshold: float) -> str:
    return (
        f"Walk-forward FAIL: score={score:.4f} (threshold {threshold}). "
        "OOS performance is severely degraded relative to IS. "
        "Strategy likely relies on patterns that do not generalise beyond the backtest window."
    )

def p_wf_warn(score: float, pass_gate: float) -> str:
    return (
        f"Walk-forward WARNING: score={score:.4f} (PASS threshold {pass_gate}). "
        "Moderate OOS degradation detected. May indicate mild overfitting or "
        "regime shift between IS and OOS periods."
    )

def p_wf_pf_retention(pf_retention: float, is_pf: float, oos_pf: float) -> str:
    drop = 1.0 - pf_retention
    return (
        f"PF retention low: dropped {drop:.1%} from IS ({is_pf:.2f}) "
        f"to OOS ({oos_pf:.2f}). "
        "Profit factor degrades significantly outside the training window."
    )

def p_wf_dd_worse(dd_component: float, is_dd: float, oos_dd: float) -> str:
    return (
        f"OOS drawdown worse: IS {is_dd:.1%} vs OOS {oos_dd:.1%} "
        f"(DD component={dd_component:.2f}). "
        "Risk profile deteriorates out-of-sample."
    )

def p_mc_fail(score: float) -> str:
    return (
        f"Monte Carlo FAIL: survival={score:.1%}. "
        "Strategy results are heavily dependent on the specific sequence of trades. "
        "A different ordering of the same trades leads to ruin in the majority of simulations."
    )

def p_mc_warn(score: float, pass_gate: float) -> str:
    return (
        f"Monte Carlo WARNING: survival={score:.1%} (PASS threshold {pass_gate:.0%}). "
        "Marginal robustness to trade sequence variation."
    )

def p_mc_prob_positive_low(prob: float) -> str:
    return (
        f"Low probability positive: {prob:.1%} of bootstrap simulations end "
        "with equity above initial capital. Strategy has a negative-expectancy tail risk."
    )

def p_drawdown_exceeds_limit(dd: float, limit: float) -> str:
    return (
        f"Drawdown {dd:.1%} exceeds prop-firm limit {limit:.0%}. "
        "Strategy does not meet standard prop-firm drawdown constraints."
    )

def p_drawdown_severe(dd: float) -> str:
    return (
        f"Severe drawdown: {dd:.1%}. "
        "Capital at significant risk; review position sizing and stop-loss logic."
    )

def p_single_regime_window(label: str) -> str:
    return (
        f"Single regime window: all trades concentrated in '{label}'. "
        "Insufficient regime coverage to assess cross-condition robustness."
    )

def p_audit_fails(count: int) -> str:
    return (
        f"{count} FAIL finding(s) in audit: "
        "critical research gates have not been cleared. "
        "Review audit report and resolve all failures before advancing."
    )


# ---------------------------------------------------------------------------
# Strength pattern descriptions
# ---------------------------------------------------------------------------

def s_mc_pass(score: float, sims: int) -> str:
    return f"Monte Carlo PASS: {score:.1%} survival across {sims:,} bootstrap simulations."

def s_mc_prob_positive(prob: float) -> str:
    return f"High probability positive: {prob:.1%} of simulations end above initial capital."

def s_wf_pass(score: float) -> str:
    return f"Walk-forward PASS: score={score:.4f}. OOS performance validates IS results."

def s_wf_reasonable(score: float) -> str:
    return (
        f"Walk-forward WARNING: score={score:.4f}. "
        "OOS degradation is present but manageable. Strategy shows cross-period generality."
    )

def s_high_score(score: float, grade: str) -> str:
    return f"Composite score {score:.1f} (Grade {grade}): strong overall performance profile."

def s_strong_pf(pf: float, trades: int) -> str:
    return (
        f"Solid profit factor {pf:.2f} with {trades} trades: "
        "risk/reward ratio supported by adequate sample."
    )

def s_good_win_rate(wr: float, trades: int) -> str:
    return f"Win rate {wr:.1%} across {trades} trades: consistent directional accuracy."

def s_low_overfit() -> str:
    return "Overfitting risk 0.00: scoring engine detected no parameter overfit."

def s_low_drawdown(dd: float) -> str:
    return f"Low max drawdown {dd:.1%}: strong capital preservation."

def s_regime_consistent(label: str, regime: str) -> str:
    return f"Regime analysis shows '{label}' window classified {regime}: strategy performs in current conditions."

def s_clean_audit() -> str:
    return "Audit passed all checks: no FAIL findings."


# ---------------------------------------------------------------------------
# Next-action suggestion builders
# ---------------------------------------------------------------------------

def a_import_nt8(spec_id: int) -> Dict:
    return {
        "priority":    1,
        "action_type": "collect_more_trades",
        "description": "Import real NT8 backtest export with trade_list_json populated.",
        "command":     f"python -m connectors.ninjatrader.nt8_import_pipeline --spec-id {spec_id} --initial-capital <amount>",
    }

def a_collect_minimum_trades(spec_id: int, current: int, minimum: int) -> Dict:
    return {
        "priority":    1,
        "action_type": "collect_more_trades",
        "description": f"Extend backtest to reach minimum {minimum} trades (currently {current}). Re-export from NT8.",
        "command":     None,
    }

def a_collect_more_trades(spec_id: int, current: int, target: int) -> Dict:
    return {
        "priority":    2,
        "action_type": "collect_more_trades",
        "description": f"Extend backtest to {target}+ trades (currently {current}). Run on 12+ months to build reliable sample.",
        "command":     None,
    }

def a_run_oos(spec_id: int) -> Dict:
    return {
        "priority":    2,
        "action_type": "run_oos_test",
        "description": "Import an out-of-sample backtest (separate date range) and run walk-forward validation.",
        "command":     (
            f"python -m connectors.ninjatrader.nt8_import_pipeline --oos ... "
            f"&& python -m research.validation.walk_forward --spec-id {spec_id}"
        ),
    }

def a_run_walk_forward(spec_id: int) -> Dict:
    return {
        "priority":    2,
        "action_type": "run_oos_test",
        "description": "OOS backtest exists. Run walk-forward validation engine.",
        "command":     f"python -m research.validation.walk_forward --spec-id {spec_id}",
    }

def a_run_monte_carlo(spec_id: int) -> Dict:
    return {
        "priority":    3,
        "action_type": "run_monte_carlo",
        "description": "Run Monte Carlo bootstrap validation to assess sequence robustness.",
        "command":     f"python -m research.validation.monte_carlo --spec-id {spec_id}",
    }

def a_run_regime(spec_id: int) -> Dict:
    return {
        "priority":    3,
        "action_type": "test_regime_variation",
        "description": "Run regime analysis to assess performance across market conditions.",
        "command":     f"python -m research.regime.regime_analyzer --spec-id {spec_id}",
    }

def a_review_oos_degradation(spec_id: int, wf_score: float) -> Dict:
    return {
        "priority":    2,
        "action_type": "run_oos_test",
        "description": (
            f"Walk-forward score {wf_score:.4f} indicates notable OOS degradation. "
            "Extend the OOS period and re-validate. Review IS/OOS date boundary for data leakage."
        ),
        "command":     f"python -m research.validation.walk_forward --spec-id {spec_id}",
    }

def a_reject_wf_fail(spec_id: int, score: float) -> Dict:
    return {
        "priority":    1,
        "action_type": "reject_candidate",
        "description": (
            f"Walk-forward FAIL (score={score:.4f}): OOS performance severely degraded. "
            "Recommend rejection. Archive in research/rejected/ with documented reason."
        ),
        "command":     None,
    }

def a_reject_mc_fail(spec_id: int, score: float) -> Dict:
    return {
        "priority":    1,
        "action_type": "reject_candidate",
        "description": (
            f"Monte Carlo FAIL (survival={score:.1%}): strategy is sequence-dependent. "
            "Results not reproducible. Recommend rejection."
        ),
        "command":     None,
    }

def a_parameter_sweep(spec_id: int, pf: float, trades: int) -> Dict:
    return {
        "priority":    4,
        "action_type": "run_wider_parameter_sweep",
        "description": (
            f"PF={pf:.2f} with {trades} trades may reflect overfit. "
            "Run a wider parameter sweep (research only) to assess metric stability "
            "across parameter variations. Do not modify the live spec."
        ),
        "command":     None,
    }

def a_add_drawdown_filter(spec_id: int, dd: float, limit: float) -> Dict:
    return {
        "priority":    4,
        "action_type": "add_filter_for_research",
        "description": (
            f"Max drawdown {dd:.1%} exceeds prop-firm limit {limit:.0%}. "
            "Research only: explore adding a trailing stop or daily loss limit "
            "to bring drawdown within compliance. Backtest and validate before applying."
        ),
        "command":     None,
    }

def a_test_regime_filter(spec_id: int, best_window: str) -> Dict:
    return {
        "priority":    5,
        "action_type": "test_regime_specific_variation",
        "description": (
            f"Regime analysis identified '{best_window}' as best window. "
            "Research only: test regime-filtered variation that only trades in Strong windows. "
            "Backtest and re-validate. Do not modify the live spec."
        ),
        "command":     f"python -m research.regime.regime_analyzer --spec-id {spec_id} --label-file research/regime/sample_regime_labels.csv",
    }

def a_extend_oos_period(spec_id: int) -> Dict:
    return {
        "priority":    4,
        "action_type": "run_oos_test",
        "description": (
            "Walk-forward WARNING: extend OOS period to cover more market conditions. "
            "Re-import OOS with a longer date range and re-run walk-forward."
        ),
        "command":     f"python -m research.validation.walk_forward --spec-id {spec_id}",
    }

def a_prepare_human_review(spec_id: int) -> Dict:
    return {
        "priority":    1,
        "action_type": "prepare_for_human_review",
        "description": (
            "All automated validation gates cleared. "
            "Generate final decision package and submit to human reviewer for approval or rejection."
        ),
        "command":     f"python -m research.decision.decision_package --spec-id {spec_id}",
    }
