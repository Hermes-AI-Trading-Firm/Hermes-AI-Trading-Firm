# research/reporting

Phase 10 — Report generation layer for the Hermes AI Trading Firm pipeline.

Sits at the end of the pipeline:
```
Import → Analyze → Score → Rank → Comply → Report
```

## Files

| File | Purpose |
|------|---------|
| `report_generator.py` | Build report dicts from DB (read-only) |
| `templates.py` | Render report dicts as Markdown strings |
| `exporter.py` | Write `.md` and `.json` files to `reports/` |
| `__init__.py` | Package marker |

---

## Output Structure

```
reports/
├── strategies/
│   ├── MGC_VWAP_PULLBACK_v001_20260614.md
│   ├── MGC_VWAP_PULLBACK_v001_20260614.json
│   ├── MNQ_ORB_FVG_v001_20260614.md
│   └── ...
└── summaries/
    ├── firm_summary_20260614.md
    └── firm_summary_20260614.json
```

---

## Public API

### `report_generator.py`

```python
generate_strategy_report(conn, spec_id) -> Dict
```
Pulls spec + latest scoring result + latest backtest for one strategy.
Returns a plain dict. No DB writes.

```python
generate_all_reports(conn) -> List[Dict]
```
Generates a report for every spec that has a scoring result.
Sorted by composite_score descending.

### `exporter.py`

```python
export_strategy_report(report) -> (md_path, json_path)
export_firm_summary(reports, generated_at) -> (md_path, json_path)
export_all(reports) -> {"strategies": {...}, "summary": {...}}
```

### `templates.py`

```python
render_strategy_md(report) -> str
render_firm_summary_md(reports, generated_at) -> str
CLASSIFICATION_MAP  # recommendation → canonical label
```

---

## Classification Map

| Recommendation | Classification |
|----------------|---------------|
| Live Candidate | `LIVE_CANDIDATE` |
| Forward Test | `FORWARD_TEST_CANDIDATE` |
| Optimize | `OPTIMIZATION_CANDIDATE` |
| Retest | `NEEDS_RETEST` |
| Reject | `REJECTED` |

---

## Usage

```python
import sqlite3
from research.reporting.report_generator import generate_all_reports
from research.reporting.exporter import export_all

conn = sqlite3.connect("database/hermes_research.db")
reports = generate_all_reports(conn)
paths = export_all(reports)
conn.close()

print(paths["summary"]["md"])
```

---

## Safety Boundaries

- **Read-only** DB access — no INSERT, UPDATE, or DELETE
- **No database schema changes**
- **Output only** to `reports/strategies/` and `reports/summaries/`
- **No API endpoint** — called directly from scripts or runner
- **No live trading, no broker connection, no order placement**
- Human approval gate remains mandatory before any strategy advances
