#!/usr/bin/env python3
"""Start the Hermes AI API server on localhost:7433.

Usage (from project root):
    python api/run_api.py

The server is read-only. It runs init.sql on startup to ensure all
Phase 1 tables exist in hermes_research.db before accepting requests.
"""

from __future__ import annotations

import sqlite3
import sys
from http.server import HTTPServer
from pathlib import Path

# Allow `import queries` and `import server` from this directory.
_API_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _API_DIR.parent
sys.path.insert(0, str(_API_DIR))
sys.path.insert(0, str(_PROJECT_ROOT))  # allows `from research.risk import …`

import server  # noqa: E402

HOST = "localhost"
PORT = 7433
DB_PATH = _PROJECT_ROOT / "database" / "hermes_research.db"
INIT_SQL = _PROJECT_ROOT / "database" / "init.sql"


def _ensure_schema() -> None:
    """Apply init.sql to hermes_research.db.

    All CREATE statements use IF NOT EXISTS so this is safe to re-run
    against a populated database — existing data is never touched.
    """
    sql = INIT_SQL.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    print("Hermes AI Trading Firm — API Server")
    print(f"  DB     : {DB_PATH}")
    print(f"  Listen : http://{HOST}:{PORT}")
    print()

    if not DB_PATH.parent.exists():
        print(f"ERROR: database directory not found: {DB_PATH.parent}")
        sys.exit(1)

    print("Applying schema (IF NOT EXISTS) …")
    _ensure_schema()

    # Verify table count after migration
    conn = sqlite3.connect(str(DB_PATH))
    n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    conn.close()
    print(f"Schema OK — {n} tables ready.")
    print()

    # Wire DB path into request handler
    server.DB_PATH = DB_PATH

    print("Endpoints:")
    for path in server.ROUTES:
        print(f"  GET  http://{HOST}:{PORT}{path}")
    print()

    httpd = HTTPServer((HOST, PORT), server.HermesAPIHandler)
    print(f"Listening on http://{HOST}:{PORT}  (Ctrl+C to stop)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
