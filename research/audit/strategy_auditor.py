#!/usr/bin/env python3
"""
Strategy Auditor -- research/audit/strategy_auditor.py

Read-only pre-approval audit checklist. Helps the human reviewer decide
whether a strategy needs more research, more data, forward-test
consideration, or rejection.

No DB writes. No schema changes. No scoring changes. No promotion.
Human approval gate remains mandatory.

Audit categories
----------------
1. Data Completeness   -- spec, backtest, trade list, equity curve, score
2. Sample Size         -- trade count thresholds (FAIL <30, WARN <100)
3. Backtest Quality    -- date ranges, performance summary, initial capital
4. Overfit Risk        -- PF/Sharpe/win-rate suspicion flags vs trade count
5. Out-of-Sample       -- OOS backtest, walk-forward, Monte Carlo
6. Prop-Firm Readiness -- drawdown limits, prop_firm_supported flag
7. Recommendation      -- NEEDS_REAL_NT8_EXPORT | NEEDS_MORE_TRADES |
                          NEEDS_WALK_FORWARD | READY_FOR_HUMAN_REVIEW |
                          REJECT_RESEARCH_CANDIDATE

Usage
-----
Audit one strategy:
    python -m research.audit.strategy_auditor --spec-id 1

Audit all strategies:
    python -m research.audit.strategy_auditor --all

Dry-run (no report files written):
    python -m research.audit.strategy_auditor --spec-id 1 --dry-run
    python -m research.audit.strategy_auditor --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB      = _PROJECT_ROOT / "database" / "hermes_research.db"
DEFAULT_REPORTS = _PROJECT_ROOT / "reports" / "audits"

_STATUS_ICON = {"PASS": "+", "WARN": "!", "FAIL": "X", "INFO": "i"}

# Prop-firm typical max trailing drawdown (5% is common)
_PROP_FIRM_DD_LIMIT = 0.05

# Summary table: categories shown and their column headers
_SUMMARY_CATS = [
    ("Data Completeness",       "Data"),
    ("Sample Size",             "Size"),
    ("Overfit Risk",            "Overfit"),
    ("Out-of-Sample Readiness", "OOS"),
    ("Prop-Firm Readiness",     "Prop"),
]

# Recommendations that indicate the strategy has real backtest data
_DATA_RECS = {"NEEDS_WALK_FORWARD", "READY_FOR_HUMAN_REVIEW", "REJECT_RESEARCH_CANDIDATE"}

_STATUS_RANK = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class AuditCheck:
    category: str
    check:    str
    status:   str   # PASS | WARN | FAIL | INFO
    detail:   str


@dataclass
class AuditReport:
    spec_id:     int
    spec_name:   str
    symbol:      str
    timeframe:   str
    spec_status: str
    audited_at:  str
    checks:         List[AuditCheck] = field(default_factory=list)
    recommendation: str              = "UNKNOWN"

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "PASS")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "WARN")

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "FAIL")


# ---------------------------------------------------------------------------
# Database helpers (read-only)
# ---------------------------------------------------------------------------

def _open_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:///{db_path}?mode=ro", uri=True)


def _fetch_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT spec_id, spec_name, asset_class, symbol, timeframe,
               status, created_at, updated_at
        FROM strategy_specs WHERE spec_id = ?
    """, (spec_id,)).fetchone()
    if row is None:
        return None
    cols = ["spec_id", "spec_name", "asset_class", "symbol", "timeframe",
            "status", "created_at", "updated_at"]
    return dict(zip(cols, row))


def _fetch_latest_backtest(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT backtest_id, data_source, data_start_date, data_end_date,
               net_profit, profit_factor, win_rate, total_trades,
               max_drawdown_pct, sharpe_ratio, sortino_ratio,
               trade_list_json, equity_curve_json,
               initial_capital, is_in_sample
        FROM backtests
        WHERE spec_id = ?
        ORDER BY backtest_id DESC
        LIMIT 1
    """, (spec_id,)).fetchone()
    if row is None:
        return None
    cols = ["backtest_id", "data_source", "data_start_date", "data_end_date",
            "net_profit", "profit_factor", "win_rate", "total_trades",
            "max_drawdown_pct", "sharpe_ratio", "sortino_ratio",
            "trade_list_json", "equity_curve_json",
            "initial_capital", "is_in_sample"]
    return dict(zip(cols, row))


def _count_backtests(conn: sqlite3.Connection, spec_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM backtests WHERE spec_id = ?", (spec_id,)
    ).fetchone()[0]


def _count_oos_backtests(conn: sqlite3.Connection, spec_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM backtests WHERE spec_id = ? AND is_in_sample = 0",
        (spec_id,),
    ).fetchone()[0]


def _fetch_latest_score(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT scoring_id, composite_score, grade, recommendation,
               walk_forward_score, monte_carlo_score, robustness_score,
               overfitting_risk, walk_forward_pass, monte_carlo_pass,
               prop_firm_supported, scored_at
        FROM scoring_results
        WHERE spec_id = ?
        ORDER BY scoring_id DESC
        LIMIT 1
    """, (spec_id,)).fetchone()
    if row is None:
        return None
    cols = ["scoring_id", "composite_score", "grade", "recommendation",
            "walk_forward_score", "monte_carlo_score", "robustness_score",
            "overfitting_risk", "walk_forward_pass", "monte_carlo_pass",
            "prop_firm_supported", "scored_at"]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Check helpers
# ---------------------------------------------------------------------------

def _chk(checks: List[AuditCheck], category: str, check: str,
         status: str, detail: str) -> None:
    checks.append(AuditCheck(category=category, check=check,
                              status=status, detail=detail))


# ---------------------------------------------------------------------------
# Category 1: Data Completeness
# ---------------------------------------------------------------------------

def _check_data_completeness(
    checks: List[AuditCheck],
    spec: Dict[str, Any],
    bt: Optional[Dict[str, Any]],
    bt_count: int,
    score: Optional[Dict[str, Any]],
) -> None:
    cat = "Data Completeness"

    _chk(checks, cat, "Has strategy spec", "PASS",
         f"spec_id={spec['spec_id']}  status={spec['status']}")

    if bt_count == 0:
        _chk(checks, cat, "Has at least one backtest", "FAIL",
             "No backtest rows found -- import NT8 export first")
    else:
        _chk(checks, cat, "Has at least one backtest", "PASS",
             f"{bt_count} backtest(s) found  latest=backtest_id={bt['backtest_id']}")

    if bt is None:
        _chk(checks, cat, "Has trade_list_json",   "FAIL", "No backtest available")
        _chk(checks, cat, "Has equity_curve_json", "FAIL", "No backtest available")
    else:
        tl_json = bt.get("trade_list_json")
        if tl_json and tl_json.strip() not in ("null", "[]", ""):
            try:
                trades = json.loads(tl_json)
                _chk(checks, cat, "Has trade_list_json", "PASS",
                     f"{len(trades)} trade(s) in JSON")
            except (json.JSONDecodeError, TypeError):
                _chk(checks, cat, "Has trade_list_json", "WARN",
                     "trade_list_json present but could not be parsed")
        else:
            _chk(checks, cat, "Has trade_list_json", "FAIL",
                 "trade_list_json is null -- run nt8_import_pipeline with real export")

        ec_json = bt.get("equity_curve_json")
        if ec_json and ec_json.strip() not in ("null", "[]", ""):
            try:
                points = json.loads(ec_json)
                _chk(checks, cat, "Has equity_curve_json", "PASS",
                     f"{len(points)} equity point(s)")
            except (json.JSONDecodeError, TypeError):
                _chk(checks, cat, "Has equity_curve_json", "WARN",
                     "equity_curve_json present but could not be parsed")
        else:
            _chk(checks, cat, "Has equity_curve_json", "FAIL",
                 "equity_curve_json is null -- trade list required for equity curve")

    if score is None:
        _chk(checks, cat, "Has latest scoring result", "FAIL",
             "No scoring result -- run score_from_backtests.py after importing backtest")
    else:
        _chk(checks, cat, "Has latest scoring result", "PASS",
             f"score={score['composite_score']}  grade={score['grade']}"
             f"  scored_at={score['scored_at']}")


# ---------------------------------------------------------------------------
# Category 2: Sample Size
# ---------------------------------------------------------------------------

def _check_sample_size(
    checks: List[AuditCheck],
    bt: Optional[Dict[str, Any]],
) -> None:
    cat = "Sample Size"

    if bt is None:
        _chk(checks, cat, "Total trades", "FAIL",
             "No backtest available -- cannot assess sample size")
        return

    trades = bt.get("total_trades")
    if trades is None:
        _chk(checks, cat, "Total trades", "WARN",
             "total_trades not recorded in backtest row")
        return

    if trades < 30:
        _chk(checks, cat, "Total trades", "FAIL",
             f"{trades} trades -- minimum 30 required (hard gate)")
    elif trades < 100:
        _chk(checks, cat, "Total trades", "WARN",
             f"{trades} trades -- 100+ recommended for statistical confidence")
    else:
        _chk(checks, cat, "Total trades", "PASS",
             f"{trades} trades -- sufficient sample size")


# ---------------------------------------------------------------------------
# Category 3: Backtest Quality
# ---------------------------------------------------------------------------

def _check_backtest_quality(
    checks: List[AuditCheck],
    bt: Optional[Dict[str, Any]],
) -> None:
    cat = "Backtest Quality"

    if bt is None:
        _chk(checks, cat, "Backtest data", "FAIL",
             "No backtest available -- cannot assess quality")
        return

    start = bt.get("data_start_date")
    end   = bt.get("data_end_date")

    if start and end:
        _chk(checks, cat, "Date range", "PASS",
             f"{start} to {end}")
    elif start or end:
        _chk(checks, cat, "Date range", "WARN",
             f"Partial date range: start={start}  end={end}")
    else:
        _chk(checks, cat, "Date range", "WARN",
             "data_start_date and data_end_date both missing")

    net_profit = bt.get("net_profit")
    pf         = bt.get("profit_factor")
    if net_profit is not None and pf is not None:
        _chk(checks, cat, "Performance summary", "PASS",
             f"net_profit={net_profit}  profit_factor={pf}")
    elif bt.get("data_source", "").endswith("(trade list)"):
        _chk(checks, cat, "Performance summary", "WARN",
             "Derived from trade list only -- no NT8 Performance Summary imported")
    else:
        _chk(checks, cat, "Performance summary", "WARN",
             "Net profit or profit factor missing from backtest row")

    tl = bt.get("trade_list_json")
    tl_ok = bool(tl and tl.strip() not in ("null", "[]", ""))
    _chk(checks, cat, "Trade list attached", "PASS" if tl_ok else "WARN",
         "trade_list_json populated" if tl_ok
         else "trade_list_json missing -- run pipeline with real NT8 export")

    cap = bt.get("initial_capital")
    if cap is not None:
        _chk(checks, cat, "Initial capital", "PASS", f"${cap:,.0f}")
    else:
        _chk(checks, cat, "Initial capital", "WARN",
             "initial_capital not recorded -- equity curve uses raw P&L only")


# ---------------------------------------------------------------------------
# Category 4: Overfit Risk
# ---------------------------------------------------------------------------

def _check_overfit_risk(
    checks: List[AuditCheck],
    bt: Optional[Dict[str, Any]],
    score: Optional[Dict[str, Any]],
) -> None:
    cat = "Overfit Risk"

    if bt is None:
        _chk(checks, cat, "Overfit screening", "FAIL",
             "No backtest available -- cannot screen for overfit risk")
        return

    trades = bt.get("total_trades") or 0
    pf     = bt.get("profit_factor")
    sharpe = bt.get("sharpe_ratio")
    wr     = bt.get("win_rate")
    mdd    = bt.get("max_drawdown_pct")
    net_p  = bt.get("net_profit")

    any_flag = False

    # Tiered thresholds: tighter limits when trade count is low
    # < 75 trades: small sample -- lower bar for "suspicious" metrics
    pf_limit     = 2.0 if trades < 75 else 2.5
    sharpe_limit = 1.5 if trades < 75 else 2.0
    wr_limit     = 0.65 if trades < 75 else 0.75

    if pf is not None and pf > pf_limit and trades < 100:
        _chk(checks, cat, "Profit factor vs trades",
             "WARN",
             f"PF={pf} > {pf_limit} with only {trades} trades -- "
             f"insufficient sample to trust this metric")
        any_flag = True

    if sharpe is not None and sharpe > sharpe_limit and trades < 100:
        _chk(checks, cat, "Sharpe vs trades",
             "WARN",
             f"Sharpe={sharpe} > {sharpe_limit} with only {trades} trades -- "
             f"insufficient sample to trust this metric")
        any_flag = True

    if wr is not None and wr > wr_limit and trades < 100:
        _chk(checks, cat, "Win rate vs trades",
             "WARN",
             f"Win rate={wr:.0%} > {wr_limit:.0%} with only {trades} trades -- "
             f"insufficient sample to trust this metric")
        any_flag = True

    if mdd is not None and net_p is not None and net_p > 0 and abs(mdd) < 0.01:
        _chk(checks, cat, "Drawdown vs profit",
             "WARN",
             f"Max DD={abs(mdd):.1%} is suspiciously low relative to net_profit={net_p}"
             " -- verify backtest period coverage")
        any_flag = True

    if score is not None:
        risk = score.get("overfitting_risk")
        if risk is not None and risk > 0.3:
            _chk(checks, cat, "Scoring overfit risk",
                 "WARN", f"overfitting_risk={risk:.2f} flagged by scoring engine")
            any_flag = True
        elif risk is not None:
            _chk(checks, cat, "Scoring overfit risk",
                 "PASS", f"overfitting_risk={risk:.2f}")

    if not any_flag:
        _chk(checks, cat, "Overfit screening", "PASS",
             "No overfit warning flags raised")


# ---------------------------------------------------------------------------
# Category 5: Out-of-Sample Readiness
# ---------------------------------------------------------------------------

def _check_oos_readiness(
    checks: List[AuditCheck],
    oos_count: int,
    score: Optional[Dict[str, Any]],
) -> None:
    cat = "Out-of-Sample Readiness"

    if oos_count == 0:
        _chk(checks, cat, "Out-of-sample backtest", "FAIL",
             "No OOS backtest found -- import a separate out-of-sample period")
    else:
        _chk(checks, cat, "Out-of-sample backtest", "PASS",
             f"{oos_count} OOS backtest(s) found")

    if score is None:
        _chk(checks, cat, "Walk-forward validation", "WARN",
             "No scoring result -- walk-forward score unavailable")
        _chk(checks, cat, "Monte Carlo validation", "WARN",
             "No scoring result -- Monte Carlo score unavailable")
        return

    wf_score = score.get("walk_forward_score")
    wf_pass  = bool(score.get("walk_forward_pass"))
    if wf_score is not None:
        if wf_pass:
            status = "PASS"
        elif wf_score >= 0.50:
            status = "WARN"
        else:
            status = "FAIL"
        _chk(checks, cat, "Walk-forward validation", status,
             f"score={wf_score}  pass={wf_pass}")
    else:
        _chk(checks, cat, "Walk-forward validation", "WARN",
             "walk_forward_score is null -- no WF data supplied to scoring engine")

    mc_score = score.get("monte_carlo_score")
    mc_pass  = bool(score.get("monte_carlo_pass"))
    if mc_score is not None:
        if mc_pass:
            status = "PASS"
        elif mc_score >= 0.70:
            status = "WARN"   # 70-84%: marginal
        else:
            status = "FAIL"   # < 70%: sequence-dependent
        _chk(checks, cat, "Monte Carlo validation", status,
             f"score={mc_score}  pass={mc_pass}")
    else:
        _chk(checks, cat, "Monte Carlo validation", "WARN",
             "monte_carlo_score is null -- no MC data supplied to scoring engine")


# ---------------------------------------------------------------------------
# Category 6: Prop-Firm Readiness
# ---------------------------------------------------------------------------

def _check_prop_firm(
    checks: List[AuditCheck],
    bt: Optional[Dict[str, Any]],
    score: Optional[Dict[str, Any]],
) -> None:
    cat = "Prop-Firm Readiness"

    if bt is None:
        _chk(checks, cat, "Drawdown assessment", "FAIL",
             "No backtest available -- cannot assess prop-firm DD limits")
        return

    mdd = bt.get("max_drawdown_pct")
    if mdd is None:
        _chk(checks, cat, "Drawdown assessment", "WARN",
             "max_drawdown_pct not recorded -- cannot verify prop-firm DD limit")
    else:
        abs_mdd = abs(mdd)
        if abs_mdd > _PROP_FIRM_DD_LIMIT:
            _chk(checks, cat, "Drawdown assessment", "WARN",
                 f"Max DD={abs_mdd:.1%} exceeds typical prop-firm limit of"
                 f" {_PROP_FIRM_DD_LIMIT:.0%} -- review rule set")
        else:
            _chk(checks, cat, "Drawdown assessment", "PASS",
                 f"Max DD={abs_mdd:.1%} within typical prop-firm limit of"
                 f" {_PROP_FIRM_DD_LIMIT:.0%}")

    if score is not None:
        supported = bool(score.get("prop_firm_supported"))
        status    = "PASS" if supported else "WARN"
        _chk(checks, cat, "Prop-firm supported flag", status,
             f"prop_firm_supported={supported} (set by scoring engine)")
    else:
        _chk(checks, cat, "Prop-firm supported flag", "WARN",
             "No scoring result -- prop_firm_supported unknown")


# ---------------------------------------------------------------------------
# Category 7: Recommendation
# ---------------------------------------------------------------------------

def _derive_recommendation(checks: List[AuditCheck]) -> str:
    fail_checks = {c.check for c in checks if c.status == "FAIL"}
    fail_cats   = {c.category for c in checks if c.status == "FAIL"}
    fail_count  = len([c for c in checks if c.status == "FAIL"])

    # No backtest at all
    if "Has at least one backtest" in fail_checks:
        return "NEEDS_REAL_NT8_EXPORT"

    # Has backtest but missing trade list (summary-only import)
    if "Has trade_list_json" in fail_checks:
        return "NEEDS_REAL_NT8_EXPORT"

    # Insufficient trades (hard gate failure)
    if any(c.check == "Total trades" and c.status == "FAIL" for c in checks):
        return "NEEDS_MORE_TRADES"

    # No OOS data
    if any(c.check == "Out-of-sample backtest" and c.status == "FAIL" for c in checks):
        return "NEEDS_WALK_FORWARD"

    # Multiple FAILs across different areas
    if fail_count >= 2:
        return "REJECT_RESEARCH_CANDIDATE"

    # Single FAIL not caught above
    if fail_count == 1:
        if "Out-of-Sample Readiness" in fail_cats:
            return "NEEDS_WALK_FORWARD"
        if "Data Completeness" in fail_cats:
            return "NEEDS_REAL_NT8_EXPORT"
        return "NEEDS_WALK_FORWARD"

    # WARN-only: check for OOS gap
    if any(c.check == "Out-of-sample backtest" and c.status == "WARN" for c in checks):
        return "NEEDS_WALK_FORWARD"
    if any(c.check in ("Walk-forward validation", "Monte Carlo validation")
           and c.status == "WARN" for c in checks):
        return "NEEDS_WALK_FORWARD"

    return "READY_FOR_HUMAN_REVIEW"


# ---------------------------------------------------------------------------
# Core audit engine
# ---------------------------------------------------------------------------

def audit_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[AuditReport]:
    """
    Run full checklist for one spec. Read-only. Returns None if spec not found.
    """
    spec = _fetch_spec(conn, spec_id)
    if spec is None:
        return None

    bt        = _fetch_latest_backtest(conn, spec_id)
    bt_count  = _count_backtests(conn, spec_id)
    oos_count = _count_oos_backtests(conn, spec_id)
    score     = _fetch_latest_score(conn, spec_id)

    checks: List[AuditCheck] = []
    _check_data_completeness(checks, spec, bt, bt_count, score)
    _check_sample_size(checks, bt)
    _check_backtest_quality(checks, bt)
    _check_overfit_risk(checks, bt, score)
    _check_oos_readiness(checks, oos_count, score)
    _check_prop_firm(checks, bt, score)

    recommendation = _derive_recommendation(checks)

    return AuditReport(
        spec_id     = spec["spec_id"],
        spec_name   = spec["spec_name"],
        symbol      = spec.get("symbol") or "",
        timeframe   = spec.get("timeframe") or "",
        spec_status = spec.get("status") or "",
        audited_at  = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        checks      = checks,
        recommendation = recommendation,
    )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _to_markdown(r: AuditReport) -> str:
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    p(f"# Strategy Audit: {r.spec_name}")
    p(f"**Audited:** {r.audited_at[:10]}")
    p(f"**spec_id:** {r.spec_id}  |  "
      f"**status:** {r.spec_status}  |  "
      f"**symbol:** {r.symbol}  |  "
      f"**timeframe:** {r.timeframe}")
    p()
    p("---")
    p()
    p(f"## Recommendation: {r.recommendation}")
    p()
    p(f"**{r.pass_count} PASS  |  {r.warn_count} WARN  |  {r.fail_count} FAIL**")
    p()
    p("---")
    p()

    # Group checks by category
    categories: Dict[str, List[AuditCheck]] = {}
    for c in r.checks:
        categories.setdefault(c.category, []).append(c)

    for i, (cat, cat_checks) in enumerate(categories.items(), 1):
        p(f"## {i}. {cat}")
        p()
        p("| Check | Status | Detail |")
        p("|-------|--------|--------|")
        for c in cat_checks:
            p(f"| {c.check} | {c.status} | {c.detail} |")
        p()

    p("---")
    p()
    p("*Read-only audit. No data was modified. "
      "Human approval required before any strategy advances beyond REVIEW_REQUIRED.*")

    return "\n".join(lines)


def _to_dict(r: AuditReport) -> Dict[str, Any]:
    return {
        "spec_id":        r.spec_id,
        "spec_name":      r.spec_name,
        "symbol":         r.symbol,
        "timeframe":      r.timeframe,
        "spec_status":    r.spec_status,
        "audited_at":     r.audited_at,
        "recommendation": r.recommendation,
        "pass_count":     r.pass_count,
        "warn_count":     r.warn_count,
        "fail_count":     r.fail_count,
        "checks": [
            {
                "category": c.category,
                "check":    c.check,
                "status":   c.status,
                "detail":   c.detail,
            }
            for c in r.checks
        ],
    }


def write_reports(
    r: AuditReport, reports_dir: Path
) -> Tuple[Path, Path]:
    """Write Markdown + JSON audit reports. Returns (md_path, json_path)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = r.audited_at[:10].replace("-", "")
    safe_name = re.sub(r"[^\w\-]", "_", r.spec_name)

    md_path   = reports_dir / f"{safe_name}_{date_str}_audit.md"
    json_path = reports_dir / f"{safe_name}_{date_str}_audit.json"

    md_path.write_text(_to_markdown(r), encoding="utf-8")
    json_path.write_text(
        json.dumps(_to_dict(r), indent=2), encoding="utf-8"
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_report(r: AuditReport) -> None:
    print(f"Audit: {r.spec_name}  [spec_id={r.spec_id}]")
    print(f"  symbol={r.symbol}  timeframe={r.timeframe}"
          f"  status={r.spec_status}")
    print()

    current_cat = ""
    for c in r.checks:
        if c.category != current_cat:
            current_cat = c.category
            print(f"  {c.category}")
        icon = _STATUS_ICON.get(c.status, c.status)
        print(f"    [{icon}] {c.check:<34} {c.detail}")

    print()
    print(f"  PASS={r.pass_count}  WARN={r.warn_count}  FAIL={r.fail_count}")
    print(f"  Recommendation: {r.recommendation}")
    print()


# ---------------------------------------------------------------------------
# Summary table (--all mode)
# ---------------------------------------------------------------------------

def _worst_status(checks: List[AuditCheck], category: str) -> str:
    cats = [c for c in checks if c.category == category]
    if not cats:
        return "-"
    return min(cats, key=lambda c: _STATUS_RANK.get(c.status, 99)).status


def _print_summary(reports: List[AuditReport]) -> None:
    data_reports   = [r for r in reports if r.recommendation in _DATA_RECS]
    nodata_reports = [r for r in reports if r.recommendation not in _DATA_RECS]

    print("-" * 72)
    print("AUDIT SUMMARY")
    print("-" * 72)
    print()

    if data_reports:
        name_w = max((len(r.spec_name) for r in data_reports), default=20)
        rec_w  = max((len(r.recommendation) for r in data_reports), default=14)
        cat_hdr = "  ".join(f"{short:<7}" for _, short in _SUMMARY_CATS)
        sep_cat = "  ".join("-" * 7 for _ in _SUMMARY_CATS)

        print(f"Strategies with backtest data ({len(data_reports)}):")
        print()
        print(f"  {'Strategy':<{name_w}}  PASS  WARN  FAIL  "
              f"{'Recommendation':<{rec_w}}  {cat_hdr}")
        print(f"  {'-'*name_w}  ----  ----  ----  {'-'*rec_w}  {sep_cat}")
        for r in data_reports:
            cats_str = "  ".join(
                f"[{_STATUS_ICON.get(_worst_status(r.checks, cat), '-')}]    "
                for cat, _ in _SUMMARY_CATS
            )
            print(f"  {r.spec_name:<{name_w}}  {r.pass_count:4}  "
                  f"{r.warn_count:4}  {r.fail_count:4}  "
                  f"{r.recommendation:<{rec_w}}  {cats_str}")
        print()

    if nodata_reports:
        print(f"Strategies without backtest data ({len(nodata_reports)}):")
        print()
        for r in nodata_reports:
            print(f"  {r.spec_name:<40}  {r.recommendation}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only pre-approval strategy audit. "
            "No DB writes. Human approval required."
        ),
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec-id", type=int, metavar="ID",
                     help="Audit a single strategy by spec_id")
    grp.add_argument("--all", action="store_true",
                     help="Audit all strategies in strategy_specs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run checks and print output but do not write report files")
    parser.add_argument("--db", default=str(DEFAULT_DB), metavar="PATH",
                        help="Path to hermes_research.db")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS), metavar="DIR",
                        help="Output directory for audit reports")
    args = parser.parse_args()

    db_path     = Path(args.db)
    reports_dir = Path(args.reports_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    conn = _open_ro(db_path)
    exit_code = 0

    try:
        if args.spec_id is not None:
            spec_ids = [args.spec_id]
        else:
            rows     = conn.execute(
                "SELECT spec_id FROM strategy_specs ORDER BY spec_id"
            ).fetchall()
            spec_ids = [r[0] for r in rows]

        if not spec_ids:
            print("No strategies found in strategy_specs.")
            sys.exit(0)

        mode = "DRY-RUN" if args.dry_run else "LIVE"
        print(f"Hermes Strategy Auditor  [{mode}]")
        print(f"  DB          : {db_path}")
        if not args.dry_run:
            print(f"  Reports dir : {reports_dir}")
        print(f"  Strategies  : {len(spec_ids)}")
        print()

        report_paths: List[Tuple[str, str, str]] = []  # (name, md, json)
        all_reports:  List[AuditReport]           = []

        for sid in spec_ids:
            report = audit_spec(conn, sid)
            if report is None:
                print(f"  WARNING: spec_id={sid} not found -- skipped")
                continue

            _print_report(report)
            all_reports.append(report)

            if not args.dry_run:
                md_path, json_path = write_reports(report, reports_dir)
                report_paths.append((report.spec_name, str(md_path), str(json_path)))

            if report.fail_count > 0:
                exit_code = 1

        if args.all and len(all_reports) > 1:
            _print_summary(all_reports)

        if not args.dry_run and report_paths:
            print("Generated reports:")
            for name, md, js in report_paths:
                print(f"  {name}")
                print(f"    MD  : {md}")
                print(f"    JSON: {js}")
            print()

        if args.dry_run:
            print("DRY-RUN complete. No report files written.")

    finally:
        conn.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
