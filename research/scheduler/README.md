# research/scheduler — Pipeline Runner

Executes the full Hermes research chain in sequence:

```
import → score → report
```

No daemon. No continuous loop. Runs once and exits. Call manually or via Windows Task Scheduler.

---

## Files

| File | Purpose |
|------|---------|
| `run_pipeline.py` | Main runner script — three stages, CLI args, file logging |

---

## Usage

### Full pipeline (all three stages)
```powershell
python research/scheduler/run_pipeline.py
```

### Specific stages only
```powershell
python research/scheduler/run_pipeline.py --stages import,score
python research/scheduler/run_pipeline.py --stages report
python research/scheduler/run_pipeline.py --stages score,report
```

### Dry run (no DB writes, no files written)
```powershell
python research/scheduler/run_pipeline.py --dry-run
```

### Custom database or log path
```powershell
python research/scheduler/run_pipeline.py --db database/hermes_research.db --log logs/manual.log
```

---

## Stages

| Stage | What it does |
|-------|-------------|
| `import` | Reads `nt8_export/nt8_trades.csv` and `nt8_export/nt8_account_state.json`, imports into DB via `connectors/ninjatrader/nt8_sync.py`. Duplicates skipped. |
| `score` | Runs `research.scoring.runner.run_from_db()` — scores all unscored strategies, saves results to `scoring_results`. |
| `report` | Runs `research.reporting.report_generator.generate_all_reports()` + `exporter.export_all()` — writes Markdown + JSON to `reports/strategies/` and `reports/summaries/`. |

---

## Logging

Each run writes a timestamped log to `logs/pipeline_YYYYMMDD_HHMMSS.log`.

Log format:
```
2026-06-14T06:00:01  INFO     Hermes Pipeline Runner — 2026-06-14T06:00:01 — mode=LIVE
2026-06-14T06:00:01  INFO     Stages: import, score, report
2026-06-14T06:00:02  INFO     === STAGE: import ===
...
2026-06-14T06:00:05  INFO     Pipeline complete in 4.2s — SUCCESS
```

Dry-run logs go to console only (no file written unless `--log` is passed explicitly).

---

## Windows Task Scheduler Setup

To run the full pipeline daily at 06:00:

| Field | Value |
|-------|-------|
| Program | `python.exe` (full path from `where python`) |
| Arguments | `research\scheduler\run_pipeline.py` |
| Start in | `C:\Users\ebo13\Hermes-AI-Trading-Firm` |
| Trigger | Daily at 06:00 |
| Run whether logged on or not | Optional |

---

## Constraints

- Read-only API server is separate — this runner writes only to the database and `reports/`.
- No live trading. No broker connection. No order placement.
- NT8 data enters via CSV file drop into `nt8_export/` only.
- Human approval is required before any strategy advances beyond the Decision Queue.
