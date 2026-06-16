# research/learning — AI Learning Brain

Advisory-only read-only pattern review layer. Reads all completed research
evidence and generates improvement suggestions. Nothing here writes to a
database, modifies a strategy, or advances any strategy past REVIEW_REQUIRED.

## What it does

1. **Collects evidence** — DB rows (spec, backtest, scoring) plus all JSON
   report files (audit, walk-forward, Monte Carlo, regime, decision package).

2. **Identifies failure patterns** — signals that warrant concern, with
   severity HIGH / MEDIUM / LOW and category tags.

3. **Identifies strength patterns** — positive signals that support confidence
   in the research evidence so far.

4. **Suggests next research actions** — prioritised, actionable steps with
   commands where applicable.

5. **Writes reports** — `reports/learning/{strategy}_learning_review_{date}.md`
   and a matching `.json`.

## What it does NOT do

- Does not write to any database table
- Does not modify strategy specs or scores
- Does not approve or reject strategies
- Does not create or cancel orders
- Does not connect to any broker
- Does not start live trading
- Does not move strategies past REVIEW_REQUIRED

Every suggestion is advisory. Human approval is required for all decisions
that advance a strategy beyond REVIEW_REQUIRED.

## Failure pattern categories

| Category   | Examples                                                       |
|------------|----------------------------------------------------------------|
| Data       | No trade_list_json, insufficient trades, audit FAILs           |
| Overfit    | PF or Sharpe elevated on small sample                          |
| OOS        | Missing OOS, walk-forward FAIL or WARN, poor PF retention      |
| Robustness | Monte Carlo FAIL or WARN, low probability positive             |
| Compliance | Drawdown exceeds prop-firm limit or severe                     |
| Regime     | No regime analysis, single-window regime data                  |

## Action types

| action_type                       | Meaning                                      |
|-----------------------------------|----------------------------------------------|
| collect_more_trades               | Extend backtest or import new NT8 export     |
| run_oos_test                      | Import OOS and/or run walk-forward           |
| run_monte_carlo                   | Run bootstrap MC validation                  |
| run_wider_parameter_sweep         | Research-only sweep to check metric stability|
| add_filter_for_research           | Explore stop/filter changes in backtest only |
| test_regime_specific_variation    | Research-only regime-filtered variant        |
| reject_candidate                  | Recommend rejection and archival             |
| prepare_for_human_review          | Generate decision package for human reviewer |

## Usage

```bash
# Single strategy
python -m research.learning.learning_brain --spec-id 6

# All scored strategies
python -m research.learning.learning_brain --all

# Dry-run (console only, no files written)
python -m research.learning.learning_brain --spec-id 6 --dry-run
python -m research.learning.learning_brain --all --dry-run

# Custom DB or reports path
python -m research.learning.learning_brain --spec-id 6 --db path/to/db --reports-dir path/to/dir
```

## Output files

```
reports/learning/
  {strategy}_learning_review_{YYYYMMDD}.md
  {strategy}_learning_review_{YYYYMMDD}.json
```

Files in `reports/learning/` are gitignored and are never committed.

## Pipeline position

```
Spec -> Backtest -> Scoring -> Audit -> Walk-Forward -> Monte Carlo
-> Regime -> Decision Package -> [AI Learning Brain] -> Human Reviewer
```

The learning brain reads all upstream outputs but writes only to
`reports/learning/`. It has no upstream effects.
