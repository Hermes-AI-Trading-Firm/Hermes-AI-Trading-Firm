# NT8 Import Layer

File-based NinjaTrader 8 import connector for the Hermes AI Trading Firm.

**Scope**: File import only. No live trading, no broker connection, no order placement, no ATM control.

---

## Overview

```
NinjaTrader 8
    └── Export files manually or via NinjaScript
            ├── nt8_export/nt8_trades.csv
            └── nt8_export/nt8_account_state.json
                        │
                        ▼
            connectors/ninjatrader/nt8_sync.py
                        │
                        ▼
            database/hermes_research.db
                        ├── nt8_trades
                        └── nt8_account_snapshots
                        │
                        ▼
            API: GET /nt8-trades
                 GET /nt8-account
```

---

## Files

| File | Purpose |
|---|---|
| `nt8_sync.py` | Import script — reads CSV + JSON, writes to DB |
| `sample_nt8_trades.csv` | Sample trades file (5 MNQ trades for testing) |
| `sample_nt8_account_state.json` | Sample account snapshot for testing |
| `mapping.md` | Field-by-field mapping: NT8 → Hermes DB columns |

---

## Setup

1. Create `nt8_export/` in the project root (already tracked via `.gitkeep`):
   ```
   nt8_export/
     nt8_trades.csv          ← place NT8 trade exports here
     nt8_account_state.json  ← place NT8 account snapshots here
   ```

2. Run the API server so the DB schema exists:
   ```
   python api/run_api.py
   ```

3. Run the sync:
   ```
   python connectors/ninjatrader/nt8_sync.py
   ```

---

## Usage

```bash
# Default: reads from nt8_export/
python connectors/ninjatrader/nt8_sync.py

# Custom file paths:
python connectors/ninjatrader/nt8_sync.py \
    --trades  nt8_export/nt8_trades.csv \
    --account nt8_export/nt8_account_state.json

# Test with sample files:
python connectors/ninjatrader/nt8_sync.py \
    --trades  connectors/ninjatrader/sample_nt8_trades.csv \
    --account connectors/ninjatrader/sample_nt8_account_state.json

# Dry-run (validate without writing):
python connectors/ninjatrader/nt8_sync.py --dry-run

# Custom DB path:
python connectors/ninjatrader/nt8_sync.py --db path/to/hermes_research.db
```

---

## Import Rules

- **Idempotent**: Safe to run multiple times. Duplicates are detected via unique indexes and skipped.
- **Read-only source**: The script never modifies the CSV or JSON input files.
- **Validation first**: All rows are validated before any DB write. Rows with errors are skipped; valid rows are still imported.
- **No schema changes**: The script adds two unique indexes on first run but does not modify any table structure.

### Deduplication keys

| Table | Unique key |
|---|---|
| `nt8_trades` | `(account_id, symbol, direction, entry_time, entry_price)` |
| `nt8_account_snapshots` | `(account_id, snapshot_at)` |

---

## Input Formats

### `nt8_trades.csv`

Required columns: `strategy_id`, `symbol`, `direction`, `entry_time`, `exit_time`, `entry_price`, `exit_price`, `quantity`, `pnl`

Optional columns: `account_id`, `commission`, `slippage`, `atm_template`

Direction must be `LONG` or `SHORT` (case-insensitive).

Datetime columns accept: `YYYY-MM-DD HH:MM:SS`, `MM/DD/YYYY HH:MM:SS`, ISO-8601, and others — see `mapping.md`.

### `nt8_account_state.json`

Required keys: `account_id`, `equity`, `snapshot_at`

May be a single JSON object `{...}` or an array `[{...}, {...}]`.

---

## API Endpoints (read-only)

After import, data is immediately available via the API:

```
GET http://localhost:7433/nt8-trades          → recent closed trades
GET http://localhost:7433/nt8-account         → latest account snapshot
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `ERROR: Database not found` | Run `python api/run_api.py` once to initialise the DB, then Ctrl+C |
| `Missing required columns` | Check CSV headers match expected names (see `mapping.md`) |
| Direction error | NT8 sometimes exports `Buy` / `Sell` — rename to `LONG` / `SHORT` |
| `JSON parse error` | Validate the JSON with a linter; check for trailing commas |
| All rows skipped | File was already imported; check with `--dry-run` to confirm |
