"""HTTP request handler for the Hermes AI API server.

Routes inbound GET requests to query functions in queries.py and
returns JSON with CORS headers so the dashboard can fetch from file://.
"""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

import queries as q

# Set by run_api.py before the server starts.
DB_PATH: Path = Path("database/hermes_research.db")


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Route table  path → lambda(conn, params) → dict
# ---------------------------------------------------------------------------

ROUTES: Dict[str, Callable] = {
    "/health": lambda conn, _p: q.health(conn),
    "/strategy-queue": lambda conn, _p: q.strategy_queue(conn),
    "/research-rankings": lambda conn, _p: q.research_rankings(conn),
    "/prop-firm-candidates": lambda conn, p: q.prop_firm_candidates(
        conn, _int((p.get("profile_id") or [None])[0])
    ),
    "/pipeline-status": lambda conn, _p: q.pipeline_status(conn),
    "/nt8-trades": lambda conn, p: q.nt8_trades(
        conn, _int((p.get("limit") or [50])[0]) or 50
    ),
    "/nt8-account": lambda conn, _p: q.nt8_account(conn),
    "/activity-feed": lambda conn, p: q.activity_feed(
        conn, _int((p.get("limit") or [20])[0]) or 20
    ),
    "/strategy-attribution": lambda conn, _p: q.strategy_attribution(conn),
    "/compliance-status":    lambda conn, _p: q.compliance_status(conn),
    "/equity-curve":         lambda conn, _p: q.equity_curve(conn),
    "/performance-summary":  lambda conn, _p: q.performance_summary(conn),
}


class HermesAPIHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        if path == "/":
            self._json({
                "name": "Hermes AI API",
                "port": 7433,
                "endpoints": list(ROUTES.keys()),
            })
            return

        handler = ROUTES.get(path)
        if handler is None:
            self._json(
                {"error": "Not found", "path": path, "available": list(ROUTES)},
                status=404,
            )
            return

        conn = sqlite3.connect(str(DB_PATH))
        try:
            result = handler(conn, params)
            self._json(result)
        except Exception as exc:
            self._json({"error": str(exc)}, status=500)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"  [{self.address_string()}] {fmt % args}")
