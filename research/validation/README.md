# research/validation -- Robustness Validation Engines

Read-only validation tools that test whether a strategy's results hold up
under stress. These run after scoring and before human review.

```
Score -> Audit -> Validation -> REVIEW_REQUIRED -> Human Review
```

---

## Engines

| File | Purpose |
|------|---------|
| `monte_carlo.py` | Bootstrap resample trades N times; measure survival rate |

---

## Monte Carlo (`monte_carlo.py`)

**Question:** "How likely is this strategy to survive a different sequence
of wins and losses?"

**Method:**
1. Take the P&L list from the latest in-sample backtest
2. Bootstrap resample (sample N trades with replacement) -- 1000 simulations
3. For each simulation, replay the equity curve and measure max drawdown
4. Survival rate = % of simulations where max drawdown < ruin threshold
5. Write `monte_carlo_score` and `monte_carlo_pass` to `scoring_results`

**Bootstrap vs shuffle:** Sampling with replacement means each simulation
draws a different subset of trades, so total P&L and drawdown vary
across simulations. This makes `probability_positive` and drawdown
distribution metrics meaningful.

**No schema changes.** Updates two existing null fields in the latest
`scoring_results` row.

### Requirements

- In-sample backtest with `trade_list_json` populated
- `initial_capital` recorded on the backtest row (re-import with
  `--initial-capital` if missing)
- At least one `scoring_results` row for the spec

### Usage

```powershell
# Run one spec
python -m research.validation.monte_carlo --spec-id N

# Run all specs
python -m research.validation.monte_carlo --all

# Dry-run (compute, no DB write)
python -m research.validation.monte_carlo --spec-id N --dry-run

# Custom parameters
python -m research.validation.monte_carlo --spec-id N --simulations 2000 --seed 42
```

### Defaults

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `--simulations` | 1000 | Number of bootstrap runs |
| Ruin threshold | `initial_capital * 5%` | Prop-firm standard (derived, not a CLI arg) |
| Survival gate | 85% | Min survival rate for PASS |
| Warning gate | 70% | Min survival rate before FAIL |

### Output

Console: survival rate, probability positive, drawdown distribution, pass/fail verdict.

Reports written to `reports/validation/` (gitignored):
```
reports/validation/
  MNQ_ORB_FVG_v001_monte_carlo_20260613.md
  MNQ_ORB_FVG_v001_monte_carlo_20260613.json
```

Filename format: `{spec_name}_monte_carlo_{YYYYMMDD}.md`

### Metrics

| Metric | Description |
|--------|-------------|
| `survival_rate` | % of sims where max drawdown < ruin threshold |
| `probability_positive` | % of sims where final equity > initial capital |
| `worst_drawdown` | Largest max-DD fraction seen across all simulations |
| `p95_drawdown` | 95th-percentile max-DD fraction |
| `median_drawdown` | Median max-DD fraction |
| `monte_carlo_score` | == survival_rate (0.0-1.0) |
| `monte_carlo_pass` | True when survival_rate >= 0.85 |

### Survival tiers

| Tier | Survival rate |
|------|--------------|
| PASS | >= 85% |
| WARNING | 70-84% |
| FAIL | < 70% |

### What it does NOT do

- Does not alter trade P&L values
- Does not simulate slippage or commission variation
- Does not replace walk-forward testing
- Does not promote a strategy past `REVIEW_REQUIRED`

### Auditor integration

After Monte Carlo runs, the auditor's `[!] Monte Carlo validation null`
check resolves to:
- `[+] PASS` — score >= 85%
- `[!] WARN` — score 70-84%
- `[X] FAIL` — score < 70%

`walk_forward_score` remains null until a future walk-forward phase.

---

## Placement in the Pipeline

```
Spec Import
  -> Backtest Import (--oos for OOS runs)
    -> Score
      -> Audit
        -> Monte Carlo  <-- here
          -> REVIEW_REQUIRED
            -> Human Review
```
