"""End-to-end pipeline integration demo.

Wires regime detection → walk-forward → Monte Carlo → strategy scoring
into a single save_report() call that returns the composite score and a
combined markdown report, and writes the report to reports/pipeline_demo/.
"""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = PROJECT_ROOT / "research"

if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

import numpy as np  # noqa: E402

from regime_engine.regime_engine import (  # noqa: E402
    classify_regimes,
    regime_performance,
    render_regime_report,
)
from walk_forward_engine.walk_forward_engine import (  # noqa: E402
    WindowMode,
    run_walk_forward,
    render_report as render_wf_report,
)
from monte_carlo_engine.monte_carlo_engine import (  # noqa: E402
    run_monte_carlo,
    render_report as render_mc_report,
)
from strategy_scoring.strategy_scoring import (  # noqa: E402
    StrategyScore,
    score_strategy,
    render_report as render_score_report,
)

_REPORT_DIR = PROJECT_ROOT / "reports" / "pipeline_demo"
_REPORT_PATH = _REPORT_DIR / "pipeline_demo_report.md"

_SEED = 42
_START_EQUITY = 100_000.0


def _make_price_series(n: int = 504) -> np.ndarray:
    rng = np.random.default_rng(_SEED)
    daily_returns = rng.normal(0.0003, 0.012, n)
    return 100.0 * np.cumprod(1.0 + daily_returns)


def _make_wf_scores(n: int = 100) -> list[float]:
    rng = __import__("random").Random(_SEED)
    return [round(rng.uniform(0.8, 2.2), 4) for _ in range(n)]


def _make_trade_returns(n: int = 150) -> list[float]:
    rng = __import__("random").Random(_SEED)
    out = []
    for _ in range(n):
        if rng.random() < 0.55:
            out.append(round(rng.uniform(0.005, 0.025), 5))
        else:
            out.append(round(-rng.uniform(0.003, 0.015), 5))
    return out


def save_report() -> dict:
    """Run the full pipeline on synthetic demo data.

    Returns
    -------
    dict with keys:
        "score"  – StrategyScore dataclass
        "report" – combined markdown string (also written to disk)
    """
    # 1. Regime
    prices = _make_price_series()
    regime_series = classify_regimes(prices)
    regime_report = regime_performance(regime_series)
    regime_counts = {s.regime: s.count for s in regime_report.stats}

    # 2. Walk-forward
    wf_scores = _make_wf_scores()
    wf_report = run_walk_forward(wf_scores, train_size=60, test_size=20, mode=WindowMode.ROLLING)
    finite_degradations = [
        r.degradation_ratio for r in wf_report.results
        if r.in_sample_score > 0 and r.degradation_ratio != float("inf")
    ]
    median_degradation = (
        sorted(finite_degradations)[len(finite_degradations) // 2]
        if finite_degradations else 0.0
    )

    # 3. Monte Carlo
    trade_returns = _make_trade_returns()
    mc_report = run_monte_carlo(
        trade_returns, simulations=1000, start_equity=_START_EQUITY, seed=_SEED
    )

    # 4. Score strategy
    backtest = {
        "profit_factor": 1.45,
        "expectancy": 0.012,
        "win_rate": 0.55,
        "max_drawdown": -0.12,
        "trades": len(trade_returns),
        "rules_documented": True,
    }
    wf_input = {
        "overall_pass": wf_report.overall_pass,
        "median_degradation": median_degradation,
    }
    mc_input = {
        "pass_status": mc_report.pass_status,
        "pct5_ending_equity": mc_report.pct_5_ending_equity,
        "probability_of_loss": mc_report.probability_of_loss,
    }
    regime_input = {"regime_counts": regime_counts}
    prop_firm_input = {
        "account_size": _START_EQUITY,
        "trailing_drawdown_limit": 0.20,
        "max_drawdown": max(mc_report.max_drawdowns),
        "drawdown_breach_probability": mc_report.drawdown_breach_probability,
        "max_losing_streak": max(mc_report.longest_losing_streaks),
        "max_drawdown_limit": 0.20,
    }

    score = score_strategy(
        backtest=backtest,
        regime=regime_input,
        wf=wf_input,
        mc=mc_input,
        prop_firm=prop_firm_input,
    )

    # 5. Render combined report
    sections = [
        "# Pipeline Demo Report\n\nEnd-to-end research pipeline integration using synthetic data.",
        render_regime_report(regime_report),
        render_wf_report(wf_report),
        render_mc_report(mc_report, start_equity=_START_EQUITY),
        render_score_report(score, meta={"strategy": "Pipeline Demo Strategy"}),
    ]
    combined = "\n\n---\n\n".join(sections)

    # 6. Write to disk
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text(combined, encoding="utf-8")

    return {"score": score, "report": combined}
