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
# Scoring deduplication pattern
#
# scoring_results is append-only history: every scoring run adds a new row.
# Display queries (rankings, prop-firm, decision queue) must show ONE row per
# strategy — the latest result only.
#
# Canonical filter used in every display query:
#
#   WHERE sr.scoring_id = (
#       SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = sr.spec_id
#   )
#
# The full history is preserved for audit and trend analysis and is never
# deleted.  Use latest_scoring_results() to surface the deduplicated view.
# ---------------------------------------------------------------------------

def latest_scoring_results(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Return the most recent scoring row per spec_id.

    scoring_results is append-only history — use this query for any display
    that should show one row per strategy.  Full history remains in the table.
    """
    try:
        cur = conn.execute("""
            SELECT
                sr.scoring_id,
                sr.spec_id,
                ss.spec_name    AS name,
                sr.composite_score,
                sr.grade,
                sr.recommendation,
                sr.scored_at
            FROM scoring_results sr
            JOIN strategy_specs ss ON ss.spec_id = sr.spec_id
            WHERE sr.scoring_id = (
                SELECT MAX(scoring_id)
                FROM scoring_results
                WHERE spec_id = sr.spec_id
            )
            ORDER BY sr.composite_score DESC
        """)
        items = _rows(cur)
    except Exception as exc:
        return {"error": str(exc), "count": 0, "items": []}
    return {"count": len(items), "items": items}


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
        # Latest score per spec only — scoring_results is append-only history.
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
            WHERE sr.scoring_id = (
                SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = sr.spec_id
            )
            ORDER BY
                CASE WHEN sr.composite_score IS NULL THEN 1 ELSE 0 END,
                sr.composite_score DESC
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

        # Top 10 — latest score per spec only, ordered by prop-firm suitability then score.
        cur = conn.execute("""
            SELECT
                ss.spec_id,
                ss.spec_name            AS name,
                ss.asset_class,
                ss.symbol,
                ss.timeframe,
                sr.composite_score,
                sr.grade,
                sr.prop_firm_supported,
                sr.drawdown_score,
                b.profit_factor,
                b.max_drawdown_pct,
                b.sharpe_ratio
            FROM scoring_results sr
            JOIN strategy_specs ss ON ss.spec_id = sr.spec_id
            LEFT JOIN backtests b ON b.backtest_id = (
                SELECT MAX(backtest_id) FROM backtests WHERE spec_id = sr.spec_id
            )
            WHERE sr.scoring_id = (
                SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = sr.spec_id
            )
            ORDER BY
                sr.prop_firm_supported DESC,
                sr.composite_score DESC
            LIMIT 10
        """)
        items = _rows(cur)

        # Evaluate DD eligibility against the loaded profile
        for rank, item in enumerate(items, 1):
            item["rank"] = rank
            item["profile_id"] = profile["profile_id"]
            mdd = item.get("max_drawdown_pct")
            if mdd is not None:
                item["dd_within_limit"] = abs(mdd) <= (dd_limit * 100)
                item["eligible"] = item["dd_within_limit"]
            else:
                item["dd_within_limit"] = None
                item["eligible"] = bool(item.get("prop_firm_supported"))

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


# ---------------------------------------------------------------------------
# /strategy-attribution
# ---------------------------------------------------------------------------

def strategy_attribution(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        cur = conn.execute("""
            SELECT
                t.strategy_id,
                ss.spec_id,
                ss.asset_class,
                ss.symbol,
                ss.timeframe,
                COUNT(*)                                                          AS trade_count,
                ROUND(SUM(t.pnl), 2)                                             AS total_pnl,
                SUM(CASE WHEN t.pnl > 0  THEN 1 ELSE 0 END)                     AS wins,
                SUM(CASE WHEN t.pnl <= 0 THEN 1 ELSE 0 END)                     AS losses,
                ROUND(SUM(CASE WHEN t.pnl > 0 THEN t.pnl  ELSE 0   END), 2)    AS gross_profit,
                ROUND(SUM(CASE WHEN t.pnl < 0 THEN ABS(t.pnl) ELSE 0 END), 2)  AS gross_loss,
                ROUND(AVG(CASE WHEN t.pnl > 0 THEN t.pnl END), 2)              AS avg_win,
                ROUND(AVG(CASE WHEN t.pnl < 0 THEN t.pnl END), 2)              AS avg_loss,
                ROUND(MAX(t.pnl), 2)                                              AS best_trade,
                ROUND(MIN(t.pnl), 2)                                              AS worst_trade,
                ROUND(SUM(t.commission), 2)                                       AS total_commission,
                MAX(t.entry_time)                                                 AS last_trade_time
            FROM nt8_trades t
            LEFT JOIN strategy_specs ss ON ss.spec_name = t.strategy_id
            GROUP BY t.strategy_id
            ORDER BY total_pnl DESC
        """)
        items = _rows(cur)
    except Exception as exc:
        return {"error": str(exc), "count": 0, "items": []}

    for item in items:
        tc   = item.get("trade_count") or 0
        wins = item.get("wins") or 0
        item["win_rate"]      = round(wins / tc * 100, 1) if tc else 0.0
        gp = item.get("gross_profit") or 0.0
        gl = item.get("gross_loss")   or 0.0
        item["profit_factor"] = round(gp / gl, 2) if gl else None
        item["net_pnl"]       = round(
            (item.get("total_pnl") or 0.0) - (item.get("total_commission") or 0.0), 2
        )

    return {"count": len(items), "items": items}


# ---------------------------------------------------------------------------
# /compliance-status
# ---------------------------------------------------------------------------

def compliance_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Run full compliance check via research.risk and return a dashboard-ready
    dict. Deduplicates accounts by (firm_name, account_label) to handle
    duplicate seed rows in prop_firm_profiles.
    """
    _empty: Dict[str, Any] = {
        "firm_health_score": None,
        "firm_status": "UNKNOWN",
        "account_id": None,
        "snapshot_at": None,
        "account_count": 0,
        "accounts": [],
        "strategy_count": 0,
        "strategies": [],
    }

    try:
        from research.risk.compliance import run_full_compliance  # type: ignore
    except ImportError as exc:
        return {**_empty, "error": f"research.risk not importable: {exc}"}

    try:
        report = run_full_compliance(conn)
    except Exception as exc:
        return {**_empty, "error": str(exc)}

    # Deduplicate accounts (prop_firm_profiles seed inserts 3 × each firm)
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for acct in report.get("accounts", []):
        key = (acct.get("firm_name"), acct.get("account_label"))
        if key not in seen:
            seen.add(key)
            deduped.append(acct)

    # Enrich with dashboard-friendly derived fields
    for acct in deduped:
        dd_used  = acct.get("dd_used")  or 0.0
        dd_limit = acct.get("dd_limit") or 1.0
        acct["remaining_drawdown"] = round(dd_limit - dd_used, 2)
        acct["dd_used_pct"]        = round(dd_used / dd_limit * 100, 1) if dd_limit else 0.0

    # Latest snapshot metadata
    snapshot_at: Optional[str] = None
    account_id:  Optional[str] = None
    try:
        cur = conn.execute("""
            SELECT account_id, snapshot_at
            FROM nt8_account_snapshots
            ORDER BY snapshot_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            account_id, snapshot_at = row[0], row[1]
    except Exception:
        pass

    return {
        "firm_health_score": report.get("firm_health_score"),
        "firm_status":       report.get("firm_status", "UNKNOWN"),
        "account_id":        account_id,
        "snapshot_at":       snapshot_at,
        "account_count":     len(deduped),
        "accounts":          deduped,
        "strategy_count":    report.get("strategy_count", 0),
        "strategies":        report.get("strategies", []),
    }


# ---------------------------------------------------------------------------
# /equity-curve
# ---------------------------------------------------------------------------

def _load_trades_for_analytics(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Shared helper — loads nt8_trades ordered by exit_time (close time)."""
    cur = conn.execute("""
        SELECT
            COALESCE(exit_time, entry_time) AS entry_time,
            pnl,
            commission,
            symbol,
            direction,
            strategy_id
        FROM nt8_trades
        ORDER BY COALESCE(exit_time, entry_time) ASC, entry_time ASC
    """)
    cols = [d[0] for d in cur.description]
    return [{cols[i]: r[i] for i in range(len(cols))} for r in cur.fetchall()]


def equity_curve(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        trades = _load_trades_for_analytics(conn)
    except Exception as exc:
        return {"error": str(exc), "count": 0, "items": [], "summary": None}

    if not trades:
        return {"count": 0, "items": [], "summary": None}

    try:
        from research.analytics.equity import build_equity_curve as _build_eq  # type: ignore
        from research.analytics.equity import build_drawdown_curve as _build_dd
        from research.analytics.performance import calculate_max_drawdown as _max_dd
    except ImportError as exc:
        return {"error": str(exc), "count": 0, "items": [], "summary": None}

    curve = _build_dd(_build_eq(trades))
    dd    = _max_dd(curve)

    return {
        "count": len(curve),
        "items": curve,
        "summary": {
            "current_cumulative_pnl": curve[-1]["cumulative_pnl"] if curve else 0.0,
            "peak_pnl":               max(p["peak"] for p in curve) if curve else 0.0,
            "max_drawdown":           dd["max_drawdown"],
            "max_drawdown_pct":       dd["max_drawdown_pct"],
        },
    }


# ---------------------------------------------------------------------------
# /performance-summary
# ---------------------------------------------------------------------------

def performance_summary(conn: sqlite3.Connection) -> Dict[str, Any]:
    _empty: Dict[str, Any] = {
        "total_trades":    0,
        "total_pnl":       0.0,
        "win_rate":        0.0,
        "expectancy":      0.0,
        "profit_factor":   None,
        "sharpe_ratio":    None,
        "max_drawdown":    0.0,
        "max_drawdown_pct": 0.0,
        "avg_win":         None,
        "avg_loss":        None,
        "current_streak":  {"type": "none", "count": 0},
        "best_win_streak": 0,
        "best_loss_streak": 0,
        "monthly_returns": [],
    }

    try:
        trades = _load_trades_for_analytics(conn)
    except Exception as exc:
        return {**_empty, "error": str(exc)}

    if not trades:
        return _empty

    try:
        from research.analytics.equity import (  # type: ignore
            build_equity_curve as _eq,
            build_drawdown_curve as _dd,
            build_monthly_returns as _monthly,
        )
        from research.analytics.performance import (
            calculate_win_rate as _wr,
            calculate_expectancy as _exp,
            calculate_profit_factor as _pf,
            calculate_sharpe_ratio as _sharpe,
            calculate_max_drawdown as _mdd,
            calculate_consecutive_wins_losses as _streaks,
        )
    except ImportError as exc:
        return {**_empty, "error": str(exc)}

    curve   = _dd(_eq(trades))
    mdd     = _mdd(curve)
    streaks = _streaks(trades)
    monthly = _monthly(trades)

    wins   = [t for t in trades if float(t.get("pnl") or 0.0) > 0]
    losses = [t for t in trades if float(t.get("pnl") or 0.0) <= 0]
    avg_win  = round(sum(float(t.get("pnl") or 0) for t in wins)   / len(wins),   2) if wins   else None
    avg_loss = round(sum(float(t.get("pnl") or 0) for t in losses) / len(losses), 2) if losses else None

    return {
        "total_trades":    len(trades),
        "total_pnl":       round(sum(float(t.get("pnl") or 0) for t in trades), 2),
        "win_rate":        _wr(trades),
        "expectancy":      _exp(trades),
        "profit_factor":   _pf(trades),
        "sharpe_ratio":    _sharpe(trades),
        "max_drawdown":    mdd["max_drawdown"],
        "max_drawdown_pct": mdd["max_drawdown_pct"],
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "current_streak":  streaks["current_streak"],
        "best_win_streak": streaks["best_win_streak"],
        "best_loss_streak": streaks["best_loss_streak"],
        "monthly_returns": monthly,
    }


# ---------------------------------------------------------------------------
# /decision-queue
# ---------------------------------------------------------------------------

_CLASSIFICATION_MAP = {
    "Live Candidate": "LIVE_CANDIDATE",
    "Forward Test":   "FORWARD_TEST_CANDIDATE",
    "Optimize":       "OPTIMIZATION_CANDIDATE",
    "Retest":         "NEEDS_RETEST",
    "Reject":         "REJECTED",
}

_NEXT_ACTION_MAP = {
    "Live Candidate": "Human approval required — submit to Forward Testing Journal",
    "Forward Test":   "Human approval required — submit to Forward Testing Journal",
    "Optimize":       "Send to Optimization Lab, then re-score",
    "Retest":         "Extend backtest window, resubmit to Backtesting Lab",
    "Reject":         "Archive under research/rejected/ with documented reason",
}


def decision_queue(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        cur = conn.execute("""
            SELECT
                ss.spec_id,
                ss.spec_name                                                       AS name,
                ss.asset_class,
                ss.symbol,
                ss.timeframe,
                sr.composite_score,
                sr.grade,
                sr.recommendation,
                sr.walk_forward_pass,
                sr.monte_carlo_pass,
                sr.prop_firm_supported,
                sr.overfitting_risk,
                sr.overfit_warnings_json,
                sr.scored_at,
                CAST((julianday('now') - julianday(sr.scored_at)) AS INTEGER)     AS days_in_queue
            FROM scoring_results sr
            JOIN strategy_specs ss ON ss.spec_id = sr.spec_id
            WHERE ss.status NOT IN ('approved', 'rejected')
              AND sr.scoring_id = (
                  SELECT MAX(scoring_id) FROM scoring_results WHERE spec_id = sr.spec_id
              )
            ORDER BY
                CASE sr.recommendation
                    WHEN 'Live Candidate' THEN 1
                    WHEN 'Forward Test'   THEN 2
                    WHEN 'Optimize'       THEN 3
                    WHEN 'Retest'         THEN 4
                    WHEN 'Reject'         THEN 5
                    ELSE 6
                END,
                sr.composite_score DESC
        """)
        items = _rows(cur)

        for item in items:
            rec = item.get("recommendation") or ""
            item["classification"] = _CLASSIFICATION_MAP.get(rec, "UNCLASSIFIED")
            item["next_action"]    = _NEXT_ACTION_MAP.get(rec, "Pending review")
            item["status"]         = "REVIEW_REQUIRED"

            warnings: List[str] = []
            try:
                warnings = json.loads(item.get("overfit_warnings_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                pass
            item.pop("overfit_warnings_json", None)

            if warnings:
                item["reason"] = "; ".join(warnings)
            elif not item.get("walk_forward_pass") and not item.get("monte_carlo_pass"):
                item["reason"] = "Walk-forward and Monte Carlo both failed"
            elif not item.get("walk_forward_pass"):
                item["reason"] = "Walk-forward degradation too high"
            elif not item.get("monte_carlo_pass"):
                item["reason"] = "Monte Carlo failure probability too high"
            elif (item.get("overfitting_risk") or 0) > 0.3:
                item["reason"] = "Elevated overfit risk detected"
            else:
                item["reason"] = "All validation gates passed"

    except Exception as exc:
        return {"error": str(exc), "count": 0, "items": []}

    return {"count": len(items), "items": items}
