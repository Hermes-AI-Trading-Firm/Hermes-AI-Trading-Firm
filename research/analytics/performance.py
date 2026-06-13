"""
Performance metric calculators for Hermes AI Trading Firm.

All functions are pure — they accept plain Python lists/dicts and return
scalars or small dicts. No database access.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def calculate_win_rate(trades: List[Dict[str, Any]]) -> float:
    """Win rate as a percentage (0.0 – 100.0)."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if float(t.get("pnl") or 0.0) > 0)
    return round(wins / len(trades) * 100, 2)


def calculate_expectancy(trades: List[Dict[str, Any]]) -> float:
    """Average gross PnL per trade (positive = positive expectancy)."""
    if not trades:
        return 0.0
    total = sum(float(t.get("pnl") or 0.0) for t in trades)
    return round(total / len(trades), 2)


def calculate_profit_factor(trades: List[Dict[str, Any]]) -> Optional[float]:
    """
    Gross profit / gross loss.
    Returns None when there are no losing trades (undefined, not infinity).
    """
    gross_profit = sum(
        float(t.get("pnl") or 0.0) for t in trades if float(t.get("pnl") or 0.0) > 0
    )
    gross_loss = sum(
        abs(float(t.get("pnl") or 0.0)) for t in trades if float(t.get("pnl") or 0.0) < 0
    )
    if gross_loss == 0:
        return None
    return round(gross_profit / gross_loss, 2)


def calculate_sharpe_ratio(
    trades:           List[Dict[str, Any]],
    risk_free_rate:   float = 0.0,
) -> Optional[float]:
    """
    Per-trade Sharpe ratio: (mean_pnl − risk_free) / std_pnl.

    Uses the per-trade PnL series as the return series (not time-period
    returns), so the result is a trade-level Sharpe, not annualised.
    Returns None for < 2 trades or zero standard deviation.
    """
    if len(trades) < 2:
        return None

    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    n    = len(pnls)
    mean = sum(pnls) / n
    var  = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    std  = math.sqrt(var)

    if std == 0:
        return None

    return round((mean - risk_free_rate) / std, 4)


def calculate_max_drawdown(
    equity_curve: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Extract max drawdown from a build_drawdown_curve() result.

    Returns:
        max_drawdown      — most negative drawdown value (≤ 0)
        max_drawdown_pct  — as a percentage of peak (≤ 0)
        trough_at         — timestamp of the worst drawdown point
    """
    if not equity_curve:
        return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0, "trough_at": None}

    worst = min(equity_curve, key=lambda p: p.get("drawdown", 0.0))
    return {
        "max_drawdown":     worst.get("drawdown",     0.0),
        "max_drawdown_pct": worst.get("drawdown_pct", 0.0),
        "trough_at":        worst.get("time"),
    }


def calculate_consecutive_wins_losses(
    trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Compute win/loss streak metrics from the trade list.

    Returns:
        current_streak   — {"type": "wins"|"losses"|"none", "count": int}
        best_win_streak  — longest consecutive winning streak
        best_loss_streak — longest consecutive losing streak
    """
    if not trades:
        return {
            "current_streak":   {"type": "none", "count": 0},
            "best_win_streak":  0,
            "best_loss_streak": 0,
        }

    sorted_trades = sorted(trades, key=lambda t: t.get("entry_time") or "")

    best_w = best_l = cur_w = cur_l = 0
    for t in sorted_trades:
        pnl = float(t.get("pnl") or 0.0)
        if pnl > 0:
            cur_w += 1
            cur_l  = 0
            best_w = max(best_w, cur_w)
        else:
            cur_l += 1
            cur_w  = 0
            best_l = max(best_l, cur_l)

    # Current streak: walk backwards from the last trade
    last_pnl    = float(sorted_trades[-1].get("pnl") or 0.0)
    streak_type = "wins" if last_pnl > 0 else "losses"
    count       = 0
    for t in reversed(sorted_trades):
        pnl = float(t.get("pnl") or 0.0)
        if (streak_type == "wins" and pnl > 0) or (streak_type == "losses" and pnl <= 0):
            count += 1
        else:
            break

    return {
        "current_streak":   {"type": streak_type, "count": count},
        "best_win_streak":  best_w,
        "best_loss_streak": best_l,
    }
