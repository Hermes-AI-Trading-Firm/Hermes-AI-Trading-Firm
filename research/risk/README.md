# research/risk/

Risk compliance module for Hermes AI Trading Firm.

Evaluates NT8 account state and strategy specs against prop firm rules and
CLAUDE.md risk thresholds. **Read-only — never writes to the database.**

---

## Purpose

- Classify account drawdown state as SAFE / WARNING / DANGER / VIOLATION
- Validate strategy specs against baseline quality thresholds
- Compute a 0.0–1.0 health score for the active trading account
- Compare one NT8 account snapshot against all active prop firm rule sets simultaneously

---

## Inputs

| Source | Table | Fields used |
|---|---|---|
| Account snapshot | `nt8_account_snapshots` | `equity`, `daily_pnl`, `trailing_drawdown_used`, `trailing_drawdown_limit`, `account_id` |
| Trades (optional) | `nt8_trades` | `quantity`, `entry_time`, `account_id` |
| Prop firm rules | `prop_firm_profiles` | `trailing_drawdown_limit`, `daily_loss_limit`, `profit_target`, `min_trading_days`, `max_position_size`, `consistency_rule` |
| Strategy specs | `strategy_specs` | `spec_id`, `spec_name`, `status` |
| Backtest results | `backtests` | `profit_factor`, `max_drawdown_pct`, `total_trades`, `sharpe_ratio` |

---

## Outputs

### `evaluate_account_compliance(snapshot, rule, trades=None)`

Returns `AccountComplianceResult` with:

| Field | Description |
|---|---|
| `status` | SAFE / WARNING / DANGER / VIOLATION |
| `health_score` | float 0.0–1.0 |
| `dd_ratio` | trailing_drawdown_used / dd_limit |
| `daily_ratio` | abs(daily_loss) / daily_loss_limit |
| `violations` | list of hard rule breaches |
| `warnings` | list of soft threshold alerts |

### `evaluate_strategy_compliance(spec)`

Returns `StrategyComplianceResult` with `status`, `violations`, and `warnings`
mapped to CLAUDE.md thresholds.

### `calculate_health_score(dd_ratio, daily_ratio, equity_ratio)`

Returns float 0.0–1.0. Pure function — no DB access.

### `classify_status(violations, warnings)`

Returns `Status` enum value. Pure function.

### `run_full_compliance(conn)`

Convenience wrapper — loads snapshot + prop firm rules + strategy specs from DB
and returns one JSON-serializable dict:

```json
{
  "firm_health_score": 0.97,
  "firm_status": "SAFE",
  "account_count": 3,
  "accounts": [
    {
      "account_id": "Sim101",
      "firm_name": "Apex",
      "health_score": 0.9694,
      "status": "SAFE",
      "dd_ratio": 0.0606,
      "daily_ratio": 0.0,
      "violations": [],
      "warnings": []
    }
  ],
  "strategy_count": 4,
  "strategies": [...]
}
```

---

## Risk Thresholds

### Account (prop firm rules from `prop_firm_profiles`)

| Check | Trigger | Status |
|---|---|---|
| Trailing DD | ≥ 80% of limit | WARNING |
| Trailing DD | ≥ 100% of limit | VIOLATION |
| Daily loss | ≥ 80% of daily limit | WARNING |
| Daily loss | ≥ 100% of daily limit | VIOLATION |
| Equity floor | equity < account_size × (1 − DD limit) | VIOLATION |
| Position size | any trade qty > `max_position_size` | VIOLATION |
| Trading days | fewer than `min_trading_days` recorded | WARNING |

### Strategy (CLAUDE.md thresholds)

| Metric | Threshold | Status |
|---|---|---|
| Profit Factor | < 1.0 | VIOLATION |
| Profit Factor | < 1.20 | WARNING |
| Max Drawdown | > 25% | VIOLATION |
| Max Drawdown | > 20% | WARNING |
| Trade Count | < 30 | WARNING |
| Sharpe Ratio | < 0.0 | WARNING |

### Health Score Formula

```
score = 0.40 × (1 − dd_ratio)
      + 0.30 × (1 − daily_ratio)
      + 0.20 × (equity / account_size)
      + 0.10 × 1.0
```

Clamped to [0.0, 1.0].

### Status Classification

| Condition | Status |
|---|---|
| Any hard limit breached | VIOLATION |
| 2 or more warnings | DANGER |
| 1 warning | WARNING |
| No issues | SAFE |

---

## Prop Firm Templates (`prop_rules.py`)

Built-in templates for `from_template(name, account_size)`:

| Name | DD Limit | Daily Limit | Profit Target | Consistency Rule |
|---|---|---|---|---|
| `apex` | 8% | 2% | 10% | No |
| `topstep` | 6% | 2% | 10% | No |
| `ftmo` | 10% | 5% | 10% | Yes |
| `custom` | 5% | 2% | 8% | No |

All thresholds are fractions of `account_size`. Dollar values are computed
automatically in `PropRule.__post_init__`.

---

## Safety Boundaries

- No database writes.
- No live trading. No broker or NT8 connection.
- No order placement. Evaluation only.
- No API endpoint yet. Direct Python import only.
- Human approval gate (CLAUDE.md §5) is not bypassed or automated.

---

## Future: API Endpoint

Planned: `GET /compliance-report`

Calls `run_full_compliance(conn)` and returns the full dict.
Dashboard: Row 8 — full-width, firm health score + per-account status cards
+ strategy compliance table.

---

## Usage

```bash
# From project root
python -m research.risk.compliance
```

```python
import sqlite3
from research.risk.compliance import run_full_compliance

conn   = sqlite3.connect("database/hermes_research.db")
report = run_full_compliance(conn)
conn.close()
```
