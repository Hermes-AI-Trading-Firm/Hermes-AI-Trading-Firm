"""Monte Carlo Testing Engine"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

# --------------------
# Data structures
# --------------------


@dataclass(frozen=True)
class MonteCarloResult:
    final_equity: float
    max_drawdown: float
    longest_losing_streak: int
    ruined: bool


@dataclass(frozen=True)
class MonteCarloReport:
    simulations: int
    median_ending_equity: float
    best_ending_equity: float
    worst_ending_equity: float
    pct_5_ending_equity: float
    pct_95_ending_equity: float
    probability_of_loss: float
    drawdown_breach_probability: float
    max_drawdowns: List[float]
    longest_losing_streaks: List[int]
    risk_of_ruin: float
    pass_status: bool


# --------------------
# Simulation primitives
# --------------------


def _apply_slippage(returns: Sequence[float], slippage: float) -> List[float]:
    return [r - slippage for r in returns]


def _apply_commission(returns: Sequence[float], commission: float) -> List[float]:
    return [r - commission for r in returns]


def _random_miss(returns: List[float], miss_prob: float, rng) -> List[float]:
    out = []
    for r in returns:
        if rng.random() < miss_prob:
            out.append(0.0)
        else:
            out.append(r)
    return out


def _worse_fill(returns: List[float], worse_factor: float, rng) -> List[float]:
    return [r * (1.0 - worse_factor * rng.random()) for r in returns]


def _run_sequence(returns: Sequence[float], start_equity: float, max_dd_limit: Optional[float], daily_loss_limit: Optional[float], trailing_drawdown: bool) -> MonteCarloResult:
    equity = start_equity
    peak = start_equity
    max_dd = 0.0
    losing_streak = 0
    longest_losing_streak = 0
    ruined = False

    for r in returns:
        equity += equity * r
        if equity <= 0:
            equity = 0.0
            ruined = True
            break
        if equity > peak:
            peak = equity
            if trailing_drawdown:
                pass
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        if max_dd_limit is not None and dd > max_dd_limit:
            ruined = True
            break
        if daily_loss_limit is not None and r < 0:
            losing_streak += 1
            if losing_streak > 10:
                ruined = True
                break
        else:
            losing_streak = 0
        longest_losing_streak = max(longest_losing_streak, losing_streak)
    return MonteCarloResult(final_equity=float(equity), max_drawdown=float(max_dd), longest_losing_streak=int(longest_losing_streak), ruined=bool(ruined))


# --------------------
# Core runner
# --------------------


def run_monte_carlo(
    trade_returns: Sequence[float],
    simulations: int = 1000,
    start_equity: float = 100_000.0,
    slippage: float = 0.0005,
    commission: float = 0.0002,
    miss_prob: float = 0.05,
    worse_fill: float = 0.15,
    max_dd_limit: Optional[float] = 0.20,
    daily_loss_limit: Optional[float] = -0.02,
    trailing_drawdown: bool = False,
    seed: int = 0,
) -> MonteCarloReport:
    if not trade_returns:
        raise ValueError("trade_returns must not be empty.")
    base = [float(r) for r in trade_returns]
    results: List[MonteCarloResult] = []
    rng = __import__("random").Random(seed)
    for _ in range(simulations):
        seq = list(base)
        rng.shuffle(seq)
        seq = _apply_slippage(seq, slippage)
        seq = _apply_commission(seq, commission)
        seq = _random_miss(seq, miss_prob, rng)
        seq = _worse_fill(seq, worse_fill, rng)
        results.append(_run_sequence(seq, start_equity, max_dd_limit, daily_loss_limit, trailing_drawdown))
    finals = [r.final_equity for r in results]
    finals_sorted = sorted(finals)
    median = float(finals_sorted[len(finals_sorted) // 2])
    best = float(finals_sorted[-1])
    worst = float(finals_sorted[0])
    pct_5 = float(finals_sorted[int(len(finals_sorted) * 0.05)])
    pct_95 = float(finals_sorted[int(len(finals_sorted) * 0.95)])
    loss_count = sum(1 for f in finals if f < start_equity)
    prob_loss = loss_count / simulations
    dd_breach = sum(1 for r in results if r.max_drawdown > (max_dd_limit or 1.0))
    dd_breach_prob = dd_breach / simulations
    longest = [r.longest_losing_streak for r in results]
    risk_of_ruin = sum(1 for r in results if r.ruined) / simulations
    pass_status = (
        prob_loss <= 0.45
        and pct_5 > 0.0
        and dd_breach_prob <= 0.25
        and max(longest) <= 20
        and risk_of_ruin <= 0.25
    )
    return MonteCarloReport(
        simulations=simulations,
        median_ending_equity=median,
        best_ending_equity=best,
        worst_ending_equity=worst,
        pct_5_ending_equity=pct_5,
        pct_95_ending_equity=pct_95,
        probability_of_loss=round(prob_loss, 4),
        drawdown_breach_probability=round(dd_breach_prob, 4),
        max_drawdowns=[round(r.max_drawdown, 4) for r in results],
        longest_losing_streaks=longest,
        risk_of_ruin=round(risk_of_ruin, 4),
        pass_status=bool(pass_status),
    )


# --------------------
# Reporting
# --------------------


def render_report(report: MonteCarloReport, start_equity: float) -> str:
    lines = [
        "# Monte Carlo Report",
        "",
        "## Summary",
        f"- Simulations: {report.simulations}",
        f"- Median ending equity: {report.median_ending_equity:,.2f}",
        f"- Best ending equity: {report.best_ending_equity:,.2f}",
        f"- Worst ending equity: {report.worst_ending_equity:,.2f}",
        f"- 5th percentile equity: {report.pct_5_ending_equity:,.2f}",
        f"- 95th percentile equity: {report.pct_95_ending_equity:,.2f}",
        f"- Probability of loss: {report.probability_of_loss:.2%}",
        f"- Drawdown breach probability: {report.drawdown_breach_probability:.2%}",
        f"- Risk of ruin: {report.risk_of_ruin:.2%}",
        "",
        "## Pass Status",
        f"- {'PASS' if report.pass_status else 'FAIL'}",
        "",
        "## Distribution Notes",
        "- Max drawdown distribution saved in JSON metadata only.",
        "- Longest losing streak max: " + str(max(report.longest_losing_streaks)),
        "",
        "## Rules Applied",
        "- Bootstrap trade shuffle",
        "- Slippage stress",
        "- Commission stress",
        "- Random missed trades",
        "- Worse-fill stress",
        "",
        "## Safeguards",
        "- No lookahead: each simulation uses only base trade sequence.",
        "- No future leakage: parameters fixed before simulation.",
    ]
    return "\n".join(lines)
