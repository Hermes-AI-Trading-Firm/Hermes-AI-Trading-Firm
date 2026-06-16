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
| `walk_forward.py` | Compare IS vs OOS backtests; measure performance retention |

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

## Walk-Forward (`walk_forward.py`)

**Question:** "Did the strategy perform reasonably out-of-sample compared
to in-sample?"

**Method:**
1. Load latest IS backtest and latest OOS backtest for the spec
2. Compute three retention/penalty components
3. Weight into `walk_forward_score` (0.0–1.0)
4. Write `walk_forward_score` and `walk_forward_pass` to `scoring_results`

**Components:**

| Component | Formula | Weight |
|-----------|---------|--------|
| PF retention | `clamp(OOS PF / IS PF, 0, 1)` | 40% |
| Expectancy retention | `clamp(OOS exp/trade / IS exp/trade, 0, 1)` | 40% |
| Drawdown component | `clamp(IS DD / OOS DD, 0, 1)` if OOS DD > IS DD, else 1.0 | 20% |

**Tiers:**

| Tier | Threshold |
|------|-----------|
| PASS | >= 70% |
| WARNING | 50–69% |
| FAIL | < 50% |

**NOT_RUN:** If no OOS backtest exists for the spec, the engine skips it
without touching `scoring_results`.

**No schema changes.** Updates two existing null fields in the latest
`scoring_results` row.

### Requirements

- In-sample backtest in `backtests` (`is_in_sample=1`)
- Out-of-sample backtest in `backtests` (`is_in_sample=0`)
- At least one `scoring_results` row for the spec
- Import OOS with: `python -m connectors.ninjatrader.nt8_import_pipeline --oos ...`

### Usage

```powershell
# Run one spec
python -m research.validation.walk_forward --spec-id N

# Run all specs (NOT_RUN shown for specs without OOS)
python -m research.validation.walk_forward --all

# Dry-run (compute, no DB write)
python -m research.validation.walk_forward --spec-id N --dry-run
```

### Output

Console: IS vs OOS metric table, component breakdown, pass/fail verdict.

Reports written to `reports/validation/` (gitignored):
```
reports/validation/
  ES_VWAP_REVERSION_v001_walk_forward_20260615.md
  ES_VWAP_REVERSION_v001_walk_forward_20260615.json
```

Filename format: `{spec_name}_walk_forward_{YYYYMMDD}.md`

### Auditor integration

After walk-forward runs, the auditor's `[!] Walk-forward validation null`
check resolves to PASS / WARN / FAIL based on the three tiers above.

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
