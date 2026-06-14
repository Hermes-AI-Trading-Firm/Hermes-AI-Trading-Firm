# Hermes AI Trading Firm — Research Schedule

Defines the three recurring cadences from `CLAUDE.md` and how each maps to a pipeline run.

---

## Cadences

| Cadence | When | Stages | Purpose |
|---------|------|--------|---------|
| **Daily** | Every day at 06:00 | `import, score` | Ingest new NT8 exports; re-score all strategies |
| **Weekly** | Sunday at 07:00 | `score, report` | Refresh rankings; generate firm summary for weekly human review |
| **Monthly** | 1st of month at 08:00 | `import, score, report` | Full comprehensive pass; output reports for monthly human review session |

---

## What each stage does

| Stage | Module called | Output |
|-------|--------------|--------|
| `import` | `connectors/ninjatrader/nt8_sync.py` | Rows in `nt8_trades`, `nt8_account_snapshots` |
| `score` | `research/scoring/runner.py` | Rows in `scoring_results` |
| `report` | `research/reporting/report_generator.py` + `exporter.py` | Files in `reports/strategies/` and `reports/summaries/` |

---

## Setup — Windows Task Scheduler (Option B)

Run once from an **elevated** PowerShell prompt:

```powershell
cd C:\Users\ebo13\Hermes-AI-Trading-Firm
powershell -ExecutionPolicy Bypass -File research\scheduler\register_tasks.ps1
```

This registers three tasks under `\Hermes\` in Task Scheduler:

| Task name | Trigger | Command |
|-----------|---------|---------|
| `Hermes-Daily-Import-Score` | Daily 06:00 | `run_pipeline.py --stages import,score` |
| `Hermes-Weekly-Score-Report` | Sunday 07:00 | `run_pipeline.py --stages score,report` |
| `Hermes-Monthly-Full-Pipeline` | 1st of month 08:00 | `run_pipeline.py --stages import,score,report` |

Re-running the script is safe — tasks are replaced, not duplicated.

### Verify tasks were registered
```powershell
Get-ScheduledTask -TaskPath "\Hermes\" | Format-Table TaskName, State
```

### Trigger a task manually (for testing)
```powershell
Start-ScheduledTask -TaskPath "\Hermes\" -TaskName "Hermes-Daily-Import-Score"
```

### Remove all Hermes tasks
```powershell
Unregister-ScheduledTask -TaskPath "\Hermes\" -Confirm:$false
```

---

## Logs

Each task run writes a timestamped log to `logs/`:

```
logs/
  pipeline_20260614_060001.log   ← daily run
  pipeline_20260615_070002.log   ← weekly run
  pipeline_20260701_080003.log   ← monthly run
```

Log format:
```
2026-06-14T06:00:01  INFO     Hermes Pipeline Runner — 2026-06-14T06:00:01 — mode=LIVE
2026-06-14T06:00:01  INFO     Stages: import, score
2026-06-14T06:00:01  INFO     DB    : C:\...\database\hermes_research.db
2026-06-14T06:00:01  INFO     === STAGE: import ===
...
2026-06-14T06:00:05  INFO     Pipeline complete in 4.2s — SUCCESS
```

Exit code 0 = all stages succeeded. Exit code 1 = at least one stage failed (Task Scheduler will record as failed).

---

## Manual Run (Option A)

The runner can also be called directly without Task Scheduler:

```powershell
# Full pipeline
python research/scheduler/run_pipeline.py

# Dry run — validate without writing
python research/scheduler/run_pipeline.py --dry-run

# Specific stages
python research/scheduler/run_pipeline.py --stages import,score
python research/scheduler/run_pipeline.py --stages report
```

---

## Cadence Alignment with CLAUDE.md

| CLAUDE.md activity | Mapped cadence | Pipeline stages |
|-------------------|----------------|----------------|
| Daily — up to 3 new strategy ideas; backtest 1–3 completed specs | Daily 06:00 | `import, score` — data ingested, scores current |
| Weekly — review top 10 strategies by rank; optimize best 3 | Sunday 07:00 | `score, report` — rankings refreshed, reports ready |
| Monthly — comprehensive review; retire weak performers; human review session; update AI Learning Brain | 1st 08:00 | `import, score, report` — full pipeline, all outputs current for human review |

---

## Constraints

- No live trading. No broker connection. No order placement.
- NT8 data enters via file drop into `nt8_export/` only — the scheduler does not pull live data.
- Human approval is required before any strategy advances beyond the Decision Queue.
- Task Scheduler runs are unattended. The human reviews outputs at their own cadence.
