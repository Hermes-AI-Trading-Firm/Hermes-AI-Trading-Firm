"""SQLite query functions — one per API endpoint.

Each function accepts an open sqlite3.Connection and returns a
plain Python dict ready for JSON serialisation. All functions are
read-only and handle empty / missing tables gracefully.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rows(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
    if not cur.description:
        return []
    cols = [d[0] for d in cur.description]
    return [{cols[i]: row[i] for i in range(len(cols))} for row in cur.fetchall()]


def _one(cur: sqlite3.Cursor) -> Optional[Dict[str, Any]]:
    if not cur.description:
        return None
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def health(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        spec_count = conn.execute("SELECT COUNT(*) FROM strategy_specs").fetchone()[0]
        reachable = True
    except Exception as exc:
        return {"status": "error", "db_reachable": False, "error": str(exc), "timestamp": _now()}

    return {
        "status": "ok",
        "db_reachable": reachable,
        "table_count": table_count,
        "strategy_specs": spec_count,
        "timestamp": _now(),
    }


# ---------------------------------------------------------------------------
# /strategy-queue
# ---------------------------------------------------------------------------

def strategy_queue(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        cur = conn.execute("""
            SELECT
                ss.spec_id,
                ss.spec_name                                                       AS name,
                ss.asset_class,
                ss.symbol,
                ss.timeframe,
                ss.status                                                          AS stage,
                CAST((julianday('now') - julianday(ss.updated_at)) AS INTEGER)    AS days_in_stage,
                b.profit_factor,
                b.sharpe_ratio,
                b.max_drawdown_pct,
                b.total_trades,
                b.win_rate
            FROM strategy_specs ss
            LEFT JOIN backtests b ON b.backtest_id = (
                SELECT MAX(backtest_id) FROM backtests WHERE spec_id = ss.spec_id
            )
            WHERE ss.status NOT IN ('approved', 'rejected')
            ORDER BY ss.updated_at DESC
        """)
        items = _rows(cur)
    except Exception as exc:
        return {"error": str(exc), "count": 0, "items": []}
    return {"count": len(items), "items": items}


# ---------------------------------------------------------------------------
# /research-rankings
# ---------------------------------------------------------------------------

def research_rankings(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        cur = conn.execute("""
            SELECT
                sr.spec_id,
                ss.spec_name            AS name,
                ss.asset_class,
                ss.symbol,
                ss.timeframe,
                sr.composite_score,
                sr.grade,
                sr.recommendation,
                sr.profitability_score,
                sr.drawdown_score,
                sr.consistency_score,
                sr.walk_forward_score,
                sr.monte_carlo_score,
                sr.regime_score,
                sr.robustness_score,
                sr.overfitting_risk,
                sr.monte_carlo_pass,
                sr.walk_forward_pass,
                sr.prop_firm_supported,
                sr.scored_at,
                b.profit_factor,
                b.sharpe_ratio,
                b.max_drawdown_pct,
                b.win_rate,
                b.total_trades
            FROM scoring_results sr
            JOIN strategy_specs ss ON ss.spec_id = sr.spec_id
            LEFT JOIN backtests b ON b.backtest_id = (
                SELECT MAX(backtest_id) FROM backtests WHERE spec_id = sr.spec_id
            )
            ORDER BY
                CASE WHEN sr.composite_score IS NULL THEN 1 ELSE 0 END,
                sr.composite_score DESC,
                sr.scored_at DESC
        """)
        items = _rows(cur)
        for rank, item in enumerate(items, 1):
            item["rank"] = rank
    except Exception as exc:
        return {"error": str(exc), "count": 0, "items": []}
    return {"count": len(items), "items": items}


# ---------------------------------------------------------------------------
# /prop-firm-candidates
# ---------------------------------------------------------------------------

def prop_firm_candidates(
    conn: sqlite3.Connection,
    profile_id: Optional[int] = None,
) -> Dict[str, Any]:
    try:
        # Load the requested (or first active) profile
        if profile_id is not None:
            cur = conn.execute(
                "SELECT * FROM prop_firm_profiles WHERE profile_id = ? AND is_active = 1",
                (profile_id,),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM prop_firm_profiles WHERE is_active = 1 ORDER BY profile_id LIMIT 1"
            )
        profile = _one(cur)
        if profile is None:
            return {"profile": None, "count": 0, "items": [],
                    "message": "No active prop firm profiles found."}

        dd_limit = profile["trailing_drawdown_limit"]
        daily_limit = profile["daily_loss_limit"] or 1.0

        # Approved strategies with latest scoring and backtest metrics
        cur = conn.execute("""
            SELECT
                a.approved_strategy_id,
                a.strategy_name         AS name,
                a.asset_class,
                a.symbol,
                a.timeframe,
                a.status,
                a.expected_max_drawdown,
                sr.composite_score,
                sr.grade,
                sr.prop_firm_supported,
                b.profit_factor,
                b.max_drawdown_pct,
                b.sharpe_ratio,
                b.max_consecutive_losses
            FROM approved_strategies a
            LEFT JOIN scoring_results sr ON sr.scoring_id = (
                SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = a.spec_id
            )
            LEFT JOIN backtests b ON b.backtest_id = (
                SELECT MAX(backtest_id) FROM backtests WHERE spec_id = a.spec_id
            )
            ORDER BY
                CASE WHEN sr.composite_score IS NULL THEN 1 ELSE 0 END,
                sr.composite_score DESC
        """)
        items = _rows(cur)

        # Evaluate eligibility per strategy against the loaded profile
        for item in items:
            mdd = item.get("max_drawdown_pct")
            if mdd is not None:
                dd_frac = abs(mdd) / 100.0
                item["dd_within_limit"] = dd_frac <= dd_limit
            else:
                item["dd_within_limit"] = None
            item["eligible"] = item["dd_within_limit"]
            item["profile_id"] = profile["profile_id"]

    except Exception as exc:
        return {"error": str(exc), "profile": None, "count": 0, "items": []}

    return {
        "profile": profile,
        "count": len(items),
        "items": items,
    }


# ---------------------------------------------------------------------------
# /pipeline-status
# ---------------------------------------------------------------------------

def pipeline_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        spec_stages: Dict[str, int] = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) FROM strategy_specs GROUP BY status"
        ):
            spec_stages[row[0]] = row[1]

        idea_stages: Dict[str, int] = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) FROM strategy_ideas GROUP BY status"
        ):
            idea_stages[row[0]] = row[1]

        def _count(table: str) -> int:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        today_bt = conn.execute(
            "SELECT COUNT(*) FROM backtests WHERE date(created_at) = date('now')"
        ).fetchone()[0]
        today_regime = conn.execute(
            "SELECT COUNT(*) FROM regime_analysis WHERE date(created_at) = date('now')"
        ).fetchone()[0]

    except Exception as exc:
        return {"error": str(exc)}

    return {
        "spec_stage_counts": spec_stages,
        "idea_stage_counts": idea_stages,
        "totals": {
            "ideas":          _count("strategy_ideas"),
            "specs":          _count("strategy_specs"),
            "backtests":      _count("backtests"),
            "regime_analyses":_count("regime_analysis"),
            "optimizations":  _count("optimizations"),
            "scored":         _count("scoring_results"),
            "approved":       _count("approved_strategies"),
            "rejected":       _count("rejected_strategies"),
        },
        "today": {
            "backtests":       today_bt,
            "regime_analyses": today_regime,
        },
        "timestamp": _now(),
    }


# ---------------------------------------------------------------------------
# /nt8-trades
# ---------------------------------------------------------------------------

def nt8_trades(conn: sqlite3.Connection, limit: int = 50) -> Dict[str, Any]:
    try:
        cur = conn.execute("""
            SELECT
                nt8_trade_id,
                strategy_id,
                account_id,
                symbol,
                direction,
                entry_time,
                exit_time,
                entry_price,
                exit_price,
                quantity,
                pnl,
                commission,
                slippage,
                atm_template,
                imported_at
            FROM nt8_trades
            ORDER BY entry_time DESC
            LIMIT ?
        """, (limit,))
        items = _rows(cur)
    except Exception as exc:
        return {"error": str(exc), "count": 0, "items": []}
    return {
        "count": len(items),
        "items": items,
        "message": "No NT8 trades imported yet." if not items else None,
    }


# ---------------------------------------------------------------------------
# /nt8-account
# ---------------------------------------------------------------------------

def nt8_account(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        cur = conn.execute("""
            SELECT
                snapshot_id,
                account_id,
                equity,
                daily_pnl,
                daily_pnl_pct,
                open_drawdown,
                trailing_drawdown_used,
                trailing_drawdown_limit,
                daily_loss_limit,
                active_strategy_id,
                snapshot_at,
                imported_at
            FROM nt8_account_snapshots
            ORDER BY snapshot_at DESC
            LIMIT 1
        """)
        snapshot = _one(cur)
    except Exception as exc:
        return {"error": str(exc), "snapshot": None}
    return {
        "snapshot": snapshot,
        "message": "No NT8 account data imported yet." if snapshot is None else None,
    }


# ---------------------------------------------------------------------------
# /activity-feed
# ---------------------------------------------------------------------------

def activity_feed(conn: sqlite3.Connection, limit: int = 20) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    try:
        # Research notes
        cur = conn.execute("""
            SELECT note_type AS type, content AS message, tags, created_at
            FROM research_notes
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
        for row in cur.fetchall():
            events.append({
                "source":     "research_notes",
                "type":       row[0],
                "message":    row[1],
                "tags":       _parse_json(row[2]),
                "created_at": row[3],
            })

        # Rejections
        cur = conn.execute("""
            SELECT
                strategy_name || ' rejected at ' || rejection_stage AS message,
                rejection_reason AS detail,
                archived_at      AS created_at
            FROM rejected_strategies
            ORDER BY archived_at DESC
            LIMIT ?
        """, (limit,))
        for row in cur.fetchall():
            events.append({
                "source":     "rejected_strategies",
                "type":       "rejection",
                "message":    row[0],
                "detail":     row[1],
                "created_at": row[2],
            })

        # Approvals
        cur = conn.execute("""
            SELECT
                strategy_name || ' approved' AS message,
                approval_reason              AS detail,
                created_at
            FROM approved_strategies
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
        for row in cur.fetchall():
            events.append({
                "source":     "approved_strategies",
                "type":       "approval",
                "message":    row[0],
                "detail":     row[1],
                "created_at": row[2],
            })

        events.sort(key=lambda e: e.get("created_at") or "", reverse=True)
        events = events[:limit]

    except Exception as exc:
        return {"error": str(exc), "count": 0, "items": []}

    return {"count": len(events), "items": events}
