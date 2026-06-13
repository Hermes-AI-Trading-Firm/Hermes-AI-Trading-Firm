"""Market Regime Engine

Detects and labels market regimes for strategy evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

# --------------------
# Data structures
# --------------------


@dataclass(frozen=True)
class RegimeSeries:
    dates: np.ndarray
    regimes: np.ndarray
    returns: np.ndarray
    prices: np.ndarray

    def __post_init__(self) -> None:
        if not (len(self.dates) == len(self.regimes) == len(self.returns) == len(self.prices)):
            raise ValueError("All input arrays must have the same length.")


@dataclass(frozen=True)
class RegimeStats:
    regime: str
    count: int
    win_rate: Optional[float] = None
    avg_return: Optional[float] = None
    sharpe: Optional[float] = None
    max_drawdown: Optional[float] = None
    hold_days: Optional[float] = None


@dataclass(frozen=True)
class RegimeReport:
    stats: List[RegimeStats]
    last_regime: str
    recommended_filter: Optional[str]


# --------------------
# 20-day return model
# --------------------


def compute_20d_return(close: Sequence[float]) -> np.ndarray:
    """Return rolling 20-day simple return."""
    close = np.asarray(close, dtype=float)
    ret = np.empty_like(close)
    ret[:] = np.nan
    if len(close) >= 21:
        ret[20:] = close[20:] / close[:-20] - 1.0
    return ret


# --------------------
# Regime Classification
# --------------------


def classify_regimes(
    close: Sequence[float],
    *,
    fast_window: int = 20,
    slow_window: int = 50,
    vol_window: int = 20,
    transition_speed: float = 0.6,
) -> RegimeSeries:
    """Assign simple rule-based regimes.

    - Bull: fast SMA > slow SMA AND rising momentum
    - Bear: fast SMA < slow SMA AND falling momentum
    - Sideways: both moving averages flat / crossing
    - Transition: SMA cross or vol spike

    Returns regime codes:
      1=Bull, 2=Bear, 3=Sideways, 4=Transition
    """
    close_arr = np.asarray(close, dtype=float)
    ret = compute_20d_return(close_arr)

    fast = _rolling_mean(close_arr, fast_window)
    slow = _rolling_mean(close_arr, slow_window)
    vol = _rolling_std(_safe_returns(close_arr), vol_window)

    regimes = np.full(len(close_arr), 3, dtype=int)

    bull_mask = (fast > slow) & (ret > 0.0)
    bear_mask = (fast < slow) & (ret < 0.0)
    cross_mask = np.abs(fast - slow) <= transition_speed * np.maximum(np.abs(fast), np.abs(slow))
    vol_spike = vol > 1.25 * _rolling_std(vol, vol_window)

    regimes = np.where(bull_mask, 1, 3)
    regimes = np.where(bear_mask, 2, regimes)
    regimes = np.where(cross_mask | vol_spike, 4, regimes)

    dates = np.arange(len(close_arr), dtype=int)
    return RegimeSeries(
        dates=dates,
        regimes=regimes,
        returns=np.nan_to_num(np.asarray(ret, dtype=float)),
        prices=close_arr,
    )


# --------------------
# Markov transition matrix
# --------------------


def fit_markov(regimes: np.ndarray) -> Tuple[np.ndarray, List[str]]:
    """Estimate regime transition probabilities."""
    codes = [1, 2, 3, 4]
    labels = {1: "Bull", 2: "Bear", 3: "Sideways", 4: "Transition"}
    counts = np.zeros((4, 4), dtype=float)
    for a, b in zip(regimes[:-1], regimes[1:]):
        i = codes.index(int(a))
        j = codes.index(int(b))
        counts[i, j] += 1
    row_sums = counts.sum(axis=1, keepdims=True)
    probs = np.divide(counts, row_sums, out=np.zeros_like(counts), where=row_sums > 0)
    return np.round(probs, 4), [labels[c] for c in codes]


# --------------------
# Simple HMM architecture
# --------------------


class GaussianHMM:
    """Minimal 4-state HMM for regime detection with Gaussian emissions.

    This is an intentionally simple architecture using Baum-Welch style
    re-estimation from `scipy`. Use for research prototyping only.
    """

    def __init__(self, n_states: int = 4, max_iter: int = 25, tol: float = 1e-4) -> None:
        self.n_states = int(n_states)
        self.max_iter = int(max_iter)
        self.tol = float(tol)
        self.mu: Optional[np.ndarray] = None
        self.var_: Optional[np.ndarray] = None
        self.trans: Optional[np.ndarray] = None
        self.pi: Optional[np.ndarray] = None

    def _init_params(self, obs: np.ndarray) -> None:
        rng = np.random.default_rng(42)
        self.mu = np.array(np.percentile(obs, np.linspace(5, 95, self.n_states)), dtype=float)
        self.var_ = np.full(self.n_states, np.var(obs) / self.n_states)
        self.trans = np.full((self.n_states, self.n_states), 1.0 / self.n_states)
        self.pi = np.full(self.n_states, 1.0 / self.n_states)

    def _forward_backward(self, obs: np.ndarray):
        """Simplified inference returning state responsibilities."""
        T = obs.shape[0]
        if self.mu is None or self.var_ is None or self.trans is None or self.pi is None:
            raise RuntimeError("Model parameters not initialized.")
        log_emit = -0.5 * ((obs[:, None] - self.mu[None, :]) ** 2) / self.var_[None, :]
        log_emit -= 0.5 * np.log(2.0 * np.pi * self.var_[None, :])
        log_emit = np.nan_to_num(log_emit)

        log_a = np.log(self.pi + 1e-12)
        log_t = np.log(self.trans + 1e-12)

        alpha = np.empty((T, self.n_states))
        alpha[0] = log_a + log_emit[0]
        for t in range(1, T):
            prev = alpha[t - 1][:, None] + log_t
            alpha[t] = np.logaddexp.reduce(prev, axis=0) + log_emit[t]

        beta = np.empty((T, self.n_states))
        beta[-1] = 0.0
        for t in range(T - 2, -1, -1):
            nxt = log_t + beta[t + 1][None, :] + log_emit[t + 1][None, :]
            beta[t] = np.logaddexp.reduce(nxt, axis=1)

        log_gamma = alpha + beta
        gamma = np.exp(log_gamma - np.logaddexp.reduce(log_gamma, axis=1, keepdims=True))
        return gamma

    def fit(self, obs: np.ndarray) -> "GaussianHMM":
        obs = np.asarray(obs, dtype=float)
        obs = np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)
        self._init_params(obs)
        prev_loglik = -np.inf
        for _ in range(self.max_iter):
            gamma = self._forward_backward(obs)
            self.pi = np.clip(gamma[0], 1e-8, 1.0)
            self.pi /= self.pi.sum()
            for k in range(self.n_states):
                mask = gamma[:, k] > 0.0
                self.mu[k] = np.average(obs, weights=gamma[:, k])
                diff = obs - self.mu[k]
                self.var_[k] = np.average(diff * diff, weights=gamma[:, k]) + 1e-9
            trans_num = np.zeros_like(self.trans)
            for t in range(len(obs) - 1):
                outer = np.outer(gamma[t], gamma[t + 1])
                trans_num += outer
            trans_den = gamma[:-1].sum(axis=0, keepdims=True)
            self.trans = np.divide(trans_num, trans_den.T, out=np.zeros_like(self.trans), where=trans_den.T > 0)
            row_sums = self.trans.sum(axis=1, keepdims=True)
            self.trans = np.divide(self.trans, row_sums, out=np.zeros_like(self.trans), where=row_sums > 0)
        return self

    def predict(self, obs: np.ndarray) -> np.ndarray:
        obs = np.asarray(obs, dtype=float)
        obs = np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)
        gamma = self._forward_backward(obs)
        return np.argmax(gamma, axis=1)


# --------------------
# Reporting
# --------------------


REGIME_LABELS = {1: "Bull", 2: "Bear", 3: "Sideways", 4: "Transition"}


def regime_performance(series: RegimeSeries) -> RegimeReport:
    stats: List[RegimeStats] = []
    for code, name in REGIME_LABELS.items():
        mask = series.regimes == code
        count = int(mask.sum())
        if count == 0:
            stats.append(RegimeStats(regime=name, count=0))
            continue
        r = series.returns[mask]
        win = float(np.mean(r > 0.0))
        avg = float(np.mean(r))
        std = float(np.std(r))
        sharpe = float((avg / std) * np.sqrt(252)) if std > 1e-12 else float("nan")
        prices = series.prices[mask]
        if len(prices) > 1:
            running = np.cumprod(1.0 + r)
            dd = float(np.min(running / np.maximum.accumulate(running) - 1.0))
        else:
            dd = 0.0
        stats.append(
            RegimeStats(
                regime=name,
                count=count,
                win_rate=round(win, 4),
                avg_return=round(avg, 6),
                sharpe=round(float(sharpe), 4) if np.isfinite(sharpe) else None,
                max_drawdown=round(dd, 4),
            )
        )
    last = REGIME_LABELS.get(int(series.regimes[-1]), "Sideways")
    return RegimeReport(stats=stats, last_regime=last, recommended_filter=None)


def render_regime_report(report: RegimeReport) -> str:
    lines = ["# Regime Report", ""]
    for s in report.stats:
        lines.append(f"- {s.regime}: count={s.count}, win_rate={s.win_rate}, avg_return={s.avg_return}, sharpe={s.sharpe}, max_drawdown={s.max_drawdown}")
    lines += [
        "",
        f"Last regime: {report.last_regime}",
    ]
    return "\n".join(lines)


# --------------------
# Utilities
# --------------------


def _rolling_mean(x: np.ndarray, window: int):
    if len(x) < window:
        return np.full_like(x, np.nan)
    csum = np.cumsum(np.insert(x, 0, 0.0))
    out = (csum[window:] - csum[:-window]) / float(window)
    return np.concatenate([np.full(window - 1, np.nan), out])


def _rolling_std(x: np.ndarray, window: int):
    out = np.full_like(x, np.nan, dtype=float)
    if len(x) < window:
        return out
    for i in range(window - 1, len(x)):
        out[i] = float(np.std(x[i - window + 1 : i + 1], ddof=1))
    return out


def _safe_returns(close: Sequence[float]) -> np.ndarray:
    c = np.asarray(close, dtype=float)
    r = np.empty_like(c)
    r[0] = 0.0
    r[1:] = np.where(c[:-1] > 0, c[1:] / c[:-1] - 1.0, 0.0)
    return r
