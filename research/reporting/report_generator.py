"""Report generator — read-only database access.

Public API
----------
generate_strategy_report(conn, spec_id)  -> Dict
generate_all_reports(conn)               -> List[Dict]
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .templates import CLASSIFICATION_MAP


# ---------------------------------------------------------------------------
# Internal DB loaders (read-only)
# ---------------------------------------------------------------------------


def _one(cur: sqlite3.Cursor) -> Optional[Dict[str, Any]]:
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return {cols[i]: row[i] for i in range(len(cols))}


def _load_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    return _one(conn.execute(
        """SELECT spec_id, spec_name, asset_class, symbol, timeframe, status
           FROM strategy_specs WHERE spec_id = ?""",
        (spec_id,),
    ))


def _load_scoring(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    return _one(conn.execute(
        """SELECT composite_score, grade, recommendation,
                  profitability_score, drawdown_score, consistency_score,
                  walk_forward_score, monte_carlo_score, regime_score,
                  robustness_score, prop_firm_score, explainability_score,
                  overfitting_risk, monte_carlo_pass, walk_forward_pass,
                  prop_firm_supported, overfit_warnings_json,
                  overfit_warnings_json AS gate_failures_json,
                  scored_at
           FROM scoring_results
           WHERE spec_id = ?
           ORDER BY scoring_id DESC
           LIMIT 1""",
        (spec_id,),
    ))


def _load_backtest(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    return _one(conn.execute(
        """SELECT profit_factor, sharpe_ratio, max_drawdown_pct, win_rate, total_trades
           FROM backtests
           WHERE spec_id = ?
           ORDER BY backtest_id DESC
           LIMIT 1""",
        (spec_id,),
    ))


def _classify(recommendation: Optional[str]) -> str:
    return CLASSIFICATION_MAP.get(recommendation or "", "UNCLASSIFIED")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_strategy_report(conn: sqlite3.Connection, spec_id: int) -> Dict[str, Any]:
    """Build a complete report dict for one strategy.

    Pulls spec, latest scoring result, and latest backtest (if any).
    Returns a plain dict — pass to exporter to write to disk.
    """
    spec    = _load_spec(conn, spec_id)
    scoring = _load_scoring(conn, spec_id)
    backtest = _load_backtest(conn, spec_id)

    if spec is None:
        return {"error": f"spec_id {spec_id} not found", "spec_id": spec_id}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Flatten scoring into a summary block
    sc_block: Dict[str, Any] = {}
    component_scores: Dict[str, Any] = {}
    overfit_warnings: List[str] = []
    gate_failures: List[str] = []

    if scoring:
        sc_block = {
            "composite_score":   scoring["composite_score"],
            "grade":             scoring["grade"],
            "recommendation":    scoring["recommendation"],
            "monte_carlo_pass":  bool(scoring["monte_carlo_pass"]),
            "walk_forward_pass": bool(scoring["walk_forward_pass"]),
            "prop_firm_supported": bool(scoring["prop_firm_supported"]),
            "overfitting_risk":  scoring["overfitting_risk"],
            "scored_at":         scoring["scored_at"],
        }
        component_scores = {
            "profitability":  scoring["profitability_score"],
            "drawdown":       scoring["drawdown_score"],
            "consistency":    scoring["consistency_score"],
            "walk_forward":   scoring["walk_forward_score"],
            "monte_carlo":    scoring["monte_carlo_score"],
            "regime":         scoring["regime_score"],
            "robustness":     scoring["robustness_score"],
            "prop_firm":      scoring["prop_firm_score"],
            "explainability": scoring["explainability_score"],
            "overfitting_risk": scoring["overfitting_risk"],
        }
        try:
            overfit_warnings = json.loads(scoring["overfit_warnings_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            overfit_warnings = []

    bt_block: Dict[str, Any] = {}
    if backtest:
        bt_block = {
            "profit_factor":    backtest["profit_factor"],
            "sharpe_ratio":     backtest["sharpe_ratio"],
            "max_drawdown_pct": backtest["max_drawdown_pct"],
            "win_rate":         backtest["win_rate"],
            "total_trades":     backtest["total_trades"],
        }

    return {
        "report_type":     "strategy",
        "generated_at":    now,
        "spec_id":         spec_id,
        "spec_name":       spec["spec_name"],
        "asset_class":     spec["asset_class"],
        "symbol":          spec["symbol"],
        "timeframe":       spec["timeframe"],
        "status":          spec["status"],
        "scoring":         sc_block,
        "component_scores": component_scores,
        "backtest":        bt_block,
        "classification":  _classify(sc_block.get("recommendation")),
        "overfit_warnings": overfit_warnings,
        "gate_failures":   gate_failures,
    }


def generate_all_reports(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Generate a strategy report for every spec that has a scoring result.

    Returns reports sorted by composite_score descending.
    """
    rows = conn.execute(
        "SELECT DISTINCT spec_id FROM scoring_results ORDER BY spec_id"
    ).fetchall()
    spec_ids = [r[0] for r in rows]

    reports = [generate_strategy_report(conn, sid) for sid in spec_ids]

    return sorted(
        reports,
        key=lambda r: (r.get("scoring") or {}).get("composite_score") or 0,
        reverse=True,
    )
