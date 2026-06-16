#!/usr/bin/env python3
"""
Regime Analysis Engine -- research/regime/regime_analyzer.py

Answers: "How does this strategy perform across different market regimes
or time windows?"

Two modes
---------
1. Internal time-window analysis (default)
   Group trades by month (default) or quarter.
   Label each window Strong / Neutral / Weak based on PF and expectancy.

2. Label file mode (--label-file)
   User supplies a CSV mapping date ranges to regime names.
   Engine groups trades by regime label and computes per-regime metrics.

   CSV format:
       start_date,end_date,regime_label
       2026-01-01,2026-03-31,Bull
       2026-04-01,2026-06-30,Sideways

Window labels
-------------
  Strong   PF >= 1.5  AND  expectancy/trade > 0  AND  win rate >= 55%
  Neutral  PF >= 1.0  AND  expectancy/trade > 0
  Weak     anything else

Metrics per window
------------------
  trade_count, winning_trades, losing_trades
  win_rate, net_pnl, gross_profit, gross_loss
  profit_factor (None when no losing trades)
  expectancy_per_trade
  max_drawdown_dollars (peak-to-trough within window)

No DB writes. No schema changes. No live trading. No broker connection.
Human approval required before any strategy advances beyond REVIEW_REQUIRED.

Usage
-----
    # Monthly windows (default)
    python -m research.regime.regime_analyzer --spec-id N

    # Quarterly windows
    python -m research.regime.regime_analyzer --spec-id N --window quarterly

    # User-defined regime labels
    python -m research.regime.regime_analyzer --spec-id N \
        --label-file research/regime/sample_regime_labels.csv

    # All specs
    python -m research.regime.regime_analyzer --all

    # Dry-run (console output only, no report files)
    python -m research.regime.regime_analyzer --spec-id N --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB      = _PROJECT_ROOT / "database" / "hermes_research.db"
DEFAULT_REPORTS = _PROJECT_ROOT / "reports" / "regime"

# Window labeling thresholds
_STRONG_PF = 1.5
_STRONG_WR = 0.55
_NEUTRAL_PF = 1.0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RegimeWindow:
    label:                str
    regime:               str            # Strong / Neutral / Weak / No Data
    trade_count:          int
    winning_trades:       int
    losing_trades:        int
    win_rate:             float
    net_pnl:              float
    gross_profit:         float
    gross_loss:           float
    profit_factor:        Optional[float]  # None = all wins (no losses)
    expectancy_per_trade: float
    max_drawdown_dollars: float
    start_date:           str
    end_date:             str


@dataclass
class RegimeResult:
    spec_id:          int
    spec_name:        str
    backtest_id:      int
    mode:             str              # monthly / quarterly / label_file
    label_file:       Optional[str]
    total_trades:     int
    unmatched_trades: int              # label_file mode: trades outside any range
    windows:          List[RegimeWindow] = field(default_factory=list)
    best_window:      Optional[str]    = None
    worst_window:     Optional[str]    = None
    ran_at:           str              = ""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fetch_spec(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT spec_id, spec_name FROM strategy_specs WHERE spec_id = ?",
        (spec_id,),
    ).fetchone()
    return {"spec_id": row[0], "spec_name": row[1]} if row else None


def _fetch_latest_is_backtest(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT backtest_id, trade_list_json, data_start_date, data_end_date
        FROM backtests
        WHERE spec_id = ?
          AND is_in_sample = 1
          AND trade_list_json IS NOT NULL
        ORDER BY backtest_id DESC
        LIMIT 1
    """, (spec_id,)).fetchone()
    if row is None:
        return None
    return {
        "backtest_id":      row[0],
        "trade_list_json":  row[1],
        "data_start_date":  row[2],
        "data_end_date":    row[3],
    }


def _all_spec_ids(conn: sqlite3.Connection) -> List[int]:
    return [r[0] for r in conn.execute(
        "SELECT spec_id FROM strategy_specs ORDER BY spec_id"
    ).fetchall()]


# ---------------------------------------------------------------------------
# Trade parsing
# ---------------------------------------------------------------------------

def _parse_trades(trade_list_json: str) -> List[Dict]:
    """Parse trade list JSON, filtering to trades with entry_time and pnl."""
    try:
        trades = json.loads(trade_list_json)
    except (json.JSONDecodeError, TypeError):
        return []
    return [
        t for t in trades
        if isinstance(t, dict) and "entry_time" in t and "pnl" in t
    ]


def _trade_date(t: Dict) -> str:
    return str(t["entry_time"])[:10]


# ---------------------------------------------------------------------------
# Window metric computation
# ---------------------------------------------------------------------------

def _label_window(
    trade_count: int,
    profit_factor: Optional[float],
    expectancy_per_trade: float,
    win_rate: float,
) -> str:
    if trade_count == 0:
        return "No Data"
    pf = profit_factor if profit_factor is not None else 99.0
    if pf >= _STRONG_PF and expectancy_per_trade > 0 and win_rate >= _STRONG_WR:
        return "Strong"
    if pf >= _NEUTRAL_PF and expectancy_per_trade > 0:
        return "Neutral"
    return "Weak"


def _compute_window(
    label: str,
    trades: List[Dict],
    start_date: str,
    end_date: str,
) -> RegimeWindow:
    pnls     = [float(t["pnl"]) for t in trades]
    winning  = [p for p in pnls if p > 0]
    losing   = [p for p in pnls if p < 0]

    gross_profit = sum(winning)
    gross_loss   = abs(sum(losing))
    net_pnl      = sum(pnls)
    count        = len(pnls)

    pf  = None if gross_loss == 0 else round(gross_profit / gross_loss, 4)
    wr  = len(winning) / count if count else 0.0
    exp = net_pnl / count if count else 0.0

    # Peak-to-trough drawdown in dollars within this window
    running = 0.0
    peak    = 0.0
    max_dd  = 0.0
    for p in pnls:
        running += p
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    regime = _label_window(count, pf, exp, wr)

    return RegimeWindow(
        label                = label,
        regime               = regime,
        trade_count          = count,
        winning_trades       = len(winning),
        losing_trades        = len(losing),
        win_rate             = round(wr, 4),
        net_pnl              = round(net_pnl, 2),
        gross_profit         = round(gross_profit, 2),
        gross_loss           = round(gross_loss, 2),
        profit_factor        = pf,
        expectancy_per_trade = round(exp, 2),
        max_drawdown_dollars = round(max_dd, 2),
        start_date           = start_date,
        end_date             = end_date,
    )


# ---------------------------------------------------------------------------
# Grouping strategies
# ---------------------------------------------------------------------------

def _group_by_month(trades: List[Dict]) -> Dict[str, List[Dict]]:
    groups: Dict[str, List[Dict]] = {}
    for t in trades:
        key = _trade_date(t)[:7]    # YYYY-MM
        groups.setdefault(key, []).append(t)
    return dict(sorted(groups.items()))


def _group_by_quarter(trades: List[Dict]) -> Dict[str, List[Dict]]:
    groups: Dict[str, List[Dict]] = {}
    for t in trades:
        d     = _trade_date(t)
        year  = int(d[:4])
        month = int(d[5:7])
        q     = (month - 1) // 3 + 1
        key   = f"{year}-Q{q}"
        groups.setdefault(key, []).append(t)
    return dict(sorted(groups.items()))


def _month_bounds(label: str) -> Tuple[str, str]:
    """'2026-01' -> ('2026-01-01', '2026-01-31')"""
    import calendar
    year, month = int(label[:4]), int(label[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return f"{label}-01", f"{label}-{last_day:02d}"


def _quarter_bounds(label: str) -> Tuple[str, str]:
    """'2026-Q1' -> ('2026-01-01', '2026-03-31')"""
    import calendar
    year = int(label[:4])
    q    = int(label[6])
    first_month = (q - 1) * 3 + 1
    last_month  = q * 3
    last_day    = calendar.monthrange(year, last_month)[1]
    return (f"{year}-{first_month:02d}-01",
            f"{year}-{last_month:02d}-{last_day:02d}")


# ---------------------------------------------------------------------------
# Label file
# ---------------------------------------------------------------------------

def _load_label_file(path: Path) -> List[Tuple[str, str, str]]:
    """Returns list of (start_date, end_date, label) sorted by start_date."""
    labels: List[Tuple[str, str, str]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            labels.append((
                row["start_date"].strip(),
                row["end_date"].strip(),
                row["regime_label"].strip(),
            ))
    return sorted(labels, key=lambda x: x[0])


def _group_by_labels(
    trades: List[Dict],
    labels: List[Tuple[str, str, str]],
) -> Tuple[Dict[str, List[Dict]], List[Dict]]:
    """
    Returns (groups_dict, unmatched_trades).
    groups_dict maps regime_label -> list of trades falling in that range.
    """
    groups: Dict[str, List[Dict]] = {}
    unmatched: List[Dict] = []
    for t in trades:
        date    = _trade_date(t)
        matched = False
        for start, end, label in labels:
            if start <= date <= end:
                groups.setdefault(label, []).append(t)
                matched = True
                break
        if not matched:
            unmatched.append(t)
    return groups, unmatched


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def run_regime_for_spec(
    conn:        sqlite3.Connection,
    spec_id:     int,
    window:      str              = "monthly",
    label_file:  Optional[Path]  = None,
) -> Tuple[Optional[RegimeResult], Optional[str]]:
    """
    Run regime analysis for one spec.
    Returns (RegimeResult, None) on success.
    Returns (None, reason_str) on skip.
    """
    spec = _fetch_spec(conn, spec_id)
    if spec is None:
        return None, f"spec_id={spec_id} not found"

    bt = _fetch_latest_is_backtest(conn, spec_id)
    if bt is None:
        return None, "No IS backtest with trade_list_json -- re-import with real NT8 export"

    trades = _parse_trades(bt["trade_list_json"])
    if len(trades) < 2:
        return None, f"Need at least 2 trades (found {len(trades)})"

    now       = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    windows:  List[RegimeWindow] = []
    unmatched = 0

    if label_file is not None:
        labels = _load_label_file(label_file)
        groups, unmatched_trades = _group_by_labels(trades, labels)
        unmatched = len(unmatched_trades)
        mode = "label_file"
        for start, end, label in labels:
            group = groups.get(label, [])
            windows.append(_compute_window(label, group, start, end))
    elif window == "quarterly":
        groups = _group_by_quarter(trades)
        mode = "quarterly"
        for label, group in groups.items():
            s, e = _quarter_bounds(label)
            windows.append(_compute_window(label, group, s, e))
    else:
        groups = _group_by_month(trades)
        mode = "monthly"
        for label, group in groups.items():
            s, e = _month_bounds(label)
            windows.append(_compute_window(label, group, s, e))

    # Best / worst by expectancy_per_trade (excludes No Data windows)
    data_windows = [w for w in windows if w.trade_count > 0]
    best  = max(data_windows, key=lambda w: w.expectancy_per_trade, default=None)
    worst = min(data_windows, key=lambda w: w.expectancy_per_trade, default=None)

    return RegimeResult(
        spec_id          = spec_id,
        spec_name        = spec["spec_name"],
        backtest_id      = bt["backtest_id"],
        mode             = mode,
        label_file       = str(label_file) if label_file else None,
        total_trades     = len(trades),
        unmatched_trades = unmatched,
        windows          = windows,
        best_window      = best.label  if best  else None,
        worst_window     = worst.label if worst else None,
        ran_at           = now,
    ), None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_REGIME_ICON = {"Strong": "+", "Neutral": "~", "Weak": "X", "No Data": "-"}


def _pf_str(pf: Optional[float]) -> str:
    return "inf" if pf is None else f"{pf:.2f}"


def _to_markdown(r: RegimeResult, dry_run: bool = False) -> str:
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    mode_label = {
        "monthly":    "Monthly windows",
        "quarterly":  "Quarterly windows",
        "label_file": f"Label file: {r.label_file}",
    }.get(r.mode, r.mode)

    p(f"# Regime Analysis Report: {r.spec_name}")
    p(f"**Run date:** {r.ran_at[:10]}")
    p(f"**spec_id:** {r.spec_id}  |  "
      f"**backtest_id:** {r.backtest_id}  |  "
      f"**Mode:** {mode_label}")
    if dry_run:
        p()
        p("> **DRY-RUN** -- no report files written")
    p()
    p("---")
    p()
    p("## Regime Windows")
    p()
    p(f"| Window | Regime | Trades | Net P&L | Win% | PF | Exp/Trade | Max DD$ |")
    p(f"|--------|--------|--------|---------|------|----|-----------|---------|")
    for w in r.windows:
        icon  = _REGIME_ICON.get(w.regime, "?")
        pf_s  = _pf_str(w.profit_factor)
        best  = " **[BEST]**"  if w.label == r.best_window  else ""
        worst = " **[WORST]**" if w.label == r.worst_window else ""
        badge = best or worst
        p(f"| {w.label}{badge} | [{icon}] {w.regime} | {w.trade_count} | "
          f"${w.net_pnl:,.2f} | {w.win_rate:.1%} | {pf_s}x | "
          f"${w.expectancy_per_trade:,.2f} | ${w.max_drawdown_dollars:,.2f} |")
    p()

    if r.unmatched_trades > 0:
        p(f"> **{r.unmatched_trades} trade(s)** fell outside all label ranges "
          f"and are excluded from the table above.")
        p()

    p("## Summary")
    p()
    p(f"- **Total trades analysed:** {r.total_trades - r.unmatched_trades} "
      f"of {r.total_trades}")
    if r.best_window:
        bw = next(w for w in r.windows if w.label == r.best_window)
        p(f"- **Best window:** {r.best_window}  "
          f"({bw.regime}, exp/trade=${bw.expectancy_per_trade:,.2f})")
    if r.worst_window and r.worst_window != r.best_window:
        ww = next(w for w in r.windows if w.label == r.worst_window)
        p(f"- **Worst window:** {r.worst_window}  "
          f"({ww.regime}, exp/trade=${ww.expectancy_per_trade:,.2f})")

    p()
    p("## Window Labels")
    p()
    p(f"| Label | Rule |")
    p(f"|-------|------|")
    p(f"| Strong | PF >= {_STRONG_PF}  AND  exp/trade > 0  AND  win rate >= {_STRONG_WR:.0%} |")
    p(f"| Neutral | PF >= {_NEUTRAL_PF}  AND  exp/trade > 0 |")
    p(f"| Weak | anything else |")
    p()
    p("---")
    p()
    p("*Read-only analysis. No DB writes. "
      "Human approval required before any strategy advances beyond REVIEW_REQUIRED.*")

    return "\n".join(lines)


def _to_dict(r: RegimeResult, dry_run: bool = False) -> Dict:
    return {
        "spec_id":          r.spec_id,
        "spec_name":        r.spec_name,
        "backtest_id":      r.backtest_id,
        "mode":             r.mode,
        "label_file":       r.label_file,
        "dry_run":          dry_run,
        "total_trades":     r.total_trades,
        "unmatched_trades": r.unmatched_trades,
        "best_window":      r.best_window,
        "worst_window":     r.worst_window,
        "ran_at":           r.ran_at,
        "windows": [
            {
                "label":                w.label,
                "regime":               w.regime,
                "date_range":           f"{w.start_date} -> {w.end_date}",
                "trade_count":          w.trade_count,
                "winning_trades":       w.winning_trades,
                "losing_trades":        w.losing_trades,
                "win_rate":             w.win_rate,
                "net_pnl":              w.net_pnl,
                "gross_profit":         w.gross_profit,
                "gross_loss":           w.gross_loss,
                "profit_factor":        w.profit_factor,
                "expectancy_per_trade": w.expectancy_per_trade,
                "max_drawdown_dollars": w.max_drawdown_dollars,
            }
            for w in r.windows
        ],
    }


def write_reports(
    r: RegimeResult, reports_dir: Path, dry_run: bool = False
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = r.ran_at[:10].replace("-", "")
    safe_name = re.sub(r"[^\w\-]", "_", r.spec_name)

    md_path   = reports_dir / f"{safe_name}_regime_analysis_{date_str}.md"
    json_path = reports_dir / f"{safe_name}_regime_analysis_{date_str}.json"

    md_path.write_text(_to_markdown(r, dry_run=dry_run), encoding="utf-8")
    json_path.write_text(
        json.dumps(_to_dict(r, dry_run=dry_run), indent=2), encoding="utf-8"
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_result(r: RegimeResult, dry_run: bool = False) -> None:
    tag = "  [DRY-RUN]" if dry_run else ""
    mode_label = {
        "monthly":    "monthly windows",
        "quarterly":  "quarterly windows",
        "label_file": f"label file: {r.label_file}",
    }.get(r.mode, r.mode)

    print(f"REGIME: {r.spec_name}  [spec_id={r.spec_id}]{tag}")
    print(f"  Backtest : bt_id={r.backtest_id}  trades={r.total_trades}  "
          f"mode={mode_label}")
    if r.unmatched_trades:
        print(f"  Unmatched: {r.unmatched_trades} trade(s) outside label ranges")
    print()

    col_w = max(len(w.label) for w in r.windows) if r.windows else 10
    col_w = max(col_w, 10)
    print(f"  {'Window':<{col_w}}  {'Trades':>6}  {'Net P&L':>10}  "
          f"{'Win%':>6}  {'PF':>6}  {'Exp/Trade':>10}  {'Max DD$':>8}  Regime")
    print(f"  {'-'*col_w}  {'-'*6}  {'-'*10}  "
          f"{'-'*6}  {'-'*6}  {'-'*10}  {'-'*8}  ------")

    for w in r.windows:
        icon   = _REGIME_ICON.get(w.regime, "?")
        pf_s   = _pf_str(w.profit_factor)
        marker = ""
        if w.label == r.best_window and w.label == r.worst_window:
            marker = " [only]"
        elif w.label == r.best_window:
            marker = " [best]"
        elif w.label == r.worst_window:
            marker = " [worst]"
        print(f"  {w.label:<{col_w}}  {w.trade_count:>6}  "
              f"${w.net_pnl:>9,.2f}  {w.win_rate:>5.1%}  {pf_s:>6}  "
              f"${w.expectancy_per_trade:>9,.2f}  ${w.max_drawdown_dollars:>7,.2f}  "
              f"[{icon}] {w.regime}{marker}")
    print()

    if r.best_window:
        bw = next(w for w in r.windows if w.label == r.best_window)
        print(f"  Best : {r.best_window}  ({bw.regime}, exp/trade=${bw.expectancy_per_trade:,.2f})")
    if r.worst_window and r.worst_window != r.best_window:
        ww = next(w for w in r.windows if w.label == r.worst_window)
        print(f"  Worst: {r.worst_window}  ({ww.regime}, exp/trade=${ww.expectancy_per_trade:,.2f})")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Regime Analysis Engine -- group trades by time window or label file. "
            "No DB writes. No live trading. Human approval required."
        ),
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec-id", type=int, metavar="ID",
                     help="Analyse one spec")
    grp.add_argument("--all", action="store_true",
                     help="Analyse all specs")
    parser.add_argument("--window", choices=["monthly", "quarterly"],
                        default="monthly",
                        help="Time-window grouping (default: monthly)")
    parser.add_argument("--label-file", metavar="CSV",
                        help="Path to regime label CSV (overrides --window)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Console output only -- no report files written")
    parser.add_argument("--db",          default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS), metavar="DIR")
    args = parser.parse_args()

    db_path     = Path(args.db)
    reports_dir = Path(args.reports_dir)
    label_file  = Path(args.label_file) if args.label_file else None

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    if label_file and not label_file.exists():
        print(f"ERROR: Label file not found: {label_file}")
        sys.exit(1)

    mode_desc = (f"label file: {label_file}" if label_file
                 else f"{args.window} windows")
    mode_tag  = "DRY-RUN" if args.dry_run else "LIVE"

    print(f"Hermes Regime Analyzer  [{mode_tag}]")
    print(f"  DB      : {db_path}")
    print(f"  Mode    : {mode_desc}")
    if not args.dry_run:
        print(f"  Reports : {reports_dir}")
    print()

    conn = sqlite3.connect(str(db_path))
    try:
        spec_ids = [args.spec_id] if args.spec_id is not None else _all_spec_ids(conn)

        if not spec_ids:
            print("No specs found.")
            sys.exit(0)

        results: List[RegimeResult] = []
        skipped: List[Tuple[int, str]] = []

        for sid in spec_ids:
            result, reason = run_regime_for_spec(
                conn       = conn,
                spec_id    = sid,
                window     = args.window,
                label_file = label_file,
            )

            if result is None:
                skipped.append((sid, reason))
                print(f"  SKIP spec_id={sid}: {reason}")
                print()
                continue

            _print_result(result, dry_run=args.dry_run)

            if not args.dry_run:
                md_path, json_path = write_reports(result, reports_dir)
                print(f"  Reports")
                print(f"    MD  : {md_path}")
                print(f"    JSON: {json_path}")
                print()

            results.append(result)

        # --all summary
        if args.all and results:
            print("-" * 60)
            print("REGIME SUMMARY")
            print("-" * 60)
            print()
            for r in results:
                strong  = sum(1 for w in r.windows if w.regime == "Strong")
                neutral = sum(1 for w in r.windows if w.regime == "Neutral")
                weak    = sum(1 for w in r.windows if w.regime == "Weak")
                windows = len([w for w in r.windows if w.trade_count > 0])
                print(f"  {r.spec_name}  ({windows} windows)  "
                      f"Strong={strong}  Neutral={neutral}  Weak={weak}  "
                      f"best={r.best_window or '-'}  worst={r.worst_window or '-'}")
            print()

        if skipped:
            print(f"Skipped ({len(skipped)}):")
            for sid, reason in skipped:
                print(f"  spec_id={sid}: {reason}")
            print()

        if args.dry_run:
            print("DRY-RUN complete. No report files written.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
