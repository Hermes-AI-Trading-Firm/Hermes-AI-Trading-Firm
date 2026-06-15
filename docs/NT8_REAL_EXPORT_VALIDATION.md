# NT8 Real Export Validation Guide

How to validate a real NinjaTrader 8 Strategy Analyzer CSV export against the
Hermes ingestor before running a live import.

No live trading. No broker connection. No order placement.

---

## Why This Matters

The ingestor was originally developed against hand-crafted sample CSVs.
Real NT8 exports may differ in:

- Column names (NT8 version differences, locale settings)
- Number formatting (locale-specific decimal separators, currency symbols)
- Date/time formats (12h vs 24h, slash vs dash separators)
- Encoding (BOM presence, UTF-8 vs ANSI)
- Extra or missing columns depending on strategy type

Run `--probe` on any new NT8 export before running `--dry-run` or a live import.

---

## Step 1: Export from NinjaTrader Strategy Analyzer

### Performance Summary CSV

1. Open NinjaTrader 8 > Strategy Analyzer
2. Run a backtest to completion
3. Click the **Performance** tab
4. Click **Export** (or right-click > Export to CSV)
5. Save as `<strategy_name>_summary.csv`

Expected columns (minimum required):

| Column | Description |
|--------|-------------|
| Strategy | Strategy name |
| Instrument | Ticker symbol |
| Start Date | Backtest start date |
| End Date | Backtest end date |
| Net Profit | Total net P&L |
| Profit Factor | Gross profit / gross loss |
| # of Trades | Total trade count |

Also expected (optional but scored):

| Column | DB column |
|--------|-----------|
| Max. Drawdown % | max_drawdown_pct |
| Sharpe Ratio | sharpe_ratio |
| Sortino Ratio | sortino_ratio |
| % Profitable | win_rate |
| Avg. Trade | expectancy_per_trade |
| Avg. Win | average_win |
| Avg. Loss | average_loss |
| Max. Consec. Winners | max_consecutive_wins |
| Max. Consec. Losers | max_consecutive_losses |

### Trade List CSV

1. Click the **Trades** tab in Strategy Analyzer
2. Click **Export** (or right-click > Export to CSV)
3. Save as `<strategy_name>_trades.csv`

Expected columns (minimum required):

| Column | Description |
|--------|-------------|
| Instrument | Ticker |
| Market pos. | "Long" or "Short" |
| Quantity | Contract/share count |
| Entry time | Trade entry timestamp |
| Exit time | Trade exit timestamp |
| Entry price | Entry fill price |
| Exit price | Exit fill price |
| Profit | Per-trade P&L |

Also expected (optional):

| Column | Purpose |
|--------|---------|
| Cum. profit | Used to build equity curve (preferred over accumulating Profit) |
| Commission | Per-trade commission |
| Slippage | Per-trade slippage |
| Trade # | Row identifier |
| MAE | Maximum adverse excursion |
| MFE | Maximum favorable excursion |
| ETD | End trade drawdown |

---

## Step 2: Run --probe

Probe inspects the file without touching the database or requiring a spec-id.

```powershell
# Probe the summary only
python connectors/ninjatrader/backtest_ingestor.py --probe `
    --summary path/to/your_strategy_summary.csv

# Probe the trade list only
python connectors/ninjatrader/backtest_ingestor.py --probe `
    --trade-list path/to/your_strategy_trades.csv

# Probe both at once
python connectors/ninjatrader/backtest_ingestor.py --probe `
    --summary path/to/your_strategy_summary.csv `
    --trade-list path/to/your_strategy_trades.csv

# Probe with log output (logs written only if --log-dir is provided)
python connectors/ninjatrader/backtest_ingestor.py --probe `
    --summary path/to/your_strategy_summary.csv `
    --log-dir logs/nt8_probe
```

### Reading probe output

**Required columns table** — every column listed as `MISSING` will cause the
import to fail. The column must exist in the CSV with that exact name.

**Mapped columns table** — shows each NT8 column that was found, the DB column
it maps to, and the parsed value from the first row. Check that parsed values
are non-null and look reasonable.

**Unmapped columns** — columns in the file that the ingestor does not use. These
are informational only; they do not cause failures.

**Parse warnings** — values that could not be converted to numbers. Common causes:

| Symptom | Likely cause |
|---------|-------------|
| `(null)` for a numeric field | Locale uses `,` as decimal separator |
| `(null)` for `# of Trades` | Column named differently in your NT8 version |
| `(null)` for `Max. Drawdown %` | Column may be named `Max Drawdown %` (no period) |

**Verdict**:

| Verdict | Meaning |
|---------|---------|
| `READY` | All required columns present, key metrics parsed OK |
| `WARN` | Required columns present but parse issues found |
| `FAIL` | Missing required columns or no valid trade rows |

---

## Step 3: Fix Column Mismatches (if any)

Common NT8 column name variations and how to handle them:

| NT8 may export | Ingestor expects | Fix |
|----------------|------------------|-----|
| `Max Drawdown %` | `Max. Drawdown %` | Edit CSV header |
| `Profit Factor` | `Profit Factor` | OK |
| `# Trades` | `# of Trades` | Edit CSV header |
| `Win %` | `% Profitable` | Edit CSV header |

If your NT8 locale uses comma as decimal separator (e.g. `1.234,56`), the
ingestor's `_clean_num` function may not parse these. In that case:
1. Change NT8's number format setting to period as decimal separator, OR
2. Pre-process the CSV to replace `,` decimal separators with `.`

---

## Step 4: Run --validate-only

After probe passes, run validate-only to confirm the spec exists in the DB:

```powershell
python connectors/ninjatrader/backtest_ingestor.py --validate-only `
    --summary path/to/your_strategy_summary.csv `
    --trade-list path/to/your_strategy_trades.csv `
    --spec-id 3
```

This runs probe on all files AND checks that `spec_id=3` exists in
`strategy_specs`. No DB writes. Exit 0 = ready to import.

---

## Step 5: Run --dry-run

Full parse with spec context, no writes:

```powershell
python connectors/ninjatrader/backtest_ingestor.py --dry-run `
    --summary path/to/your_strategy_summary.csv `
    --trade-list path/to/your_strategy_trades.csv `
    --spec-id 3 `
    --initial-capital 50000
```

---

## Step 6: Live Import

Once dry-run passes with no errors:

```powershell
python connectors/ninjatrader/backtest_ingestor.py `
    --summary path/to/your_strategy_summary.csv `
    --trade-list path/to/your_strategy_trades.csv `
    --spec-id 3 `
    --initial-capital 50000
```

Then score:

```powershell
python research/scoring/score_from_backtests.py --spec-id 3
```

---

## Validation Checklist

Before running a live import on a new NT8 export:

- [ ] `--probe --summary` verdict is `READY`
- [ ] `--probe --trade-list` verdict is `READY`
- [ ] Valid trade rows = total rows (no skipped rows)
- [ ] `trade_list_json count` matches expected trade count
- [ ] `equity_curve_json count` > 0
- [ ] Key metrics parsed (profit_factor, max_drawdown_pct, win_rate, total_trades)
- [ ] `--validate-only` spec check passes
- [ ] `--dry-run` completes with no errors
- [ ] File is NOT a sample file (no "[WARN] Sample file" in probe output)

---

## NT8 Version Notes

| NT8 version | Known differences |
|-------------|-------------------|
| 8.0.x | `Max. Drawdown %` includes the period after `Max` |
| 8.1.x | Same column names as 8.0.x (confirmed) |

If you discover a column name difference in a newer NT8 version, update
`_SUMMARY_MAP` or `REQUIRED_SUMMARY_COLS` in
`connectors/ninjatrader/backtest_ingestor.py` and document the version here.

---

## File Placement

Place real NT8 exports in a directory outside the sample files:

```
connectors/ninjatrader/
  real_exports/
    ES_VWAP_REVERSION_v001_summary.csv
    ES_VWAP_REVERSION_v001_trades.csv
```

`connectors/ninjatrader/real_exports/` is gitignored — real backtest data
should not be committed to the repository.
