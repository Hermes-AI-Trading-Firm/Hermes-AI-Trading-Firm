"""
Equity curve builders for Hermes AI Trading Firm.

All functions are pure — they accept plain Python dicts and return plain
Python dicts. No database access.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List


def build_equity_curve(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build a cumulative PnL equity curve from a list of closed trades.

    Expected trade keys:
        entry_time  — ISO-8601 str used as the timestamp (pass exit_time here
                      if you want to mark trades at close, not open)
        pnl         — float, gross PnL
        commission  — float, total round-trip commission (optional)

    Returns a list of dicts sorted ascending by entry_time:
        time                — trade timestamp
        pnl                 — individual trade gross PnL
        commission          — individual trade commission
        cumulative_pnl      — running sum of gross PnL
        cumulative_net_pnl  — running sum of (pnl − commission)
    """
    if not trades:
        return []

    sorted_trades = sorted(trades, key=lambda t: t.get("entry_time") or "")

    running_pnl = 0.0
    running_net = 0.0
    curve: List[Dict[str, Any]] = []

    for t in sorted_trades:
        pnl  = float(t.get("pnl")        or 0.0)
        comm = float(t.get("commission")  or 0.0)
        running_pnl += pnl
        running_net += pnl - comm
        curve.append({
            "time":                t.get("entry_time") or "",
            "pnl":                 round(pnl,          2),
            "commission":          round(comm,          2),
            "cumulative_pnl":      round(running_pnl,  2),
            "cumulative_net_pnl":  round(running_net,  2),
        })

    return curve


def build_drawdown_curve(equity_curve: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Enrich a cumulative equity curve with running peak and drawdown.

    Input: output of build_equity_curve() (must have cumulative_pnl key).
    Returns the same list with three extra keys added to each point:
        peak            — running peak of cumulative_pnl
        drawdown        — cumulative_pnl − peak  (always ≤ 0)
        drawdown_pct    — drawdown as % of peak  (always ≤ 0)
    """
    if not equity_curve:
        return []

    peak = 0.0
    result: List[Dict[str, Any]] = []

    for point in equity_curve:
        cum  = float(point.get("cumulative_pnl") or 0.0)
        peak = max(peak, cum)
        dd   = cum - peak
        dd_pct = (dd / peak * 100.0) if peak > 0 else 0.0
        result.append({
            **point,
            "peak":         round(peak,   2),
            "drawdown":     round(dd,     2),
            "drawdown_pct": round(dd_pct, 2),
        })

    return result


def build_monthly_returns(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Group closed trades by calendar month (YYYY-MM) and compute monthly stats.

    Expected trade keys: entry_time, pnl, commission (all optional-safe).
    Returns a list sorted ascending by month:
        month       — "YYYY-MM"
        total_pnl   — gross PnL for the month
        net_pnl     — PnL after commission
        trade_count — number of trades
        wins        — winning trades (pnl > 0)
        losses      — losing / break-even trades (pnl ≤ 0)
    """
    buckets: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "month": "",
        "total_pnl":   0.0,
        "net_pnl":     0.0,
        "trade_count": 0,
        "wins":        0,
        "losses":      0,
    })

    for t in trades:
        month = (t.get("entry_time") or "")[:7]   # "YYYY-MM"
        if not month:
            continue
        b = buckets[month]
        b["month"] = month
        pnl  = float(t.get("pnl")        or 0.0)
        comm = float(t.get("commission")  or 0.0)
        b["total_pnl"]   += pnl
        b["net_pnl"]     += pnl - comm
        b["trade_count"] += 1
        if pnl > 0:
            b["wins"]   += 1
        else:
            b["losses"] += 1

    out = sorted(buckets.values(), key=lambda x: x["month"])
    for b in out:
        b["total_pnl"] = round(b["total_pnl"], 2)
        b["net_pnl"]   = round(b["net_pnl"],   2)

    return out
