#!/usr/bin/env python3
"""Verify regime engine with synthetic price paths and save a sample report."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from regime_engine import (
    GaussianHMM,
    RegimeSeries,
    classify_regimes,
    compute_20d_return,
    fit_markov,
    regime_performance,
    render_regime_report,
)

ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = ROOT / "reports" / "regime_engine_demo_report.md"
REPORT_PATH.parent.mkdir(exist_ok=True)


def make_bullish_series(n: int = 252, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    returns = 0.0005 + rng.normal(scale=0.01, size=n)
    prices = 100.0 * np.cumprod(1.0 + returns)
    return prices


def make_bearish_series(n: int = 252, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    returns = -0.0006 + rng.normal(scale=0.012, size=n)
    prices = 100.0 * np.cumprod(1.0 + returns)
    return prices


def main() -> None:
    bull_prices = make_bullish_series()
    bear_prices = make_bearish_series()
    combined = np.concatenate([bull_prices, bear_prices])

    series = classify_regimes(combined)
    probs, labels = fit_markov(series.regimes)

    hmm = GaussianHMM(n_states=4)
    hmm.fit(compute_20d_return(combined))
    hmm_states = hmm.predict(compute_20d_return(combined))

    report = regime_performance(series)
    report_md = render_regime_report(report)

    lines = [
        "# Market Regime Engine — Demo Report",
        "",
        "## Input",
        "- Series: synthetic bull+bear (n=504)",
        "",
        "## Rule-Based Regime Performance",
        report_md,
        "",
        "## Markov Transition Matrix",
        "| From / To | " + " | ".join(labels) + " |",
        "|" + "|".join(["---"] * (len(labels) + 1)) + "|",
    ]

    labels_list = [labels[i] if isinstance(labels, list) else labels[str(i)] for i in range(4)]  # fallback formatting
    actual_labels = labels if isinstance(labels, list) else [labels[str(i)] for i in range(probs.shape[0])]
    for i, label in enumerate(["Bull", "Bear", "Sideways", "Transition"]):
        row = " | ".join([f"{v:.2f}" for v in probs[i]])
        lines.append(f"| {label} | {row} |")

    lines += [
        "",
        "## Hidden Markov Model",
        "- States used: 4",
        "- Model: GaussianHMM",
        f"- Predictions sample: {hmm_states[:10].tolist()}",
        "",
        "## Notes",
        "- Do not treat demo synthetic results as live signal.",
        "- Next step: attach OHLCV data for the 4 queued strategies.",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(REPORT_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
