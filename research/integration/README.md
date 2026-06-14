# research/integration — Full Pipeline Integration Test

Runs an end-to-end test of the Hermes research pipeline using local sample
files only. Verifies each stage individually, reports PASS/FAIL/SKIP/WARN,
and asserts the human approval gate was not breached.

No live trading. No broker connection. No order placement. No strategy promotion.

---

## Files

| File | Purpose |
|------|---------|
| `full_pipeline_test.py` | Main integration test runner (9 stages) |
| `test_spec.yaml` | Dedicated test fixture spec (`HERMES_INTEGRATION_TEST_v001`) |

---

## Stages

| # | Stage | What it tests |
|---|-------|--------------|
| 1 | Spec Import | Imports `test_spec.yaml` into `strategy_specs` |
| 2 | Backtest Import | Imports sample NT8 summary + trade list against test spec |
| 3 | Score from Backtest | Scores the spec; writes to `scoring_results` |
| 4 | Research Rankings | Verifies spec appears once in `/research-rankings` |
| 5 | Performance Analytics | Verifies `/equity-curve` and `/performance-summary` respond |
| 6 | Compliance Status | Verifies `/compliance-status` responds |
| 7 | Strategy Report | Generates Markdown + JSON report; records output paths |
| 8 | Decision Queue | Verifies spec appears in queue with `REVIEW_REQUIRED` |
| 9 | Human Approval Gate | Asserts spec was NOT promoted to approved/rejected |

---

## Usage

### Dry-run (validate without writing)
```powershell
python -m research.integration.full_pipeline_test --dry-run
```

### Full local test
```powershell
python -m research.integration.full_pipeline_test --run-label phase18_sample
```

### With custom database
```powershell
python -m research.integration.full_pipeline_test --db database/hermes_research.db
```

---

## Stage Status Codes

| Symbol | Code | Meaning |
|--------|------|---------|
| ✓ | PASS | Stage completed successfully |
| ✗ | FAIL | Stage encountered an error — test fails overall |
| – | SKIP | Stage skipped (dry-run mode or missing prerequisite) |
| ! | WARN | Stage completed with non-fatal warnings |

Exit code 0 = all stages PASS or SKIP. Exit code 1 = any FAIL.

---

## Sample Files Used

| Stage | Sample file |
|-------|------------|
| 1 | `research/integration/test_spec.yaml` |
| 2 | `connectors/ninjatrader/sample_nt8_backtest_summary.csv` |
| 2 | `connectors/ninjatrader/sample_nt8_trade_list.csv` |

---

## Human Approval Gate

Stage 9 (`Human Approval Gate`) asserts that:
- `strategy_specs.status` is **not** `approved` or `rejected`
- The spec does **not** appear in `approved_strategies`

This assertion runs regardless of dry-run mode. A failure here means the
pipeline promoted a strategy without human sign-off — a critical error.

---

## Re-running

The test is safe to re-run. On a second run:
- Stage 1: spec is found (skipped with warning)
- Stage 2: backtest is found (skipped with warning)
- Stage 3: new score is appended to `scoring_results` (append-only history)
- Stages 4–9: read-only, run fresh each time
