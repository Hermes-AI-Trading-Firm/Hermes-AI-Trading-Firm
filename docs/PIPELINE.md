# Hermes Research Pipeline — Official Operating Model

This document is the canonical reference for the Hermes AI Trading Firm
research pipeline. All future development, operations, and review decisions
must align with this model.

---

## Pipeline Overview

```
Strategy Spec Import
    |
    v
NT8 Backtest Import
    |
    +-- Probe (column mapping, data validation)
    +-- Validate (spec exists in DB)
    +-- Import (summary + trade list)
    |
    v
Score
    |
    v
Rank
    |
    v
Report
    |
    v
Audit
    |
    v
Decision Queue
    |
    v
REVIEW_REQUIRED  <-- maximum automated state
    |
    v
Human Review     <-- only a human can advance beyond this point
    |
    +-- Approve  --> research/approved/
    +-- Reject   --> research/rejected/
    +-- Return for more research
```

No automated process can move a strategy past `REVIEW_REQUIRED`.
Human sign-off is mandatory at every promotion decision.

---

## Permanent Constraints

These constraints are non-negotiable and apply to every session, every
agent, every future phase of development.

| Constraint | Status |
|------------|--------|
| Read-only research pipeline | ENFORCED |
| Human approval required before any promotion | ENFORCED |
| No live trading | PROHIBITED |
| No broker connection | PROHIBITED |
| No order placement | PROHIBITED |
| No ATM control | PROHIBITED |
| No automatic strategy promotion | PROHIBITED |
| No forward testing without human approval | PROHIBITED |
| File import only (no NT8 bridge) | ENFORCED |
| Local database only | ENFORCED |

Enforcement layers:
- `spec_importer.py` blocks `approved`/`rejected` status on file import
- `decision_queue()` hard-codes `status=REVIEW_REQUIRED` for all scored strategies
- `assert_no_promotion()` in `research/integration/full_pipeline_test.py` catches any breach at test time
- `CLAUDE.md` governs all agent behavior across sessions

---

## Terminal State

```
REVIEW_REQUIRED
```

This is the highest state any strategy can reach through automated pipeline
execution. It means:

- The strategy has been imported, scored, ranked, reported, and audited.
- The pipeline has done everything it can.
- A human must now read the audit report and make a decision.

No code in this repository sets `approved` on a strategy. That action is
manual and deliberate.

---

## Stage Reference

---

### Stage 1 — Strategy Spec Import

**Purpose:** Register a strategy specification in the database so the rest
of the pipeline has a target to work against.

**Inputs:**
- YAML, JSON, or Markdown frontmatter spec file
- Required fields: `spec_name`, `instrument`, `timeframe`, `strategy_type`,
  `description`, `entry_rules`, `exit_rules`, `risk_rules`

**Outputs:**
- Row in `strategy_specs`
- Linked row in `strategy_ideas` (created automatically if absent)
- `spec_id` used by all downstream stages

**Modules:**
- `connectors/strategy_specs/spec_importer.py`
- `connectors/strategy_specs/sample_strategy_spec.yaml`
- `connectors/strategy_specs/sample_strategy_spec.json`

**Safety gates:**
- `approved` and `rejected` cannot be set via import — blocked and replaced with `draft`
- Duplicate `spec_name` is detected; `--update-existing` required to overwrite

**Failure conditions:**
- Missing required fields → import rejected with field list
- File not found or unrecognised format → error, no DB write
- Duplicate without `--update-existing` → silently skipped

**Command:**
```powershell
python connectors/strategy_specs/spec_importer.py `
    --file path/to/strategy_spec.yaml
```

---

### Stage 2 — NT8 Backtest Import

**Purpose:** Import NinjaTrader 8 Strategy Analyzer export files and attach
backtest metrics, trade list, and equity curve to the strategy spec.

This stage has three sub-steps:

#### 2a. Probe

Inspect the CSV files without touching the database. No spec-id required.

**Inputs:** NT8 Performance Summary CSV and/or Trade List CSV

**Outputs:** Column mapping report, parse results, verdict (READY/WARN/FAIL)

**Module:** `connectors/ninjatrader/backtest_ingestor.py --probe`

**Failure conditions:**
- Missing required columns → FAIL verdict, import blocked
- Unparseable numeric fields → WARN with per-field detail
- Sample file detected → WARN (replace with real NT8 export)

#### 2b. Validate

Verify the target spec exists in the database before any write.

**Inputs:** `spec_id`

**Outputs:** Spec name and status confirmation

**Module:** `connectors/ninjatrader/nt8_import_pipeline.py --dry-run`

**Failure conditions:**
- spec_id not found → pipeline halts, no import

#### 2c. Import

Write the backtest row, trade list JSON, and equity curve JSON to the database.

**Inputs:**
- NT8 Performance Summary CSV (aggregate metrics)
- NT8 Trade List CSV (per-trade records)
- `spec_id`, `initial_capital`

**Outputs:**
- Row in `backtests` (`backtest_id`)
- `trade_list_json` — serialised trade records
- `equity_curve_json` — equity progression from `Cum. profit`

**Module:** `connectors/ninjatrader/backtest_ingestor.py`

**Safety gates:**
- Deduplication index on `(spec_id, data_start_date, data_end_date)` — duplicate import silently skipped
- Fallback to existing `backtest_id` on duplicate, so downstream stages still run

**Failure conditions:**
- Duplicate without matching dedup key → new row inserted
- Non-numeric price/quantity/profit on a trade row → trade skipped with warning
- Unknown `Market pos.` value → trade skipped with warning

**Recommended command (all steps):**
```powershell
python connectors/ninjatrader/nt8_import_pipeline.py `
    --summary path/to/performance_summary.csv `
    --trade-list path/to/trade_list.csv `
    --spec-id N `
    --initial-capital 50000 `
    --run-label strategy_name_v001
```

**Probe-only (no DB):**
```powershell
python connectors/ninjatrader/nt8_import_pipeline.py --probe-only `
    --summary path/to/performance_summary.csv `
    --trade-list path/to/trade_list.csv
```

**Dry-run (probe + validate, no writes):**
```powershell
python connectors/ninjatrader/nt8_import_pipeline.py --dry-run `
    --summary path/to/performance_summary.csv `
    --trade-list path/to/trade_list.csv `
    --spec-id N
```

---

### Stage 3 — Score

**Purpose:** Convert imported backtest metrics into a composite score,
grade, and pipeline recommendation.

**Inputs:** Latest `backtests` row for the spec

**Outputs:**
- Row in `scoring_results` (`scoring_id`)
- `composite_score` (0–100), `grade` (A+ / A / B / C / D / Reject)
- `recommendation` (Live Candidate / Forward Test / Optimize / Retest / Reject)
- Component scores: profitability, drawdown, consistency, walk-forward,
  Monte Carlo, regime, robustness, prop-firm
- `overfitting_risk`, `gate_failures`

**Modules:**
- `research/scoring/scoring.py` — core engine
- `research/scoring/runner.py` — batch runner, `run_for_spec()`
- `research/scoring/score_from_backtests.py` — CLI
- `research/scoring/weights.py` — all thresholds and grade bands

**Hard gates (any failure forces grade → Reject):**
- Profit factor < 1.20
- Max drawdown > 25%
- Total trades < 30
- Monte Carlo survival < 85%

**Grade bands:**
- A+ ≥ 90, A ≥ 80, B ≥ 70, C ≥ 60, D ≥ 0

**Scoring is append-only:** every run adds a new row to `scoring_results`.
Rankings always use the latest row per spec. History is never deleted.

**Commands:**
```powershell
# Score one spec
python research/scoring/score_from_backtests.py --spec-id N

# Score all specs with backtest data
python research/scoring/score_from_backtests.py --all
```

---

### Stage 4 — Rank

**Purpose:** Order all scored strategies by composite score for review.

**Inputs:** `scoring_results` (latest row per spec, deduplicated)

**Outputs:** Ranked list with score, grade, recommendation, and backtest metrics

**Module:** `api/queries.py → research_rankings()`

**Deduplication:** `WHERE scoring_id = (SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = sr.spec_id)`
— ensures only the latest score per strategy appears in rankings. Full
history is preserved in the table.

**No command required** — rankings are surfaced through the API server
(`GET /research-rankings`) or read directly via `latest_scoring_results()`.

---

### Stage 5 — Report

**Purpose:** Generate a human-readable strategy report combining spec
metadata, backtest metrics, and scoring results.

**Inputs:** `strategy_specs`, `backtests`, `scoring_results` for a spec

**Outputs:**
- `reports/strategies/<spec_name>_<date>.md`
- `reports/strategies/<spec_name>_<date>.json`

**Module:** `research/reporting/` (report generator)

**Note:** `reports/strategies/` is gitignored — generated output is not
committed to the repository.

---

### Stage 6 — Audit

**Purpose:** Pre-approval checklist that helps the human reviewer decide
whether to trust the scoring results. Answers: *how much should I trust
these numbers?*

The auditor does not replace scoring — it interrogates it.

**Inputs:** `strategy_specs`, `backtests`, `scoring_results` (read-only)

**Outputs:**
- `reports/audits/<spec_name>_<date>_audit.md`
- `reports/audits/<spec_name>_<date>_audit.json`
- Console report: PASS/WARN/FAIL per check with detail

**Module:** `research/audit/strategy_auditor.py`

**Checklist categories:**
1. Data Completeness — spec, backtest, trade list, equity curve, score
2. Sample Size — FAIL < 30 trades, WARN < 100 trades, PASS >= 100
3. Backtest Quality — date range, performance summary, initial capital
4. Overfit Risk — PF/Sharpe/win-rate vs trade count; scoring engine risk flag
5. Out-of-Sample Readiness — OOS backtest, walk-forward, Monte Carlo
6. Prop-Firm Readiness — drawdown vs 5% limit, prop_firm_supported flag

**Audit recommendations:**
- `NEEDS_REAL_NT8_EXPORT` — no trade list (sample or summary-only)
- `NEEDS_MORE_TRADES` — fewer than 30 trades
- `NEEDS_WALK_FORWARD` — no OOS backtest or WF/MC missing
- `READY_FOR_HUMAN_REVIEW` — no FAILs, ready for human decision
- `REJECT_RESEARCH_CANDIDATE` — multiple critical FAILs

**Safety:** DB opened read-only (`mode=ro`). No writes possible.

**Key distinction:**
```
Score  = "How good do the results look?"
Audit  = "How much should I trust those results?"
```

A strategy can score A+ on thin, synthetic data. The audit surfaces that.
The score is correct; the trust is not.

**Commands:**
```powershell
# Audit one strategy
python -m research.audit.strategy_auditor --spec-id N

# Audit all strategies
python -m research.audit.strategy_auditor --all

# Dry-run (no files written)
python -m research.audit.strategy_auditor --spec-id N --dry-run
```

---

### Stage 7 — Decision Queue

**Purpose:** Prioritised queue of all scored strategies awaiting human
review action.

**Inputs:** `scoring_results`, `strategy_specs` (read-only join)

**Outputs:** Ordered list by recommendation priority:
1. Live Candidate
2. Forward Test
3. Optimize
4. Retest
5. Reject

Each entry includes `status=REVIEW_REQUIRED`, `next_action`, and
`classification`.

**Module:** `api/queries.py → decision_queue()`

**Terminal state:** `REVIEW_REQUIRED` is assigned to every entry. No
automated process sets `approved` or moves a strategy out of the queue.

---

### Stage 8 — Human Review

**Purpose:** The only stage that can advance a strategy beyond research.

**Inputs:**
- Audit report (`reports/audits/<spec_name>_audit.md`)
- Strategy report (`reports/strategies/<spec_name>.md`)
- Decision queue entry
- Scoring result

**Outputs (human-initiated, manual):**
- `research/approved/` — strategy approved for forward testing
- `research/rejected/` — strategy archived with documented rejection reason

**No code executes this stage.** Human reads the audit, reviews the score,
makes a decision, and records the outcome manually.

---

## Operational Commands

### Full pipeline (one strategy, real NT8 export)
```powershell
# 1. Import spec
python connectors/strategy_specs/spec_importer.py `
    --file research/specs/MY_STRATEGY_v001.yaml

# 2. Probe export files
python connectors/ninjatrader/nt8_import_pipeline.py --probe-only `
    --summary exports/MY_STRATEGY_summary.csv `
    --trade-list exports/MY_STRATEGY_trades.csv

# 3. Import, score, and verify (all in one)
python connectors/ninjatrader/nt8_import_pipeline.py `
    --summary exports/MY_STRATEGY_summary.csv `
    --trade-list exports/MY_STRATEGY_trades.csv `
    --spec-id N `
    --initial-capital 50000

# 4. Audit
python -m research.audit.strategy_auditor --spec-id N

# 5. Review reports/audits/MY_STRATEGY_v001_<date>_audit.md
```

### Score all strategies with backtest data
```powershell
python research/scoring/score_from_backtests.py --all
```

### Audit all strategies
```powershell
python -m research.audit.strategy_auditor --all
```

### Full integration test (sample data, CI)
```powershell
python -m research.integration.full_pipeline_test --run-label ci
```

### Scheduled pipeline runs
```powershell
# Daily: import + score
python research/scheduler/run_pipeline.py --stages import,score

# Weekly: score + report
python research/scheduler/run_pipeline.py --stages score,report

# Monthly: full run
python research/scheduler/run_pipeline.py --stages import,score,report
```

---

## Troubleshooting

### Real NT8 export columns do not match the mapping

**Symptom:** `--probe` reports `MISSING` columns in the Required columns table.

**Cause:** NT8 column names vary between versions or locale settings.

**Fix:**
1. Open the CSV and compare column names to `_SUMMARY_MAP` in
   `connectors/ninjatrader/backtest_ingestor.py`.
2. Edit the CSV header to match the expected name, OR
3. Add an alias entry to `_SUMMARY_MAP` for the variant name.
4. Document the NT8 version difference in `docs/NT8_REAL_EXPORT_VALIDATION.md`.

### Sample data vs real data

**Symptom:** Probe shows `[WARN] Sample file detected`.

**Cause:** The filename contains `sample`, `demo`, `example`, or `fixture`.

**Fix:** Use a real NT8 Strategy Analyzer export. Sample files are for
integration testing only and should not be used to evaluate real strategies.

### Rankings show a strategy multiple times

**Symptom:** The same strategy appears more than once in `/research-rankings`.

**Cause:** `scoring_results` is append-only — multiple scoring runs create
multiple rows.

**Fix:** Rankings queries already apply the deduplication filter:
```sql
WHERE sr.scoring_id = (
    SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = sr.spec_id
)
```
If duplication persists, verify the query in `api/queries.py →
research_rankings()` includes this WHERE clause.

### Generated reports not appearing in `git status`

**Symptom:** `reports/audits/` and `reports/strategies/` files are not
tracked by Git.

**Cause:** Both directories are gitignored by design. Generated output is
not committed.

**This is correct behaviour.** Reports are regenerated on demand and should
not be version-controlled. To share a report, copy it out of the
`reports/` directory manually.

### Scoring fails after backtest import

**Symptom:** `run_for_spec()` raises `ValueError: spec_id=N not found` or
returns a Reject grade unexpectedly.

**Cause:** The backtest row may be missing `profit_factor`, `total_trades`,
or `max_drawdown_pct` — required by the hard gates.

**Fix:**
1. Run `--probe` on the original CSV to check if these columns parsed correctly.
2. Verify the backtest row: `SELECT profit_factor, total_trades, max_drawdown_pct FROM backtests WHERE spec_id = N`.
3. If null, re-import with the correct Performance Summary CSV (not trade-list-only).

### Audit recommends NEEDS_REAL_NT8_EXPORT after import

**Symptom:** Audit reports `trade_list_json is null` even after running the pipeline.

**Cause:** The import used `--summary` only without `--trade-list`, or the
trade list import failed silently.

**Fix:** Re-run the pipeline with both `--summary` and `--trade-list`:
```powershell
python connectors/ninjatrader/nt8_import_pipeline.py `
    --summary path/to/summary.csv `
    --trade-list path/to/trades.csv `
    --spec-id N
```

---

## File Map

```
connectors/
  strategy_specs/
    spec_importer.py          -- Stage 1: spec import
    sample_strategy_spec.yaml -- sample YAML spec
    sample_strategy_spec.json -- sample JSON spec
  ninjatrader/
    backtest_ingestor.py      -- Stage 2: probe, validate, import
    nt8_import_pipeline.py    -- Stage 2: end-to-end pipeline runner
    sample_nt8_backtest_summary.csv
    sample_nt8_trade_list.csv

research/
  scoring/
    scoring.py                -- Stage 3: composite score engine
    runner.py                 -- Stage 3: run_for_spec(), run_batch()
    score_from_backtests.py   -- Stage 3: CLI
    weights.py                -- Stage 3: thresholds, grade bands
  audit/
    strategy_auditor.py       -- Stage 6: pre-approval audit checklist
  integration/
    full_pipeline_test.py     -- End-to-end integration test (9 stages)
    test_spec.yaml            -- Integration test fixture
  scheduler/
    run_pipeline.py           -- Scheduled pipeline runner
    register_tasks.ps1        -- Windows Task Scheduler setup

api/
  queries.py                  -- Stage 4+7: rankings, decision queue

docs/
  PIPELINE.md                 -- This document
  NT8_REAL_EXPORT_VALIDATION.md
  RECOVERY.md
  ROADMAP.md
  SCHEDULE.md

reports/
  strategies/  (gitignored)   -- Stage 5 output
  audits/      (gitignored)   -- Stage 6 output
```

---

## Governance

This pipeline is governed by `CLAUDE.md` at the project root. That file
supersedes all ad-hoc decisions. All future phases must align with:

1. No unverified claims — all metrics must originate from actual backtests.
2. Robustness over performance — explainability and stability before approval.
3. Reject, don't delete — failed strategies are archived, never removed.
4. Human-in-the-loop — no strategy advances to live trading without explicit
   human approval.
5. Controlled cadence — research operates on a regulated schedule, not
   continuous uncontrolled loops.
