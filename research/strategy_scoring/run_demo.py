#!/usr/bin/env python3
"""Demo scoring engine with synthetic combined results."""

from __future__ import annotations

from pathlib import Path

from strategy_scoring import render_report, score_strategy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "reports" / "scoring" / "strategy_scoring_demo_report.md"
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    backtest = {
        "profit_factor": 1.45,
        "expectancy": 0.12,
        "win_rate": 0.58,
        "max_drawdown": 0.18,
        "trades": 45,
        "rules_documented": True,
    }
    regime = {
        "regime_counts": {"Bull": 40, "Bear": 30, "Sideways": 20, "Transition": 10},
    }
    wf = {"overall_pass": True, "median_degradation": 0.82}
    mc = {"pass_status": True, "pct5_ending_equity": 0.98, "probability_of_loss": 0.35}
    prop_firm = {
        "account_size": 50_000.0,
        "trailing_drawdown_limit": 0.25,
        "daily_loss_limit": -0.02,
        "max_drawdown": 0.18,
        "drawdown_breach_probability": 0.12,
        "max_losing_streak": 6,
    }
    score = score_strategy(backtest=backtest, regime=regime, wf=wf, mc=mc, prop_firm=prop_firm)
    text = render_report(score, meta={"strategy_id": "DEMO"})
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(REPORT_PATH)
    print(text)


if __name__ == "__main__":
    main()
