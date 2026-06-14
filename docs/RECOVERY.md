# Hermes AI Trading Firm — Recovery Procedures

## API Server

### Start
```powershell
cd C:\Users\ebo13\Hermes-AI-Trading-Firm
python api/run_api.py
```
Server starts on `http://localhost:7433`. Leave the terminal open.

### Verify
```powershell
Invoke-RestMethod http://localhost:7433/health
```
Expected: `{ "status": "ok", ... }`

### Restart (kill stale process)
```powershell
Get-Process -Name python | Stop-Process -Force
python api/run_api.py
```

### Port already in use
```powershell
netstat -ano | findstr :7433
taskkill /PID <pid> /F
python api/run_api.py
```

---

## Dashboard

Open `dashboard/dashboard.html` directly in a browser (`file://`). No build step required.

If panels show stale data, click **Refresh** or reload the page after restarting the API server.

---

## Database

### Location
```
database/hermes_research.db
```

### Inspect
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('database/hermes_research.db')
for t in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall():
    n = conn.execute(f'SELECT COUNT(*) FROM {t[0]}').fetchone()[0]
    print(f'{t[0]:40s} {n} rows')
conn.close()
"
```

### Re-initialise schema (non-destructive)
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('database/hermes_research.db')
with open('database/init.sql') as f:
    conn.executescript(f.read())
conn.close()
print('Schema applied.')
"
```
All `CREATE TABLE IF NOT EXISTS` — safe to re-run; existing data is preserved.

### Backup before destructive operations
```powershell
Copy-Item database\hermes_research.db database\hermes_research.db.bak
```

---

## NT8 Trade Import

### Import a CSV export from NinjaTrader
```powershell
python -c "
from nt8_connector.importer import import_trades_from_csv
import sqlite3
conn = sqlite3.connect('database/hermes_research.db')
result = import_trades_from_csv(conn, 'nt8_export/your_export.csv')
print(result)
conn.close()
"
```

Place NT8 export files under `nt8_export/` (gitignored except `.gitkeep`).

---

## Scoring Pipeline

### Re-score all strategies
```powershell
python -c "
import sqlite3
from research.scoring.runner import run_batch
from research.scoring.scoring import ScoringInput

conn = sqlite3.connect('database/hermes_research.db')
# Build ScoringInput list with real backtest data, then:
# summary = run_batch(inputs, conn=conn, save=True)
# summary.print_summary()
conn.close()
"
```

### Dry run (no DB write)
```powershell
python -c "
import sqlite3
from research.scoring.runner import run_from_db
conn = sqlite3.connect('database/hermes_research.db')
summary = run_from_db(conn, save=False)
summary.print_summary()
conn.close()
"
```

---

## Reporting

### Regenerate all strategy reports
```powershell
python -c "
import sqlite3
from research.reporting.report_generator import generate_all_reports
from research.reporting.exporter import export_all
conn = sqlite3.connect('database/hermes_research.db')
reports = generate_all_reports(conn)
paths = export_all(reports)
conn.close()
print(paths['summary']['md'])
"
```

Output written to `reports/strategies/` and `reports/summaries/`.

---

## Compliance Check

### Run compliance evaluation
```powershell
python -c "
import sqlite3
from research.risk.compliance import run_full_compliance
conn = sqlite3.connect('database/hermes_research.db')
result = run_full_compliance(conn)
print('Health:', result['firm_health_score'])
print('Status:', result['firm_status'])
conn.close()
"
```

---

## Git Recovery

### Check state
```powershell
git status
git log --oneline -10
```

### Discard unstaged changes to a file
```powershell
git checkout -- <file>
```

### Restore to last commit
```powershell
git stash        # saves in-progress work
git stash pop    # restores it
```

### Reset to remote (destructive — confirm first)
```powershell
git fetch origin
git reset --hard origin/master
```

---

## Environment

| Item | Value |
|------|-------|
| Python | 3.11+ |
| DB engine | SQLite via stdlib `sqlite3` |
| API port | 7433 |
| Project root | `C:\Users\ebo13\Hermes-AI-Trading-Firm` |
| Tag | `v1.0-research-pipeline` |
| Remote | `https://github.com/Hermes-AI-Trading-Firm/Hermes-AI-Trading-Firm.git` |

No external Python packages required. All dependencies are stdlib.
