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
- Strategy scoring engine → writes `scoring_results` after each run
- NT8 sync → writes `nt8_trades` and `nt8_account_snapshots` from NT8 exports
- Dashboard: reads consolidated state via `reports/dashboard_state.json` (Phase 1) / API server (Phase 2+)

## Tables

| Table | Phase | Purpose |
|-------|-------|---------|
| markets | Original | Tradeable instrument registry |
| strategy_ideas | Original | Raw ideas from Strategy Factory |
| strategy_specs | Original | Complete strategy specifications |
| backtests | Original | Backtest results |
| optimizations | Original | Parameter optimization runs |
| regime_analysis | Original | Regime detection results |
| forward_tests | Original | Paper trading records |
| approved_strategies | Original | Approved strategies archive |
| rejected_strategies | Original | Failed strategies archive |
| research_notes | Original | AI Learning Brain observations |
| scoring_results | Phase 1 | Strategy scoring engine output |
| prop_firm_profiles | Phase 1 | Prop firm account constraint profiles |
| nt8_trades | Phase 1 | NT8 closed trade imports |
| nt8_account_snapshots | Phase 1 | NT8 account state time-series |

## Locations

| Artifact | Path |
|----------|------|
| DB file | `database/hermes_research.db` |
| Schema + init | `database/init.sql` |
| Sync script | `database/sync_queue.py` |
| Dashboard state | `reports/dashboard_state.json` |
| Schema docs | `database/schema.md` |
| NT8 export dir | `nt8_export/` (gitignored) |
