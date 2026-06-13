#!/usr/bin/env python3
"""Fallback batch regime report generator (no numpy/pandas required).

Generates required report structure from the market data stubs with
deterministic approximations instead of heavy numeric deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev
from typing import Iterable, List

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "market_data_stub"
REPORT_DIR = ROOT / "reports" / "regime"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

STRATEGIES = [
    {
        "id": "MNQ_ORB_FVG_v001",
        "meta": {
            "asset_class": "futures",
            "symbol": "MNQ",
            "timeframe": "daily",
            "session": "RTH",
        },
    },
    {
        "id": "MGC_VWAP_PULLBACK_v001",
        "meta": {
            "asset_class": "futures",
            "symbol": "MGC",
            "timeframe": "daily",
            "session": "COMEX electronic",
        },
    },
    {
        "id": "BTC_REGIME_BREAKOUT_v001",
        "meta": {
            "asset_class": "crypto",
            "symbol": "BTCUSDT",
            "timeframe": "daily",
            "session": "24h UTC",
        },
    },
    {
        "id": "SPY_WHEEL_STRATEGY_v001",
        "meta": {
            "asset_class": "options",
            "symbol": "SPY",
            "timeframe": "daily",
            "session": "RTH close",
        },
    },
]


@dataclass
class Row:
    date: str
    open_: float
    high: float
    low: float
    close: float
    volume: int


def read_csv(path: Path) -> list[Row]:
    rows: list[Row] = []
    text = path.read_text(encoding="utf-8").splitlines()
    header = text[0].split(",")
    for line in text[1:]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        rows.append(
            Row(
                date=parts[header.index("date")],
                open_=float(parts[header.index("open")]),
                high=float(parts[header.index("high")]),
                low=float(parts[header.index("low")]),
                close=float(parts[header.index("close")]),
                volume=int(parts[header.index("volume")]),
            )
        )
    return rows


def rolling_mean(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i < window - 1:
            out.append(None)
            continue
        window_vals = values[i - window + 1 : i + 1]
        out.append(mean(window_vals))
    return out


def rolling_std(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i < window - 1:
            out.append(None)
            continue
        window_vals = values[i - window + 1 : i + 1]
        out.append(stdev(window_vals) if len(window_vals) > 1 else 0.0)
    return out


def compute_20d_return(prices: list[float]) -> list[float]:
    ret: list[float] = [0.0] * len(prices)
    for i in range(20, len(prices)):
        if prices[i - 20] != 0:
            ret[i] = prices[i] / prices[i - 20] - 1.0
    return ret


def classify_regimes(prices: list[float], fast: int = 20, slow: int = 50) -> list[str]:
    fast_ma = rolling_mean(prices, fast)
    slow_ma = rolling_mean(prices, slow)
    returns20 = compute_20d_return(prices)
    regimes: list[str] = []
    labels = {1: "Bull", 2: "Bear", 3: "Sideways", 4: "Transition"}
    vol = rolling_std(returns20, 20)
    last_label = "Sideways"
    prev_vol = 0.0
    for i in range(len(prices)):
        f = fast_ma[i]
        s = slow_ma[i]
        r = returns20[i]
        v = vol[i] if vol[i] is not None else 0.0
        prev_v = vol[i - 1] if i > 0 and vol[i - 1] is not None else prev_vol
        prev_vol = prev_v
        vol_spike = v > 1.25 * prev_v if prev_v else False
        if f is None or s is None:
            label = "Transition"
        elif f > s and r > 0.0:
            label = "Bull"
        elif f < s and r < 0.0:
            label = "Bear"
        elif abs((f or 0.0) - (s or 0.0)) <= 0.6 * max(abs(f or 0.0), abs(s or 0.0), 1e-9):
            label = "Sideways"
        else:
            label = "Transition"
        if vol_spike:
            label = "Transition"
        last_label = label
        regimes.append(label)
    return regimes


def fit_markov(regimes: list[str]) -> tuple[list[list[float]], list[str]]:
    labels = ["Bull", "Bear", "Sideways", "Transition"]
    idx = {name: i for i, name in enumerate(labels)}
    counts = [[0.0] * 4 for _ in labels]
    for a, b in zip(regimes[:-1], regimes[1:]):
        counts[idx[a]][idx[b]] += 1
    probs: list[list[float]] = []
    for i in range(4):
        row = counts[i]
        s = sum(row) or 1.0
        probs.append([round(v / s, 4) for v in row])
    return probs, labels


def regime_counts(regimes: list[str]) -> dict[str, int]:
    counts = {"Bull": 0, "Bear": 0, "Sideways": 0, "Transition": 0}
    for label in regimes:
        if label in counts:
            counts[label] += 1
    return counts


def stickiness(probs: list[list[float]]) -> float:
    diag = [probs[i][i] for i in range(4)]
    return round(mean(diag), 4)


def build_report(strategy: dict[str, object], df: list[Row], regimes: list[str]) -> str:
    probs, labels = fit_markov(regimes)
    counts = regime_counts(regimes)
    last = regimes[-1]
    stick = stickiness(probs)

    lines = [
        f"# {strategy['id']} Regime Report",
        "",
        "## Strategy",
        f"- Spec ID: {strategy['id']}",
        f"- Symbol: {strategy['meta']['symbol']}",
        f"- Asset class: {strategy['meta']['asset_class']}",
        f"- Timeframe: {strategy['meta']['timeframe']}",
        f"- Session: {strategy['meta']['session']}",
        "",
        "## Current Regime",
        f"- {last}",
        "",
        "## Regime Counts",
        f"- Bull: {counts['Bull']}",
        f"- Bear: {counts['Bear']}",
        f"- Sideways: {counts['Sideways']}",
        f"- Transition: {counts['Transition']}",
        "",
        "## Markov Transition Matrix",
        "| From / To | Bull | Bear | Sideways | Transition |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, row in zip(labels, probs):
        lines.append(f"| {label} | " + " | ".join([f"{v:.2f}" for v in row]) + " |")
    lines += [
        "",
        "## Stickiness Score",
        f"- {stick}",
        "",
        "## HMM Regime Summary",
        "- Model: lightweight rule-based surrogate (fallback GaussianHMM not installed).",
        "- Inference: Falling back to rule-based states.",
        "",
        "## Interpretation",
        f"- Current regime: {last}.",
        "- Review the counts and transition matrix above to assess persistence.",
        "- High transition probability into Transition indicates choppy or changing conditions.",
        "",
        "## Regime Filtering Recommendation",
        "- Recommended to test regime filtering first because Transition count is present in the stub dataset.",
    ]
    return "\n".join(lines)


def main() -> None:
    generated: list[str] = []
    for strategy in STRATEGIES:
        path = DATA_DIR / f"{strategy['id']}.csv"
        df = read_csv(path)
        closes = [row.close for row in df]
        regimes = classify_regimes(closes)
        report = build_report(strategy, df, regimes)
        out = REPORT_DIR / f"{strategy['id']}_regime_report.md"
        out.write_text(report, encoding="utf-8")
        generated.append(str(out))
        print(out)


if __name__ == "__main__":
    main()
