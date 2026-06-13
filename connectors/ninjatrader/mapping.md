# NT8 → Hermes Field Mapping

Field mapping between NinjaTrader 8 export formats and Hermes database tables.

---

## Trades: `nt8_trades.csv` → `nt8_trades`

| CSV Column | DB Column | Type | Required | Notes |
|---|---|---|---|---|
| `strategy_id` | `strategy_id` | TEXT | **Yes** | Must match a `strategy_specs.spec_name` or any short label |
| `account_id` | `account_id` | TEXT | No | NT8 account name (e.g. `Sim101`) |
| `symbol` | `symbol` | TEXT | **Yes** | Uppercased on import (e.g. `MNQ`, `ES`, `NQ`) |
| `direction` | `direction` | TEXT | **Yes** | `LONG` or `SHORT` (case-insensitive on import) |
| `entry_time` | `entry_time` | TEXT (ISO-8601) | **Yes** | See datetime formats below |
| `exit_time` | `exit_time` | TEXT (ISO-8601) | **Yes** | See datetime formats below |
| `entry_price` | `entry_price` | REAL | **Yes** | Numeric; NT8 uses decimal point |
| `exit_price` | `exit_price` | REAL | **Yes** | Numeric |
| `quantity` | `quantity` | INTEGER | **Yes** | Number of contracts |
| `pnl` | `pnl` | REAL | **Yes** | Net PnL per trade (before commission if NT8 reports gross) |
| `commission` | `commission` | REAL | No | Per-trade commission; defaults to 0.0 |
| `slippage` | `slippage` | REAL | No | Per-trade slippage; defaults to 0.0 |
| `atm_template` | `atm_template` | TEXT | No | Name of NT8 ATM strategy used |

### Accepted datetime formats

The import layer normalises any of the following to ISO-8601 (`YYYY-MM-DDTHH:MM:SS`):

| Format | Example |
|---|---|
| `YYYY-MM-DDTHH:MM:SS` | `2026-06-10T09:31:00` |
| `YYYY-MM-DD HH:MM:SS` | `2026-06-10 09:31:00` |
| `MM/DD/YYYY HH:MM:SS` | `06/10/2026 09:31:00` |
| `MM/DD/YYYY HH:MM` | `06/10/2026 09:31` |
| `YYYY-MM-DDTHH:MM` | `2026-06-10T09:31` |

### Deduplication key

`(account_id, symbol, direction, entry_time, entry_price)` — a unique index is added by `nt8_sync.py` on first run. Re-running the same file inserts 0 rows and reports the count as "Skipped".

---

## Account State: `nt8_account_state.json` → `nt8_account_snapshots`

| JSON Key | DB Column | Type | Required | Notes |
|---|---|---|---|---|
| `account_id` | `account_id` | TEXT | **Yes** | NT8 account name |
| `equity` | `equity` | REAL | **Yes** | Current account equity |
| `daily_pnl` | `daily_pnl` | REAL | No | Today's realised PnL |
| `daily_pnl_pct` | `daily_pnl_pct` | REAL | No | Daily PnL as decimal (0.025 = 2.5%) |
| `open_drawdown` | `open_drawdown` | REAL | No | Unrealised open trade drawdown |
| `trailing_drawdown_used` | `trailing_drawdown_used` | REAL | No | Trailing DD consumed so far (dollar amount) |
| `trailing_drawdown_limit` | `trailing_drawdown_limit` | REAL | No | Total trailing DD allowed (dollar amount) |
| `daily_loss_limit` | `daily_loss_limit` | REAL | No | Daily loss cap (dollar amount) |
| `active_strategy_id` | `active_strategy_id` | TEXT | No | Strategy currently running in NT8 |
| `snapshot_at` | `snapshot_at` | TEXT (ISO-8601) | **Yes** | Timestamp of the snapshot |

The JSON file may contain a single object or an array of objects (for batch snapshots).

### Deduplication key

`(account_id, snapshot_at)` — unique index added on first run. Importing the same snapshot twice is safe.

---

## How to export from NinjaTrader 8

### Trades CSV

1. Open **Control Center → Account Performance** tab
2. Set date range to cover the period you want to import
3. Filter by strategy name if needed
4. Click **Export** → select CSV format
5. Rename output to `nt8_trades.csv`
6. Add a `strategy_id` column if it was not included; use the Hermes spec name (e.g. `MNQ_ORB_FVG_v001`)
7. Place file in `nt8_export/nt8_trades.csv`

### Account State JSON

NT8 does not natively export a JSON snapshot. The recommended approach is one of:

| Method | Description |
|---|---|
| Manual | Create `nt8_account_state.json` by hand from the Account panel values |
| NinjaScript | Write an NT8 indicator/strategy that serialises account state to JSON on each bar close |
| File-based bridge | Use a NinjaScript `OnExecutionUpdate` / `OnAccountItemChanged` handler to write the file |

The file must be placed at `nt8_export/nt8_account_state.json` (or passed via `--account`).

---

## NT8 column name aliases

If your NT8 export uses different column names, rename before importing or patch `nt8_sync.py`:

| NT8 default export name | Hermes expected name |
|---|---|
| `Strategy Name` | `strategy_id` |
| `Account` | `account_id` |
| `Instrument` | `symbol` |
| `Market Position` | `direction` |
| `Entry Time` | `entry_time` |
| `Exit Time` | `exit_time` |
| `Entry Price` | `entry_price` |
| `Exit Price` | `exit_price` |
| `Quantity` | `quantity` |
| `Profit` | `pnl` |
| `Commission` | `commission` |
