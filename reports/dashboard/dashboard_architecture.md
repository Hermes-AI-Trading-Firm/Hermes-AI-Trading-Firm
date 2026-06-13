# Hermes AI Trading Firm — Dashboard Architecture Plan

**Date**: 2026-06-13  
**Status**: Plan only — no code written  
**Scope**: Five new panels + database extensions + API layer + NT8 integration

---

## Current State Audit

The existing dashboard (`dashboard/dashboard.html` + `dashboard.js`) is a fully-styled HTML shell with:
- Four layout rows: Market Focus, Strategy Queue, Firm Health, Approved/Rejected, Forward Testing, Database Status, Activity Log
- A JavaScript mock data layer (`dashboard_data.js`) that falls back to `reports/dashboard_state.json`
- `sync_queue.py` writes only 8 aggregate fields to `dashboard_state.json` — all rich panel data (strategy lists, rankings, forward test metrics) is still served from hardcoded MOCK objects

**Gap summary**: The UI shell exists. The data pipeline behind it is mostly mock. All five planned panels require real database queries, a local API server, and (for NT8) a file-based or socket bridge.

---

## 1. Strategy Queue Panel

### Purpose
Show all active strategy specs in the research pipeline with their current stage, sortable and filterable by asset class, timeframe, and status.

### Data Source
Tables: `strategy_ideas`, `strategy_specs`

### View Design

```
┌─────────────────────────────────────────────────────────────────┐
│ Strategy Queue                              [Filter ▼] [Sort ▼] │
├──────┬───────────────────────────┬──────────┬──────────┬────────┤
│  ID  │ Name                      │ Asset    │ Stage    │ Status │
├──────┼───────────────────────────┼──────────┼──────────┼────────┤
│ S-01 │ MNQ ORB Fair Value Gap    │ Futures  │ Backtesting│ Active│
│ S-02 │ BTC Regime Breakout       │ Crypto   │ Regime   │ Active │
│ S-03 │ SPY Wheel Strategy        │ Stocks   │ Scoring  │ Active │
│ S-04 │ MGC VWAP Pullback         │ Futures  │ Spec     │ Draft  │
└──────┴───────────────────────────┴──────────┴──────────┴────────┘
```

### Fields Required

| Field | Source |
|---|---|
| `spec_id` / `idea_id` | `strategy_specs.spec_id` |
| `name` | `strategy_specs.spec_name` |
| `asset_class` | `strategy_specs.asset_class` |
| `symbol` | `strategy_specs.symbol` |
| `timeframe` | `strategy_specs.timeframe` |
| `stage` | `strategy_specs.status` (draft → coding → backtesting → optimized → regime_analyzed → approved/rejected) |
| `days_in_stage` | Computed: `datetime('now') - updated_at` |
| `profit_factor` | `backtests.profit_factor` (latest backtest for spec) |

### Interaction
- Click row → expand inline detail card (entry rules, hypothesis, latest backtest summary)
- Badge turns red if `days_in_stage > 7` (stale)
- Filter by `asset_class`, `status`, `timeframe`

### SQL Query (core)
```sql
SELECT
  ss.spec_id, ss.spec_name, ss.asset_class, ss.symbol,
  ss.timeframe, ss.status,
  CAST((julianday('now') - julianday(ss.updated_at)) AS INTEGER) AS days_in_stage,
  b.profit_factor
FROM strategy_specs ss
LEFT JOIN backtests b ON b.spec_id = ss.spec_id
  AND b.backtest_id = (SELECT MAX(backtest_id) FROM backtests WHERE spec_id = ss.spec_id)
WHERE ss.status NOT IN ('approved', 'rejected')
ORDER BY ss.updated_at DESC;
```

---

## 2. Research Rankings Panel

### Purpose
Rank all tested strategies by composite score (from `strategy_scoring` engine), surfacing the best candidates across all asset classes.

### Data Source
Tables: `strategy_specs`, `backtests`, `regime_analysis`  
New table required: `scoring_results` (see Section 6)

### View Design

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Research Rankings                              Sort by: [Score ▼] [PF] [DD]│
├──────┬───────────────────────┬───────┬──────┬──────┬──────┬───────┬───────┤
│ Rank │ Strategy              │ Asset │  PF  │Sharpe│  DD  │ Score │ Grade │
├──────┼───────────────────────┼───────┼──────┼──────┼──────┼───────┼───────┤
│   1  │ MNQ ORB FVG           │  F   │ 1.82 │ 1.41 │ 11.2%│ 87.4  │   A   │
│   2  │ BTC Regime Breakout   │  C   │ 1.71 │ 1.28 │ 18.9%│ 81.2  │   A   │
│   3  │ SPY Wheel Strategy    │  S   │ 1.44 │ 0.97 │  7.1%│ 73.6  │   B   │
│   4  │ MGC VWAP Pullback     │  F   │ 1.35 │ 0.88 │ 14.3%│ 66.1  │   C   │
└──────┴───────────────────────┴───────┴──────┴──────┴──────┴───────┴───────┘
```

### Fields Required

| Field | Source |
|---|---|
| `composite_score` | `scoring_results.composite_score` |
| `grade` | `scoring_results.grade` |
| `recommendation` | `scoring_results.recommendation` |
| `profit_factor` | `backtests.profit_factor` |
| `sharpe_ratio` | `backtests.sharpe_ratio` |
| `max_drawdown_pct` | `backtests.max_drawdown_pct` |
| `walk_forward_pass` | `scoring_results.walk_forward_score` |
| `monte_carlo_pass` | `scoring_results.monte_carlo_score` |
| `best_regime` | `regime_analysis.best_regime` |
| `worst_regime` | `regime_analysis.worst_regime` |

### Interaction
- Sortable columns: Score, PF, Sharpe, DD, Grade
- Row expand: full category score breakdown (profitability, drawdown, walk-forward, monte carlo, regime, robustness)
- Color coding: A+ / A = green, B = yellow, C = orange, D / Reject = red
- Filter: asset class, min score threshold, passed-only toggle

---

## 3. Prop Firm Candidate Panel

### Purpose
Identify strategies that meet prop firm account constraints (drawdown limits, daily loss limits, profit targets) and surface them as eligible candidates.

### Data Source
Tables: `approved_strategies`, `backtests`, `forward_tests`  
New table required: `prop_firm_profiles` (see Section 6)

### View Design

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Prop Firm Candidates              Account: [Apex $50K ▼]  [Add Profile +]│
├───────────────────────┬──────┬────────┬──────────┬──────────┬────────────┤
│ Strategy              │  PF  │Max DD  │DD Breach │RoR       │ Eligible   │
├───────────────────────┼──────┼────────┼──────────┼──────────┼────────────┤
│ MNQ ORB FVG           │ 1.82 │  11.2% │   0.0%   │  0.3%    │  ✅ Yes    │
│ BTC Regime Breakout   │ 1.71 │  18.9% │   2.1%   │  1.2%    │  ✅ Yes    │
│ SPY Wheel Strategy    │ 1.44 │   7.1% │   0.0%   │  0.0%    │  ✅ Yes    │
│ MGC VWAP Pullback     │ 1.35 │  14.3% │   8.4%   │  4.1%    │  ⚠ Review  │
└───────────────────────┴──────┴────────┴──────────┴──────────┴────────────┘
  Account rules: Max trailing DD 8% · Daily loss limit 2% · Min 30 trading days
```

### Fields Required

| Field | Source |
|---|---|
| `max_drawdown_pct` | `backtests.max_drawdown_pct` |
| `drawdown_breach_probability` | `scoring_results.monte_carlo_score` detail |
| `risk_of_ruin` | Monte Carlo result JSON |
| `longest_losing_streak` | `backtests.max_consecutive_losses` |
| `prop_firm_supported` | `scoring_results.prop_firm_support` JSON |
| `account_profile` | `prop_firm_profiles` (new table) |

### Prop Firm Profile Fields
Each profile defines:
- `account_size` (e.g. 50 000)
- `trailing_drawdown_limit` (e.g. 0.08 = 8%)
- `daily_loss_limit` (e.g. 0.02 = 2%)
- `profit_target` (e.g. 0.10 = 10%)
- `min_trading_days` (e.g. 30)
- `firm_name` (e.g. "Apex", "Topstep", "FTMO")

### Interaction
- Dropdown to switch between saved prop firm profiles
- Traffic-light eligibility: green (all constraints met), amber (borderline), red (fails)
- Tooltip on each metric showing which constraint it maps to
- "Add Profile" button opens a modal form (future implementation)

---

## 4. Research Pipeline Status Panel

### Purpose
Show the full workflow status of all strategies in the pipeline as a visual stage tracker, replacing the current basic "Database Status" widget.

### View Design

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Research Pipeline Status                              Last sync: 10:02 AM   │
│                                                                              │
│  Idea → Spec → Backtest → Risk → Regime → Optimization → Walk-Fwd → MC → Score
│   4      4        4         2       4           2             2        2     2  │
│                                                                              │
│  ████████  Approved: 0   ████████  Rejected: 0   ████████  In Progress: 4  │
│                                                                              │
│ Stage Breakdown:                                                             │
│  [draft 0] [coding 0] [backtesting 4] [optimized 0] [regime 4] [scoring 2] │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Fields Required

| Field | Source |
|---|---|
| Count per `strategy_specs.status` | `strategy_specs` GROUP BY status |
| Count per `strategy_ideas.status` | `strategy_ideas` GROUP BY status |
| Active backtest runs | `backtests` WHERE `created_at > now - 24h` |
| Pending optimizations | `optimizations` WHERE `status = 'running'` |
| Regime analyses completed today | `regime_analysis` WHERE `created_at > today` |
| Recent research notes | `research_notes` ORDER BY `created_at DESC LIMIT 5` |
| Walk-forward pass rate | `scoring_results.walk_forward_score > 0.7` |
| Monte Carlo pass rate | `scoring_results.monte_carlo_pass = 1` |

### SQL Query (stage counts)
```sql
SELECT status, COUNT(*) as count
FROM strategy_specs
GROUP BY status
UNION ALL
SELECT 'ideas_pending', COUNT(*) FROM strategy_ideas WHERE status = 'pending';
```

### Interaction
- Stage boxes are clickable — click "backtesting" to jump to Strategy Queue filtered to that stage
- Auto-refreshes every 5 minutes alongside the rest of the dashboard
- Activity feed below shows last 10 events from `research_notes` and `rejected_strategies`

---

## 5. NT8 Integration Design

### Purpose
Bridge NinjaTrader 8 forward test execution data into the Hermes dashboard. NT8 runs the actual trades; the dashboard tracks and displays them.

### Integration Architecture

```
NinjaTrader 8 (local)
  └── NT8 Add-On / ATM Manager
        └── writes → nt8_export/
              ├── nt8_trades.csv         (trade-by-trade log)
              ├── nt8_account_state.json (equity, drawdown, daily P&L)
              └── nt8_alerts.json        (rule violations, limits hit)
                    ↓
              file watcher (Python)
              database/nt8_sync.py
                    ↓
              hermes_research.db
              └── nt8_trades (new table)
              └── nt8_account_snapshots (new table)
                    ↓
              dashboard API → NT8 panel
```

### NT8 Export Format

**`nt8_trades.csv`** (one row per closed trade):
```
trade_id, strategy_id, symbol, direction, entry_time, exit_time,
entry_price, exit_price, quantity, pnl, commission, slippage,
atm_template, account_id
```

**`nt8_account_state.json`** (written on each bar close):
```json
{
  "account_id": "Sim101",
  "equity": 102450.00,
  "daily_pnl": 312.50,
  "daily_pnl_pct": 0.0031,
  "open_drawdown": 0.0,
  "trailing_drawdown_used": 0.024,
  "trailing_drawdown_limit": 0.08,
  "daily_loss_limit": 0.02,
  "active_strategy_id": "MNQ_ORB_FVG_v001",
  "timestamp": "2026-06-13T10:00:00"
}
```

### NT8 Dashboard Panel View

```
┌──────────────────────────────────────────────────────────────────────┐
│ NT8 Forward Testing                          Account: Sim101  [Live] │
├──────────────────────┬────────────────────────────────────────────── │
│ Equity               │ $102,450  (+$312.50 today)                    │
│ Trailing DD Used     │ 2.4% of 8.0% limit   ████░░░░░░░░  ✅ Safe   │
│ Daily Loss Used      │ 0.0% of 2.0% limit   ░░░░░░░░░░░░  ✅ Safe   │
│ Active Strategy      │ MNQ ORB FVG v001                              │
│ Today's Trades       │ 3 trades · 2W/1L · PF 2.10                   │
│ Total Forward Trades │ 47 · Win rate 58.5% · PF 1.78                │
├──────────────────────┴────────────────────────────────────────────── │
│ Recent Trades                                                         │
│  10:02  MNQ  LONG  +$187.50  (2 contracts, 12 ticks)                │
│  09:41  MNQ  SHORT  -$62.50  (2 contracts, -4 ticks)                │
│  09:15  MNQ  LONG  +$187.50  (2 contracts, 12 ticks)                │
└──────────────────────────────────────────────────────────────────────┘
```

### NT8 Sync Script (`database/nt8_sync.py`)
Responsibilities:
1. Watch `nt8_export/` directory for new/modified files
2. Parse `nt8_trades.csv` → insert new rows into `nt8_trades`
3. Parse `nt8_account_state.json` → insert row into `nt8_account_snapshots`
4. Detect rule violations (daily loss limit approach, trailing DD approach) → write `research_notes`
5. Update `forward_tests` table with live running metrics

---

## 6. Database Schema Requirements

### New Tables

#### `scoring_results`
Stores output of `strategy_scoring.score_strategy()` per spec per run.

```sql
CREATE TABLE IF NOT EXISTS scoring_results (
    scoring_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id             INTEGER NOT NULL,
    composite_score     REAL NOT NULL,
    grade               TEXT NOT NULL,
    recommendation      TEXT NOT NULL,
    profitability_score REAL,
    drawdown_score      REAL,
    consistency_score   REAL,
    walk_forward_score  REAL,
    monte_carlo_score   REAL,
    regime_score        REAL,
    robustness_score    REAL,
    prop_firm_score     REAL,
    explainability_score REAL,
    overfitting_risk    REAL,
    monte_carlo_pass    INTEGER,
    walk_forward_pass   INTEGER,
    prop_firm_supported INTEGER,
    prop_firm_support_json TEXT,
    overfit_warnings_json  TEXT,
    scored_at           TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id)
);
CREATE INDEX IF NOT EXISTS idx_scoring_spec ON scoring_results(spec_id);
CREATE INDEX IF NOT EXISTS idx_scoring_score ON scoring_results(composite_score);
CREATE INDEX IF NOT EXISTS idx_scoring_grade ON scoring_results(grade);
```

#### `prop_firm_profiles`
Stores named prop firm account constraint profiles.

```sql
CREATE TABLE IF NOT EXISTS prop_firm_profiles (
    profile_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_name               TEXT NOT NULL,
    account_size            REAL NOT NULL,
    trailing_drawdown_limit REAL NOT NULL,
    daily_loss_limit        REAL,
    profit_target           REAL,
    min_trading_days        INTEGER,
    max_position_size       INTEGER,
    allowed_instruments     TEXT,
    notes                   TEXT,
    is_active               INTEGER DEFAULT 1,
    created_at              TEXT DEFAULT (datetime('now'))
);
```

#### `nt8_trades`
One row per closed NT8 trade.

```sql
CREATE TABLE IF NOT EXISTS nt8_trades (
    nt8_trade_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL,
    spec_id         INTEGER,
    forward_test_id INTEGER,
    account_id      TEXT,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry_time      TEXT NOT NULL,
    exit_time       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    quantity        INTEGER NOT NULL,
    pnl             REAL NOT NULL,
    commission      REAL DEFAULT 0.0,
    slippage        REAL DEFAULT 0.0,
    atm_template    TEXT,
    imported_at     TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id),
    FOREIGN KEY (forward_test_id) REFERENCES forward_tests(forward_test_id)
);
CREATE INDEX IF NOT EXISTS idx_nt8_trades_strategy ON nt8_trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_nt8_trades_symbol ON nt8_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_nt8_trades_entry ON nt8_trades(entry_time);
```

#### `nt8_account_snapshots`
Time-series of account state from NT8.

```sql
CREATE TABLE IF NOT EXISTS nt8_account_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    equity                  REAL NOT NULL,
    daily_pnl               REAL,
    daily_pnl_pct           REAL,
    open_drawdown           REAL,
    trailing_drawdown_used  REAL,
    trailing_drawdown_limit REAL,
    daily_loss_limit        REAL,
    active_strategy_id      TEXT,
    snapshot_at             TEXT NOT NULL,
    imported_at             TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_nt8_snapshots_account ON nt8_account_snapshots(account_id);
CREATE INDEX IF NOT EXISTS idx_nt8_snapshots_time ON nt8_account_snapshots(snapshot_at);
```

### Existing Table Extensions

| Table | Addition | Reason |
|---|---|---|
| `strategy_specs` | No change needed | `status` field covers pipeline stages |
| `backtests` | No change needed | All metrics already present |
| `forward_tests` | Add `nt8_trade_count`, `nt8_last_sync` | Track NT8 import state |
| `research_notes` | No change needed | Used for NT8 violation events |

---

## 7. API Requirements

### Architecture Decision

The current dashboard reads a single static JSON file (`dashboard_state.json`). To serve the five panels with real data, a lightweight local API server is required.

**Recommended**: Python `http.server` + SQLite — no framework dependencies beyond stdlib.  
**Alternative**: FastAPI if async or schema validation becomes necessary later.

### Endpoint Map

| Endpoint | Method | Returns | Used By |
|---|---|---|---|
| `/api/state` | GET | Aggregate counts (replaces dashboard_state.json) | Firm Health, DB Status |
| `/api/queue` | GET | All active strategy specs with stage + latest backtest PF | Strategy Queue panel |
| `/api/rankings` | GET | Scored strategies sorted by composite_score | Research Rankings panel |
| `/api/prop-firm` | GET | Approved strategies evaluated against active profile | Prop Firm panel |
| `/api/prop-firm/profiles` | GET | List of saved prop firm profiles | Profile dropdown |
| `/api/pipeline` | GET | Stage counts, daily activity, recent notes | Pipeline Status panel |
| `/api/nt8/status` | GET | Latest account snapshot + today's trade summary | NT8 panel |
| `/api/nt8/trades` | GET | Recent NT8 trades (last 50) | NT8 trade feed |
| `/api/sync` | POST | Trigger `sync_queue.py` run | Refresh button |

### Server Location
`database/api_server.py` — runs on `localhost:7433`

### Request/Response Pattern
All endpoints return JSON. Dashboard JS replaces `fetch("reports/dashboard_state.json")` with `fetch("http://localhost:7433/api/state")`. CORS header: `Access-Control-Allow-Origin: null` (local file:// origin).

### Data Flow

```
dashboard.html (browser, file://)
    ↓ fetch()
localhost:7433/api/*
    ↓ sqlite3 query
hermes_research.db
    ↑ written by
sync_queue.py       (strategy specs → db)
nt8_sync.py         (NT8 trades → db)
pipeline_demo.py    (engine runs → scoring_results)
```

---

## 8. Recommended Implementation Order

Work proceeds in dependency order — each step is independently testable before the next begins.

### Phase 1 — Data Foundation (do first)
**Goal**: Replace mock data with real database queries.

| Step | Task | File(s) |
|---|---|---|
| 1.1 | Add `scoring_results` table to `init.sql` | `database/init.sql` |
| 1.2 | Add `prop_firm_profiles` table to `init.sql` | `database/init.sql` |
| 1.3 | Update `sync_queue.py` to write full strategy list (not just counts) to `dashboard_state.json` | `database/sync_queue.py` |
| 1.4 | Write scoring results to DB after each `score_strategy()` call in `pipeline_demo.py` | `research/pipeline_demo/pipeline_demo.py` |

### Phase 2 — API Server (do second)
**Goal**: Replace static JSON with live query endpoints.

| Step | Task | File(s) |
|---|---|---|
| 2.1 | Build `database/api_server.py` with `/api/state` and `/api/queue` | `database/api_server.py` |
| 2.2 | Add `/api/rankings` endpoint | `database/api_server.py` |
| 2.3 | Add `/api/pipeline` endpoint | `database/api_server.py` |
| 2.4 | Update `dashboard.js` to fetch from `localhost:7433` with fallback to mock | `dashboard/dashboard.js` |

### Phase 3 — Strategy Queue Panel (do third)
**Goal**: Replace mock queue list with live `strategy_specs` query.

| Step | Task |
|---|---|
| 3.1 | Wire `renderQueue()` to `/api/queue` |
| 3.2 | Add stage badge coloring + stale detection |
| 3.3 | Add click-to-expand inline detail card |

### Phase 4 — Research Rankings Panel (do fourth)
**Goal**: Add new panel surfacing composite scores.

| Step | Task |
|---|---|
| 4.1 | Add Rankings panel card to `dashboard.html` |
| 4.2 | Write `renderRankings()` in `dashboard.js` |
| 4.3 | Wire to `/api/rankings` |
| 4.4 | Add sortable columns + grade color coding |

### Phase 5 — Pipeline Status Panel (do fifth)
**Goal**: Replace basic Database Status widget with full stage tracker.

| Step | Task |
|---|---|
| 5.1 | Redesign Database Status card in `dashboard.html` → Pipeline Status |
| 5.2 | Write `renderPipelineStatus()` |
| 5.3 | Wire to `/api/pipeline` |
| 5.4 | Add recent activity feed from `research_notes` |

### Phase 6 — Prop Firm Candidate Panel (do sixth)
**Goal**: Add new panel evaluating strategies against account constraints.

| Step | Task |
|---|---|
| 6.1 | Add `prop_firm_profiles` seed data (Apex, Topstep, FTMO defaults) |
| 6.2 | Add Prop Firm panel card to `dashboard.html` |
| 6.3 | Write eligibility evaluation logic in `api_server.py` → `/api/prop-firm` |
| 6.4 | Write `renderPropFirm()` in `dashboard.js` |
| 6.5 | Add profile switcher dropdown |

### Phase 7 — NT8 Integration (do last)
**Goal**: Stream live NT8 execution data into the dashboard.

| Step | Task |
|---|---|
| 7.1 | Add `nt8_trades` and `nt8_account_snapshots` tables to `init.sql` |
| 7.2 | Create `nt8_export/` directory with `.gitignore` exclusion |
| 7.3 | Write `database/nt8_sync.py` file watcher + importer |
| 7.4 | Add `/api/nt8/status` and `/api/nt8/trades` endpoints to `api_server.py` |
| 7.5 | Add NT8 panel card to `dashboard.html` |
| 7.6 | Write `renderNT8Panel()` in `dashboard.js` |
| 7.7 | Test with Sim101 account export from NT8 ATM Manager |

---

## Dependency Map

```
Phase 1 (Data Foundation)
  └── Phase 2 (API Server)
        ├── Phase 3 (Strategy Queue)    — can start once /api/queue exists
        ├── Phase 4 (Rankings)          — requires scoring_results populated
        ├── Phase 5 (Pipeline Status)   — requires /api/pipeline
        └── Phase 6 (Prop Firm)         — requires prop_firm_profiles + /api/prop-firm
              └── Phase 7 (NT8)         — independent of Phase 6, requires nt8_sync.py
```

Phases 3–6 can be parallelized once Phase 2 is complete.  
Phase 7 (NT8) can be started independently of Phases 3–6 at any point after Phase 1.

---

*Plan only. No dashboard code has been written. Awaiting implementation authorization.*
