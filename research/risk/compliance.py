"""
Compliance evaluator for Hermes AI Trading Firm.

Evaluates NT8 account snapshots and strategy specs against prop firm rules
and CLAUDE.md risk thresholds. Read-only — never writes to the database.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from research.risk.prop_rules import PropRule, from_template, load_all_from_db


# ---------------------------------------------------------------------------
# CLAUDE.md risk thresholds
# ---------------------------------------------------------------------------

MIN_PROFIT_FACTOR   = 1.20
MIN_SHARPE          = 0.0   # positive Sharpe required
MAX_DRAWDOWN_PCT    = 0.25  # 25 %
MIN_TRADES          = 30
MIN_MC_SURVIVAL     = 0.85  # 85 %
OOS_DECAY_THRESHOLD = 0.30  # OOS degradation > 30 % = overfit warning

# Health score weights (must sum to 1.0)
_W_DD    = 0.40
_W_DAILY = 0.30
_W_EQ    = 0.20
_W_MISC  = 0.10


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class Status(str, Enum):
    SAFE      = "SAFE"
    WARNING   = "WARNING"
    DANGER    = "DANGER"
    VIOLATION = "VIOLATION"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AccountComplianceResult:
    account_id:   str
    rule:         PropRule
    equity:       float
    daily_pnl:    float
    dd_used:      float
    dd_limit:     float    # absolute $ from snapshot
    dd_ratio:     float    # dd_used / dd_limit
    daily_ratio:  float    # abs(loss) / rule.daily_loss_limit_dollars
    health_score: float    # 0.0 – 1.0
    status:       Status
    violations:   List[str]
    warnings:     List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "account_id":    self.account_id,
            "firm_name":     self.rule.firm_name,
            "account_label": self.rule.account_label,
            "equity":        self.equity,
            "daily_pnl":     self.daily_pnl,
            "dd_used":       self.dd_used,
            "dd_limit":      self.dd_limit,
            "dd_ratio":      round(self.dd_ratio, 4),
            "daily_ratio":   round(self.daily_ratio, 4),
            "health_score":  round(self.health_score, 4),
            "status":        self.status.value,
            "violations":    self.violations,
            "warnings":      self.warnings,
        }


@dataclass
class StrategyComplianceResult:
    spec_id:          Optional[int]
    spec_name:        str
    spec_status:      Optional[str]
    profit_factor:    Optional[float]
    max_drawdown_pct: Optional[float]
    trade_count:      Optional[int]
    sharpe_ratio:     Optional[float]
    status:           Status
    violations:       List[str]
    warnings:         List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "spec_id":          self.spec_id,
            "spec_name":        self.spec_name,
            "spec_status":      self.spec_status,
            "profit_factor":    self.profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "trade_count":      self.trade_count,
            "sharpe_ratio":     self.sharpe_ratio,
            "status":           self.status.value,
            "violations":       self.violations,
            "warnings":         self.warnings,
        }


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def classify_status(violations: List[str], warnings: List[str]) -> Status:
    """Map violation/warning lists to a Status enum value."""
    if violations:
        return Status.VIOLATION
    if len(warnings) >= 2:
        return Status.DANGER
    if warnings:
        return Status.WARNING
    return Status.SAFE


def calculate_health_score(
    dd_ratio:     float,
    daily_ratio:  float,
    equity_ratio: float,
) -> float:
    """
    Compute a 0.0–1.0 health score from three normalized risk ratios.
    1.0 = fully healthy, 0.0 = all limits simultaneously breached.
    """
    score = (
        _W_DD    * max(0.0, 1.0 - dd_ratio)
        + _W_DAILY * max(0.0, 1.0 - daily_ratio)
        + _W_EQ    * min(1.0, max(0.0, equity_ratio))
        + _W_MISC  * 1.0
    )
    return round(min(1.0, max(0.0, score)), 4)


def evaluate_account_compliance(
    snapshot: Dict[str, Any],
    rule:     PropRule,
    trades:   Optional[List[Dict[str, Any]]] = None,
) -> AccountComplianceResult:
    """
    Evaluate a single NT8 account snapshot against a PropRule.

    snapshot keys: account_id, equity, daily_pnl,
                   trailing_drawdown_used, trailing_drawdown_limit
    trades (optional): list of nt8_trades rows for this account;
                       enables max_position_size and min_trading_days checks.
    """
    account_id = snapshot.get("account_id") or "unknown"
    equity     = float(snapshot.get("equity")                 or 0.0)
    daily_pnl  = float(snapshot.get("daily_pnl")             or 0.0)
    dd_used    = float(snapshot.get("trailing_drawdown_used") or 0.0)
    dd_limit   = float(
        snapshot.get("trailing_drawdown_limit") or rule.dd_limit_dollars or 1.0
    )

    violations: List[str] = []
    warnings:   List[str] = []

    # --- Trailing drawdown ---
    dd_ratio = dd_used / dd_limit if dd_limit else 0.0
    if dd_ratio >= 1.0:
        violations.append(
            f"Trailing drawdown breached: ${dd_used:,.2f} of ${dd_limit:,.2f} limit"
        )
    elif dd_ratio >= 0.80:
        warnings.append(
            f"Drawdown at {dd_ratio*100:.1f}% of limit "
            f"(${dd_used:,.2f} / ${dd_limit:,.2f})"
        )

    # --- Daily loss ---
    daily_loss  = abs(min(daily_pnl, 0.0))
    daily_limit = rule.daily_loss_limit_dollars
    daily_ratio = daily_loss / daily_limit if daily_limit else 0.0
    if daily_ratio >= 1.0:
        violations.append(
            f"Daily loss limit breached: ${daily_loss:,.2f} loss "
            f"vs ${daily_limit:,.2f} limit"
        )
    elif daily_ratio >= 0.80:
        warnings.append(
            f"Daily loss at {daily_ratio*100:.1f}% of limit (${daily_loss:,.2f})"
        )

    # --- Equity floor ---
    min_equity   = rule.account_size * (1.0 - rule.trailing_drawdown_limit)
    equity_ratio = equity / rule.account_size if rule.account_size else 1.0
    if equity < min_equity:
        violations.append(
            f"Equity ${equity:,.2f} below floor ${min_equity:,.2f} "
            f"({rule.trailing_drawdown_limit*100:.0f}% DD limit)"
        )

    # --- Trade-level checks (only when trade list is provided) ---
    if trades:
        if rule.max_position_size is not None:
            oversized = [
                t for t in trades
                if (t.get("quantity") or 0) > rule.max_position_size
            ]
            if oversized:
                violations.append(
                    f"{len(oversized)} trade(s) exceeded max position size "
                    f"({rule.max_position_size} contracts)"
                )

        if rule.min_trading_days > 0:
            unique_days = len({
                (t.get("entry_time") or "")[:10]
                for t in trades
                if t.get("entry_time")
            })
            if unique_days < rule.min_trading_days:
                warnings.append(
                    f"Only {unique_days} trading day(s) recorded; "
                    f"minimum is {rule.min_trading_days}"
                )

    health = calculate_health_score(dd_ratio, daily_ratio, equity_ratio)
    status = classify_status(violations, warnings)

    return AccountComplianceResult(
        account_id=account_id,
        rule=rule,
        equity=equity,
        daily_pnl=daily_pnl,
        dd_used=dd_used,
        dd_limit=dd_limit,
        dd_ratio=dd_ratio,
        daily_ratio=daily_ratio,
        health_score=health,
        status=status,
        violations=violations,
        warnings=warnings,
    )


def evaluate_strategy_compliance(
    spec: Dict[str, Any],
) -> StrategyComplianceResult:
    """
    Evaluate a strategy_specs + backtest record against CLAUDE.md thresholds.

    spec keys: spec_id, spec_name, status,
               profit_factor, max_drawdown_pct, trade_count, sharpe_ratio
    """
    spec_id   = spec.get("spec_id")
    spec_name = spec.get("spec_name") or spec.get("name") or "unknown"
    pf        = spec.get("profit_factor")
    max_dd    = spec.get("max_drawdown_pct")
    tc        = spec.get("trade_count") or spec.get("total_trades")
    sharpe    = spec.get("sharpe_ratio")

    violations: List[str] = []
    warnings:   List[str] = []

    if pf is not None:
        if pf < 1.0:
            violations.append(f"Profit factor {pf:.2f} < 1.0 — negative expectancy")
        elif pf < MIN_PROFIT_FACTOR:
            warnings.append(
                f"Profit factor {pf:.2f} below threshold ({MIN_PROFIT_FACTOR})"
            )

    if max_dd is not None:
        if max_dd > MAX_DRAWDOWN_PCT:
            violations.append(
                f"Max drawdown {max_dd*100:.1f}% exceeds limit "
                f"({MAX_DRAWDOWN_PCT*100:.0f}%)"
            )
        elif max_dd > MAX_DRAWDOWN_PCT * 0.80:
            warnings.append(
                f"Max drawdown {max_dd*100:.1f}% approaching limit"
            )

    if tc is not None and tc < MIN_TRADES:
        warnings.append(
            f"Trade count {tc} below minimum ({MIN_TRADES}) — insufficient sample"
        )

    if sharpe is not None and sharpe < MIN_SHARPE:
        warnings.append(f"Negative Sharpe ratio ({sharpe:.2f})")

    return StrategyComplianceResult(
        spec_id=spec_id,
        spec_name=spec_name,
        spec_status=spec.get("status"),
        profit_factor=pf,
        max_drawdown_pct=max_dd,
        trade_count=tc,
        sharpe_ratio=sharpe,
        status=classify_status(violations, warnings),
        violations=violations,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Convenience: full compliance run from a DB connection
# ---------------------------------------------------------------------------

def run_full_compliance(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Load the latest NT8 account snapshot + all active prop firm profiles,
    evaluate account compliance against each profile, and evaluate all
    strategy_specs against their most recent in-sample backtest.

    Returns a dict safe for JSON serialisation.
    """
    # --- Latest account snapshot ---
    snap: Optional[Dict[str, Any]] = None
    trades_for_account: List[Dict[str, Any]] = []
    try:
        cur = conn.execute("""
            SELECT account_id, equity, daily_pnl,
                   trailing_drawdown_used, trailing_drawdown_limit, snapshot_at
            FROM nt8_account_snapshots
            ORDER BY snapshot_at DESC
            LIMIT 1
        """)
        cols = [d[0] for d in cur.description]
        rows = [{cols[i]: r[i] for i in range(len(cols))} for r in cur.fetchall()]
        snap = rows[0] if rows else None
    except Exception:
        pass

    if snap:
        try:
            cur = conn.execute("""
                SELECT quantity, entry_time
                FROM nt8_trades
                WHERE account_id = ?
            """, (snap["account_id"],))
            cols = [d[0] for d in cur.description]
            trades_for_account = [
                {cols[i]: r[i] for i in range(len(cols))} for r in cur.fetchall()
            ]
        except Exception:
            pass

    # --- Load prop firm rules (fall back to Apex template if DB is empty) ---
    rules = load_all_from_db(conn)
    if not rules and snap:
        rules = [from_template("apex", account_size=snap.get("equity") or 50_000.0)]

    account_results: List[Dict[str, Any]] = []
    if snap:
        for rule in rules:
            res = evaluate_account_compliance(
                snap, rule, trades=trades_for_account
            )
            account_results.append(res.as_dict())

    # --- Strategy compliance (spec + latest in-sample backtest) ---
    strategy_results: List[Dict[str, Any]] = []
    try:
        cur = conn.execute("""
            SELECT
                ss.spec_id,
                ss.spec_name,
                ss.status,
                b.profit_factor,
                b.max_drawdown_pct,
                b.total_trades   AS trade_count,
                b.sharpe_ratio
            FROM strategy_specs ss
            LEFT JOIN backtests b
                ON b.backtest_id = (
                    SELECT MAX(backtest_id)
                    FROM backtests
                    WHERE spec_id = ss.spec_id AND is_in_sample = 1
                )
            ORDER BY ss.spec_id
        """)
        cols  = [d[0] for d in cur.description]
        specs = [{cols[i]: r[i] for i in range(len(cols))} for r in cur.fetchall()]
    except Exception:
        specs = []

    for spec in specs:
        res = evaluate_strategy_compliance(spec)
        strategy_results.append(res.as_dict())

    # --- Aggregate firm health ---
    all_violations = [v for r in account_results for v in r["violations"]]
    all_warnings   = [w for r in account_results for w in r["warnings"]]
    firm_score = (
        round(sum(r["health_score"] for r in account_results) / len(account_results), 4)
        if account_results else 1.0
    )

    return {
        "firm_health_score": firm_score,
        "firm_status":       classify_status(all_violations, all_warnings).value,
        "account_count":     len(account_results),
        "accounts":          account_results,
        "strategy_count":    len(strategy_results),
        "strategies":        strategy_results,
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import os
    import sys

    db_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "database", "hermes_research.db"
    )
    if not os.path.exists(db_path):
        print(f"[ERROR] DB not found: {db_path}")
        sys.exit(1)

    _conn   = sqlite3.connect(db_path)
    _report = run_full_compliance(_conn)
    _conn.close()

    print(json.dumps(_report, indent=2))
