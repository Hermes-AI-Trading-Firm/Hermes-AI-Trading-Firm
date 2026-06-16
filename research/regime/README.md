# research/regime -- Regime Analysis Engine

Answers: "How does this strategy perform across different market regimes
or time windows?"

```
Backtest Import -> Score -> Audit -> Validation -> Regime Analysis -> REVIEW_REQUIRED
```

No DB writes. No schema changes. No live trading. Report-only engine.

---

## Two Modes

### 1. Internal time-window analysis (default)

Group trades from `trade_list_json` by calendar month or quarter.
Label each window **Strong / Neutral / Weak** based on PF and expectancy.

```powershell
# Monthly (default)
python -m research.regime.regime_analyzer --spec-id N

# Quarterly
python -m research.regime.regime_analyzer --spec-id N --window quarterly
```

### 2. Label file mode

User supplies a CSV that maps date ranges to regime names.
Engine groups trades by regime and computes per-regime performance.

```powershell
python -m research.regime.regime_analyzer --spec-id N \
    --label-file research/regime/sample_regime_labels.csv
```

---

## Label File Format

```csv
start_date,end_date,regime_label
2026-01-01,2026-03-31,Bull
2026-04-01,2026-06-30,Sideways
2026-07-01,2026-09-30,Bear
```

- Dates are inclusive on both ends
- Trades not matched by any range are counted as "unmatched" and excluded
- Labels can be any string (Bull, Bear, Sideways, High-Vol, Low-Vol, etc.)
- A sample file is at `research/regime/sample_regime_labels.csv`

---

## Window Labels (Internal Mode)

| Label | Rule |
|-------|------|
| Strong | PF >= 1.5 AND exp/trade > 0 AND win rate >= 55% |
| Neutral | PF >= 1.0 AND exp/trade > 0 |
| Weak | anything else |

---

## Metrics Per Window

| Metric | Description |
|--------|-------------|
| `trade_count` | Number of trades in this window |
| `win_rate` | Winning trades / total trades |
| `net_pnl` | Sum of P&L for all trades in window |
| `profit_factor` | Gross profit / gross loss (None = all wins) |
| `expectancy_per_trade` | net_pnl / trade_count |
| `max_drawdown_dollars` | Peak-to-trough equity decline within window |

---

## Requirements

- IS backtest with `trade_list_json` populated (must include `entry_time` field)
- Re-import via NT8 pipeline if `trade_list_json` is missing

---

## Usage

```powershell
# All specs, monthly
python -m research.regime.regime_analyzer --all

# One spec, quarterly
python -m research.regime.regime_analyzer --spec-id 3 --window quarterly

# One spec with user regime labels
python -m research.regime.regime_analyzer --spec-id 3 \
    --label-file research/regime/sample_regime_labels.csv

# Dry-run (no files written)
python -m research.regime.regime_analyzer --spec-id 3 --dry-run
```

---

## Output

Reports written to `reports/regime/` (gitignored):

```
reports/regime/
  MNQ_ORB_FVG_v001_regime_analysis_20260615.md
  MNQ_ORB_FVG_v001_regime_analysis_20260615.json
```

Filename format: `{spec_name}_regime_analysis_{YYYYMMDD}.md`

---

## What It Does NOT Do

- Does not write to `scoring_results` or any DB table
- Does not use external price data
- Does not classify regimes using technical indicators
- Does not promote a strategy past `REVIEW_REQUIRED`
- Does not connect to any broker or live data feed

---

## Placement in the Pipeline

```
Spec Import
  -> Backtest Import (IS + OOS)
    -> Score
      -> Audit
        -> Monte Carlo
          -> Walk-Forward
            -> Regime Analysis  <-- here
              -> REVIEW_REQUIRED
                -> Human Review
```
