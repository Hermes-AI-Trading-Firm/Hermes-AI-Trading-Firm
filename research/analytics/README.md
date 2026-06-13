# research/analytics/

Performance analytics module for Hermes AI Trading Firm.

Transforms imported NT8 trade data into equity curves, drawdown curves, and
performance metrics. **Read-only — no database access, no live trading.**

---

## Modules

### `equity.py`

| Function | Returns | Description |
|---|---|---|
| `build_equity_curve(trades)` | `List[Dict]` | Cumulative PnL curve sorted by close time |
| `build_drawdown_curve(equity_curve)` | `List[Dict]` | Enriches equity curve with peak, drawdown, drawdown_pct |
| `build_monthly_returns(trades)` | `List[Dict]` | Monthly PnL, trade count, W/L grouped by YYYY-MM |

### `performance.py`

| Function | Returns | Description |
|---|---|---|
| `calculate_win_rate(trades)` | `float` | Win % (0–100) |
| `calculate_expectancy(trades)` | `float` | Average gross PnL per trade |
| `calculate_profit_factor(trades)` | `float \| None` | Gross profit / gross loss |
| `calculate_sharpe_ratio(trades)` | `float \| None` | Per-trade Sharpe (not annualised) |
| `calculate_max_drawdown(equity_curve)` | `Dict` | Worst drawdown $ and % |
| `calculate_consecutive_wins_losses(trades)` | `Dict` | Current streak + best win/loss streak |

---

## Inputs

All functions accept plain Python dicts. Expected trade keys:

| Key | Type | Notes |
|---|---|---|
| `entry_time` | `str` | ISO-8601 — pass `exit_time` here for close-based ordering |
| `pnl` | `float` | Gross PnL of the closed trade |
| `commission` | `float` | Optional; used for net PnL calculation |

---

## API Endpoints

| Endpoint | Query function | Description |
|---|---|---|
| `GET /equity-curve` | `queries.equity_curve()` | Full equity + drawdown curve |
| `GET /performance-summary` | `queries.performance_summary()` | All metrics + monthly returns |

---

## `/equity-curve` Response Shape

```json
{
  "count": 5,
  "items": [
    {
      "time": "2026-06-10T09:47:00",
      "pnl": 140.5,
      "cumulative_pnl": 140.5,
      "commission": 4.16,
      "cumulative_net_pnl": 136.34,
      "peak": 140.5,
      "drawdown": 0.0,
      "drawdown_pct": 0.0
    }
  ],
  "summary": {
    "current_cumulative_pnl": 378.5,
    "peak_pnl": 378.5,
    "max_drawdown": -61.5,
    "max_drawdown_pct": -43.83
  }
}
```

## `/performance-summary` Response Shape

```json
{
  "total_trades": 5,
  "total_pnl": 378.5,
  "win_rate": 80.0,
  "expectancy": 75.7,
  "profit_factor": 7.15,
  "sharpe_ratio": 0.9352,
  "max_drawdown": -61.5,
  "max_drawdown_pct": -43.83,
  "avg_win": 110.0,
  "avg_loss": -61.5,
  "current_streak": {"type": "wins", "count": 3},
  "best_win_streak": 4,
  "best_loss_streak": 1,
  "monthly_returns": [
    {"month": "2026-06", "total_pnl": 378.5, "net_pnl": 357.7, "trade_count": 5, "wins": 4, "losses": 1}
  ]
}
```

---

## Safety Boundaries

- No database writes.
- No live trading. No broker connection.
- No order placement or ATM control.
- All functions are pure Python with no external dependencies.
