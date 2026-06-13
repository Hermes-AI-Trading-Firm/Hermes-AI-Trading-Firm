# Hermes AI Trading Firm — Database

Path: `database/hermes_research.db`

## Tools Used

- Initialization: `database/init.sql`
- Sync script: `database/sync_queue.py`

## Usage

Initialize:
```bash
uv run python database/sync_queue.py
```

This will:
1. Create `hermes_research.db` from `init.sql`.
2. Import all specs from `research/strategy_queue/*.md`.
3. Write dashboard state to `reports/dashboard_state.json`.

## Where the database is used

- Strategy Factory → inserts `strategy_ideas` and `strategy_specs`
- AI Learning Brain → reads/writes `research_notes`
- Backtests / Optimization / Regime / Forward Testing: write their tables
- Dashboard: reads consolidated state via `reports/dashboard_state.json`

## Locations

| Artifact | Path |
|----------|------|
| DB file | `database/hermes_research.db` |
| Schema + init | `database/init.sql` |
| Sync script | `database/sync_queue.py` |
| Dashboard state | `reports/dashboard_state.json` |
| Schema docs | `database/schema.md` |
