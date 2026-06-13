#!/usr/bin/env python3
"""Generate synthetic trade results and run Monte Carlo demo."""

from __future__ import annotations

from pathlib import Path

from monte_carlo_engine import render_report, run_monte_carlo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = PROJECT_ROOT / "reports" / "monte_carlo" / "monte_carlo_demo_report.md"
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    trade_returns = [0.02, -0.01, 0.015, -0.005, 0.03, -0.02, 0.01, -0.015, 0.025, -0.01] * 20
    report = run_monte_carlo(
        trade_returns,
        simulations=1000,
        start_equity=50_000.0,
        max_dd_limit=0.20,
        daily_loss_limit=-0.02,
    )
    text = render_report(report, start_equity=50_000.0)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(REPORT_PATH)
    print(text)


if __name__ == "__main__":
    main()
