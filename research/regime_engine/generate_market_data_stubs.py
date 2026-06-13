#!/usr/bin/env python3
"""Generate synthetic OHLCV market data stubs for regime engine testing."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "market_data_stub"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def make_daily_ohlcv(
    seed: int,
    n: int = 252,
    start_price: float = 100.0,
    vol: float = 0.01,
    drift: float = 0.0003,
    volume_mean: float = 1_000_000.0,
    volume_std: float = 200_000.0,
    rng: np.random.Generator | None = None,
) -> pd.DataFrame:
    rng = rng or np.random.default_rng(seed)
    returns = drift + vol * rng.standard_normal(n)
    prices = start_price * np.cumprod(1.0 + returns)
    opens = prices * (1.0 + rng.standard_normal(n) * 0.002)
    highs = np.maximum(opens, prices) * (1.0 + np.abs(rng.standard_normal(n)) * 0.004)
    lows = np.minimum(opens, prices) * (1.0 - np.abs(rng.standard_normal(n)) * 0.004)
    close = prices
    volume = np.clip(volume_mean + volume_std * rng.standard_normal(n), a_min=0, a_max=None).astype(int)
    dates = pd.bdate_range(start="2024-01-02", periods=n)
    return pd.DataFrame({"date": dates, "open": opens, "high": highs, "low": lows, "close": close, "volume": volume})


def main() -> None:
    rng = np.random.default_rng(7)

    assets = {
        "MNQ_ORB_FVG_v001": {"start_price": 19200.0, "vol": 0.008, "drift": 0.0002, "volume_mean": 850_000, "volume_std": 250_000},
        "MGC_VWAP_PULLBACK_v001": {"start_price": 2320.0, "vol": 0.006, "drift": 0.0001, "volume_mean": 120_000, "volume_std": 45_000},
        "BTC_REGIME_BREAKOUT_v001": {"start_price": 42000.0, "vol": 0.025, "drift": 0.0008, "volume_mean": 32_000_000_000, "volume_std": 8_000_000_000},
        "SPY_WHEEL_STRATEGY_v001": {"start_price": 475.0, "vol": 0.007, "drift": 0.0004, "volume_mean": 65_000_000, "volume_std": 15_000_000},
    }

    for name, params in assets.items():
        df = make_daily_ohlcv(seed=hash(name) % (2**32), **params, rng=rng)
        out = DATA_DIR / f"{name}.csv"
        df.to_csv(out, index=False)
        print(out)


if __name__ == "__main__":
    main()
