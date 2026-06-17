# research/certification -- NT8 Export Certification

**Phase 36**

**Goal:** Certify that Hermes correctly ingests real NinjaTrader Strategy Analyzer exports.

## Scope

    Validation only. Local files only. Local DB only.
    No live trading. No broker connection. No order placement. No ATM control.
    No automatic approval. REVIEW_REQUIRED remains the terminal pipeline state.

## Checks

| # | Check | Mode | What it verifies |
|---|-------|------|-----------------|
| 1 | Probe summary file | dry-run + full | File readable, columns detected, verdict |
| 2 | Probe trade list file | dry-run + full | File readable, valid rows, verdict |
| 3 | Confirm required columns mapped | dry-run + full | All REQUIRED_*_COLS present |
| 4 | Trade count matches CSV | dry-run + full | valid_rows == trade_list_json_count |
| 5 | trade_list_json count matches | full only | DB json_array_length == probe count |
| 6 | equity_curve_json length matches | full only | DB json_array_length == probe count |
| 7 | Backtest row created or deduped | full only | Backtest row exists for spec_id |
| 8 | Score can be generated | full only | run_for_spec completes without error |
| 9 | Audit can run | full only | audit_spec completes without error |
| 10 | REVIEW_REQUIRED unchanged | full only | No state advanced beyond REVIEW_REQUIRED |

Checks 1-4 are file-only. No DB connection required. Safe to run before any import.

Checks 5-10 require `--spec-id` and a database connection. They perform the actual
import, score, and audit as part of certification.

## Check verdicts

| Verdict | Meaning |
|---------|---------|
| PASS | Check passed cleanly |
| WARN | Check passed with caveats (review before treating as production-ready) |
| FAIL | Check failed -- resolve before using this export in the pipeline |
| SKIP | Check not applicable in this mode (dry-run) |

## Overall verdict

| Overall | Meaning |
|---------|---------|
| PASS | All non-skipped checks passed |
| WARN | At least one WARN, no FAIL |
| FAIL | At least one FAIL |

## Usage

```bash
# Dry-run: probe sample files, no DB writes (checks 1-4)
python -m research.certification.nt8_export_certifier --dry-run

# Dry-run with specific files (checks 1-4)
python -m research.certification.nt8_export_certifier --dry-run \
    --summary path/to/summary.csv \
    --trade-list path/to/trades.csv

# Full certification: all 10 checks, imports to DB, scores, audits
python -m research.certification.nt8_export_certifier \
    --summary path/to/summary.csv \
    --trade-list path/to/trades.csv \
    --spec-id 3

# Full certification with initial capital for equity curve
python -m research.certification.nt8_export_certifier \
    --summary path/to/summary.csv \
    --trade-list path/to/trades.csv \
    --spec-id 3 \
    --initial-capital 50000
```

## Output

Reports are written to `reports/certification/` (gitignored):

```
reports/certification/nt8_export_certification_<spec_name>_<date>.md
reports/certification/nt8_export_certification_<spec_name>_<date>.json
```

Each report includes:
- Pass/Warn/Fail verdict per check with detail
- Summary of failures and warnings
- Overall certification result
- Confirmation that REVIEW_REQUIRED remains terminal

## What it does NOT do

- Does not approve or reject strategies
- Does not change strategy state
- Does not advance any strategy past REVIEW_REQUIRED
- Does not connect to any live broker
- Does not place any orders

## Pipeline position

```
NT8 Strategy Analyzer
  -> Export files (CSV)
    -> [NT8 Export Certifier] (Phase 36)
       -- validates ingestion before trusting the data
       -> backtest_ingestor (existing)
         -> scoring (existing)
           -> audit (existing)
             -> research pipeline
               -> REVIEW_REQUIRED
                 -> Human Decision
```

## Relationship to the import pipeline

The certifier wraps and validates the existing import pipeline:

```
connectors/ninjatrader/backtest_ingestor.py   -- probe_summary, probe_trade_list
connectors/ninjatrader/nt8_import_pipeline.py -- step_import
research/scoring/runner.py                    -- run_for_spec
research/audit/strategy_auditor.py            -- audit_spec
research/lifecycle/lifecycle.py               -- infer_lifecycle_state
```

The certifier does not replace the import pipeline. It adds a validation
layer that confirms each import step produced correct results.

Validation only. No live trading. Human authority unchanged.
