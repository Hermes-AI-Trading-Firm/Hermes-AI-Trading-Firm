# research/scoring

Production-ready scoring engine for Hermes AI Trading Firm strategies.

## Files

| File | Purpose |
|------|---------|
| `weights.py` | All numeric constants — weights, thresholds, grade bands |
| `scoring.py` | Scoring logic, dataclasses, DB persistence |
| `__init__.py` | Package marker |

---

## Inputs (`ScoringInput`)

| Field | Type | Description |
|-------|------|-------------|
| `spec_id` | `int` | Foreign key → `strategy_specs.spec_id` |
| `backtest` | `dict` | Backtest metrics (see keys below) |
| `regime` | `dict` | Regime engine output |
| `wf` | `dict` | Walk-forward engine output |
| `mc` | `dict` | Monte Carlo engine output |
| `prop_firm` | `dict` | Prop firm profile snapshot |

### `backtest` keys

| Key | Type | Description |
|-----|------|-------------|
| `profit_factor` | float | Gross profit / gross loss |
| `expectancy` | float | Mean PnL per trade |
| `win_rate` | float | Fraction of winning trades (0.0–1.0) |
| `max_drawdown` | float | Max drawdown as fraction (e.g. −0.15 = −15%) |
| `trades` | int | Total trade count |
| `rules_documented` | bool | True if entry/exit rules are fully documented |

---

## Outputs (`ScoringResult`)

| Field | Description |
|-------|-------------|
| `composite_score` | 0–100 weighted composite |
| `grade` | A+ / A / B / C / D / Reject |
| `recommendation` | Live Candidate / Forward Test / Optimize / Retest / Reject |
| `component_scores` | Per-category float scores (0.0–1.0) |
| `gate_failures` | Hard-gate violations (override grade to Reject) |
| `overfit_warnings` | Human-readable overfit risk messages |
| `overfitting_risk` | 0.0–1.0 risk score (max 10-point deduction from composite) |
| `monte_carlo_pass` | bool |
| `walk_forward_pass` | bool |
| `prop_firm_supported` | bool |
| `prop_firm_support` | Full prop firm review dict |

---

## Scoring Formula

```
composite = (Σ component_i × weight_i / Σ weight_i) × 100 − (overfit_risk × 10)
composite = clamp(composite, 0, 100)
```

None-valued components are skipped; weights are renormalised over available data.

### Component Weights

| Component | Weight | DB Column |
|-----------|--------|-----------|
| profitability | 0.30 | `profitability_score` |
| drawdown | 0.20 | `drawdown_score` |
| consistency | 0.15 | `consistency_score` |
| regime | 0.10 | `regime_score` |
| monte_carlo | 0.10 | `monte_carlo_score` |
| walk_forward | 0.05 | `walk_forward_score` |
| robustness | 0.05 | `robustness_score` |
| prop_firm | 0.03 | `prop_firm_score` |
| explainability | 0.02 | `explainability_score` |

### Grade Bands

| Grade | Range | Recommendation |
|-------|-------|----------------|
| A+ | 90–100 | Live Candidate |
| A | 80–89 | Forward Test |
| B | 70–79 | Optimize |
| C | 60–69 | Retest |
| D | < 60 | Reject |
| Reject | — | Reject (hard gate override) |

---

## Hard Gates

Any gate failure forces grade → **Reject** regardless of composite score:

| Gate | Threshold |
|------|-----------|
| `profit_factor` | ≥ 1.20 |
| `max_drawdown` | ≤ 25% |
| `trade_count` | ≥ 30 |
| `mc_survival_rate` | ≥ 85% (if provided) |

Thresholds are defined in `weights.THRESHOLDS` and never hard-coded in logic.

---

## Usage

```python
import sqlite3
from research.scoring.scoring import ScoringInput, score, save_scoring_result

inp = ScoringInput(
    spec_id=1,
    backtest={
        "profit_factor": 1.85,
        "expectancy": 95.0,
        "win_rate": 0.62,
        "max_drawdown": -0.14,
        "trades": 47,
        "rules_documented": True,
    },
    mc={"pass_status": True, "probability_of_loss": 0.18},
    wf={"overall_pass": True, "median_degradation": 0.83},
)

result = score(inp)
print(result.grade, result.composite_score, result.recommendation)

conn = sqlite3.connect("database/hermes_research.db")
scoring_id = save_scoring_result(conn, result)
```

---

## Safety Boundaries

- Read-only access to `strategy_specs` (not written here)
- `save_scoring_result()` inserts into `scoring_results` — no schema changes
- No live trading, no broker connection, no order placement
- All inputs are plain Python dicts — no external dependencies
