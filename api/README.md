# Hermes AI API Server

Local read-only JSON API for the Hermes AI Trading Firm dashboard.  
**stdlib only** — no framework dependencies. **Port 7433.**

---

## Start

```bash
# From project root:
python api/run_api.py
```

On startup the server:
1. Runs `database/init.sql` against `hermes_research.db` (safe, idempotent — all tables use `IF NOT EXISTS`)
2. Prints table count and all endpoint URLs
3. Serves requests until `Ctrl+C`

---

## Endpoints

All endpoints are `GET`. All return `application/json` with CORS headers (`Access-Control-Allow-Origin: *`) so the dashboard can fetch from `file://`.

| Endpoint | Description |
|---|---|
| `/` | Endpoint index |
| `/health` | Server and DB health check |
| `/strategy-queue` | Active strategy specs with current stage and latest backtest metrics |
| `/research-rankings` | Strategies ranked by composite score from `scoring_results` |
| `/prop-firm-candidates` | Approved strategies evaluated against a prop firm account profile |
| `/pipeline-status` | Stage counts, totals, and today's activity summary |
| `/nt8-trades` | Recent NT8 closed trades (up to 50) |
| `/nt8-account` | Latest NT8 account state snapshot |
| `/activity-feed` | Unified feed of research notes, rejections, and approvals |

---

## Query Parameters

| Endpoint | Parameter | Default | Description |
|---|---|---|---|
| `/prop-firm-candidates` | `profile_id` | First active profile | ID from `prop_firm_profiles` |
| `/nt8-trades` | `limit` | `50` | Maximum rows returned |
| `/activity-feed` | `limit` | `20` | Maximum events returned |

Example:
```
GET http://localhost:7433/prop-firm-candidates?profile_id=2
GET http://localhost:7433/nt8-trades?limit=10
GET http://localhost:7433/activity-feed?limit=5
```

---

## Response Shapes

### `/health`
```json
{
  "status": "ok",
  "db_reachable": true,
  "table_count": 15,
  "strategy_specs": 4,
  "timestamp": "2026-06-13T17:00:00+00:00"
}
```

### `/strategy-queue`
```json
{
  "count": 4,
  "items": [
    {
      "spec_id": 1,
      "name": "MNQ_ORB_FVG_v001",
      "asset_class": "futures",
      "symbol": "MNQ",
      "timeframe": "5m",
      "stage": "backtesting",
      "days_in_stage": 0,
      "profit_factor": null,
      "sharpe_ratio": null,
      "max_drawdown_pct": null,
      "total_trades": null,
      "win_rate": null
    }
  ]
}
```

### `/research-rankings`
```json
{
  "count": 0,
  "items": []
}
```
*(Populated after `score_strategy()` writes to `scoring_results`.)*

### `/prop-firm-candidates`
```json
{
  "profile": {
    "profile_id": 1,
    "firm_name": "Apex",
    "account_label": "50K",
    "account_size": 50000.0,
    "trailing_drawdown_limit": 0.08,
    "daily_loss_limit": 0.02,
    "profit_target": 0.10
  },
  "count": 0,
  "items": []
}
```
*(Populated after strategies reach `approved_strategies`.)*

### `/pipeline-status`
```json
{
  "spec_stage_counts": { "backtesting": 4 },
  "idea_stage_counts": { "spec_created": 4 },
  "totals": {
    "ideas": 4, "specs": 4, "backtests": 0,
    "regime_analyses": 0, "optimizations": 0,
    "scored": 0, "approved": 0, "rejected": 0
  },
  "today": { "backtests": 0, "regime_analyses": 0 },
  "timestamp": "2026-06-13T17:00:00+00:00"
}
```

### `/nt8-trades`
```json
{
  "count": 0,
  "items": [],
  "message": "No NT8 trades imported yet."
}
```

### `/nt8-account`
```json
{
  "snapshot": null,
  "message": "No NT8 account data imported yet."
}
```

### `/activity-feed`
```json
{
  "count": 19,
  "items": [
    {
      "source": "research_notes",
      "type": "observation",
      "message": "Database initialized with core schema ...",
      "tags": ["database", "initialization"],
      "created_at": "2026-06-13T04:51:18"
    }
  ]
}
```

---

## Error Responses

```json
{ "error": "description of what went wrong" }
```

HTTP status codes: `200 OK`, `404 Not Found`, `500 Internal Server Error`.

---

## Architecture

```
run_api.py   — entry point: schema migration + HTTPServer on :7433
server.py    — BaseHTTPRequestHandler: routing, CORS, JSON serialisation
queries.py   — one function per endpoint, all read-only SQLite queries
```

Database connection is opened fresh per request and closed immediately after. No connection pooling required for local single-user use.
