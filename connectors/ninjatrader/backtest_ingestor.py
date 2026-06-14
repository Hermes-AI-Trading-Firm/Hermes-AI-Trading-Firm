#!/usr/bin/env python3
"""
NT8 Backtest Ingestor — connectors/ninjatrader/backtest_ingestor.py

Reads NinjaTrader 8 Strategy Analyzer export files and imports them into
the backtests table in hermes_research.db.

Two accepted file formats (use one or both per run):

  --summary   NT8 Performance Summary CSV (one row = one backtest)
              Produced by: Strategy Analyzer → Performance tab → Export
              Expected columns: Strategy, Instrument, Start Date, End Date,
              Net Profit, Profit Factor, Max. Drawdown, Sharpe Ratio, etc.

  --trade-list  NT8 Trade List CSV (one row per trade)
                Produced by: Strategy Analyzer → Trades tab → Export
                Expected columns: Trade #, Instrument, Market pos., Quantity,
                Entry time, Exit time, Entry price, Exit price, Profit, etc.

When both are supplied, summary provides aggregate metrics and the trade list
provides trade_list_json + equity_curve_json on the same backtest row.

No live trading. No broker connection. No order placement. File import only.

Usage
-----
Import summary only:
    python connectors/ninjatrader/backtest_ingestor.py \\
        --summary connectors/ninjatrader/sample_nt8_backtest_summary.csv \\
        --spec-id 1

Import trade list only:
    python connectors/ninjatrader/backtest_ingestor.py \\
        --trade-list connectors/ninjatrader/sample_nt8_trade_list.csv \\
        --spec-id 1

Import both together (recommended):
    python connectors/ninjatrader/backtest_ingestor.py \\
        --summary  path/to/performance_summary.csv \\
        --trade-list path/to/trade_list.csv \\
        --spec-id 1 --initial-capital 50000

Validate without writing:
    python connectors/ninjatrader/backtest_ingestor.py \\
        --summary connectors/ninjatrader/sample_nt8_backtest_summary.csv \\
        --spec-id 1 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]

DEFAULT_DB = _PROJECT_ROOT / "database" / "hermes_research.db"

# ---------------------------------------------------------------------------
# Column sets
# ---------------------------------------------------------------------------

REQUIRED_SUMMARY_COLS: set[str] = {
    "Strategy", "Instrument", "Start Date", "End Date",
    "Net Profit", "Profit Factor", "# of Trades",
}

REQUIRED_TRADE_LIST_COLS: set[str] = {
    "Instrument", "Market pos.", "Quantity",
    "Entry time", "Exit time", "Entry price", "Exit price", "Profit",
}

# NT8 column name → backtests column name
_SUMMARY_MAP: Dict[str, str] = {
    "Net Profit":            "net_profit",
    "Net Profit %":          "net_profit_pct",       # computed field, not stored directly
    "Gross Profit":          "gross_profit",
    "Gross Loss":            "gross_loss",
    "Commission":            "commission_value",
    "Profit Factor":         "profit_factor",
    "Max. Drawdown":         "max_drawdown",
    "Max. Drawdown %":       "max_drawdown_pct",
    "Sharpe Ratio":          "sharpe_ratio",
    "Sortino Ratio":         "sortino_ratio",
    "Recovery Factor":       "recovery_factor",
    "# of Trades":           "total_trades",
    "% Profitable":          "win_rate",
    "Avg. Trade":            "expectancy_per_trade",
    "Avg. Win":              "average_win",
    "Avg. Loss":             "average_loss",
    "Max. Win":              "max_win",
    "Max. Loss":             "max_loss",
    "Max. Consec. Winners":  "max_consecutive_wins",
    "Max. Consec. Losers":   "max_consecutive_losses",
}

VALID_DIRECTIONS = {"Long": "LONG", "Short": "SHORT"}


# ---------------------------------------------------------------------------
# Dedup index
# ---------------------------------------------------------------------------

def _ensure_dedup_index(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_backtests_dedup
        ON backtests(spec_id, data_start_date, data_end_date)
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def _clean_num(v: Any) -> Optional[float]:
    """Strip $, %, commas; return float or None."""
    if v is None or str(v).strip() in ("", "-", "N/A", "n/a"):
        return None
    s = str(v).strip().lstrip("$").replace(",", "").rstrip("%")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _pct_to_decimal(v: Any) -> Optional[float]:
    """Convert NT8 percentage string (e.g. '62.00%' or '62.00') to 0–1 decimal."""
    f = _clean_num(v)
    if f is None:
        return None
    # NT8 exports percentages as 0–100; normalize to 0–1
    return f / 100.0 if abs(f) >= 1.0 else f


def _int_or_none(v: Any) -> Optional[int]:
    f = _clean_num(v)
    return int(f) if f is not None else None


def _parse_summary_date(s: str) -> str:
    """Normalise NT8 summary date (2026-01-02 or 1/2/2026) to ISO date."""
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _parse_trade_dt(s: str) -> str:
    """Normalise NT8 trade list timestamp to ISO-8601."""
    s = s.strip()
    for fmt in (
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt).isoformat(sep="T", timespec="seconds")
        except ValueError:
            continue
    return s


# ---------------------------------------------------------------------------
# Equity curve builder
# ---------------------------------------------------------------------------

def _build_equity_curve(trades: List[Dict], initial_capital: Optional[float]) -> str:
    """
    Build equity_curve_json from trade list rows using Cum. profit column
    (or accumulated Profit if Cum. profit is absent).
    Returns a JSON array of {"date": ISO, "equity": float}.
    """
    base = initial_capital or 0.0
    points = []
    cum = 0.0

    for row in trades:
        exit_time = _parse_trade_dt(row.get("Exit time", ""))
        cum_val   = _clean_num(row.get("Cum. profit"))
        profit    = _clean_num(row.get("Profit"))

        if cum_val is not None:
            equity = base + cum_val
        else:
            cum += (profit or 0.0)
            equity = base + cum

        points.append({"date": exit_time, "equity": round(equity, 2)})

    return json.dumps(points)


# ---------------------------------------------------------------------------
# Summary import
# ---------------------------------------------------------------------------

def import_backtest_summary(
    conn: sqlite3.Connection,
    path: Path,
    spec_id: int,
    initial_capital: Optional[float] = None,
    is_in_sample: bool = True,
    notes: Optional[str] = None,
    dry_run: bool = False,
) -> Tuple[Optional[int], List[str]]:
    """
    Parse NT8 performance summary CSV and insert one row into backtests.

    Returns (backtest_id, errors). backtest_id is None on dry-run or failure.
    """
    if not path.exists():
        return None, [f"File not found: {path}"]

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows   = list(reader)
        fields = set(reader.fieldnames or [])

    if not rows:
        return None, ["CSV is empty"]

    missing = REQUIRED_SUMMARY_COLS - fields
    if missing:
        return None, [f"Missing required columns: {sorted(missing)}"]

    errors: List[str] = []
    row = rows[0]  # one row per exported backtest

    if len(rows) > 1:
        errors.append(
            f"Warning: {len(rows)} data rows found; only the first row will be imported. "
            "Export one strategy at a time from Strategy Analyzer."
        )

    # --- parse aggregate fields ---
    start_date = _parse_summary_date(row.get("Start Date", ""))
    end_date   = _parse_summary_date(row.get("End Date", ""))

    net_profit   = _clean_num(row.get("Net Profit"))
    gross_profit = _clean_num(row.get("Gross Profit"))
    gross_loss   = _clean_num(row.get("Gross Loss"))
    profit_factor = _clean_num(row.get("Profit Factor"))
    total_trades  = _int_or_none(row.get("# of Trades"))
    win_rate      = _pct_to_decimal(row.get("% Profitable"))
    sharpe        = _clean_num(row.get("Sharpe Ratio"))
    sortino       = _clean_num(row.get("Sortino Ratio"))
    recovery      = _clean_num(row.get("Recovery Factor"))
    max_dd        = _clean_num(row.get("Max. Drawdown"))
    max_dd_pct    = _pct_to_decimal(row.get("Max. Drawdown %"))
    avg_win       = _clean_num(row.get("Avg. Win"))
    avg_loss      = _clean_num(row.get("Avg. Loss"))
    max_win       = _clean_num(row.get("Max. Win"))
    max_loss      = _clean_num(row.get("Max. Loss"))
    exp_per_trade = _clean_num(row.get("Avg. Trade"))
    consec_wins   = _int_or_none(row.get("Max. Consec. Winners"))
    consec_losses = _int_or_none(row.get("Max. Consec. Losers"))
    commission    = _clean_num(row.get("Commission"))

    # derived
    loss_rate = round(1.0 - win_rate, 6) if win_rate is not None else None
    expectancy = net_profit if total_trades and total_trades > 0 and net_profit is not None else None
    winning_trades = round(total_trades * win_rate) if total_trades and win_rate is not None else None
    losing_trades  = (total_trades - winning_trades) if total_trades and winning_trades is not None else None

    strategy_name = (row.get("Strategy") or "").strip()
    instrument    = (row.get("Instrument") or "").strip()
    backtest_name = f"{strategy_name} | {instrument} | {start_date} – {end_date}"

    if not dry_run:
        try:
            cur = conn.execute("""
                INSERT OR IGNORE INTO backtests (
                    spec_id, backtest_name, data_source,
                    data_start_date, data_end_date,
                    commission_type, commission_value,
                    initial_capital,
                    net_profit, gross_profit, gross_loss,
                    profit_factor, win_rate, loss_rate,
                    total_trades, winning_trades, losing_trades,
                    average_win, average_loss,
                    max_win, max_loss,
                    max_drawdown, max_drawdown_pct,
                    recovery_factor, sharpe_ratio, sortino_ratio,
                    expectancy, expectancy_per_trade,
                    max_consecutive_wins, max_consecutive_losses,
                    is_in_sample, notes
                ) VALUES (
                    ?,?,?,
                    ?,?,
                    ?,?,
                    ?,
                    ?,?,?,
                    ?,?,?,
                    ?,?,?,
                    ?,?,
                    ?,?,
                    ?,?,
                    ?,?,?,
                    ?,?,
                    ?,?,
                    ?,?
                )
            """, (
                spec_id, backtest_name, "NT8 Strategy Analyzer",
                start_date, end_date,
                "fixed", commission,
                initial_capital,
                net_profit, gross_profit, gross_loss,
                profit_factor, win_rate, loss_rate,
                total_trades, winning_trades, losing_trades,
                avg_win, avg_loss,
                max_win, max_loss,
                max_dd, max_dd_pct,
                recovery, sharpe, sortino,
                expectancy, exp_per_trade,
                consec_wins, consec_losses,
                1 if is_in_sample else 0,
                notes,
            ))
            conn.commit()

            if cur.rowcount == 0:
                errors.append(
                    f"Duplicate skipped: spec_id={spec_id}, "
                    f"start={start_date}, end={end_date} already exists in backtests."
                )
                return None, errors

            return cur.lastrowid, errors

        except sqlite3.Error as exc:
            return None, errors + [f"Database error: {exc}"]

    # dry-run: validate required numeric fields
    if profit_factor is None:
        errors.append("Warning: Profit Factor could not be parsed")
    if total_trades is None:
        errors.append("Warning: # of Trades could not be parsed")
    if max_dd_pct is None:
        errors.append("Warning: Max. Drawdown % could not be parsed")

    return None, errors


# ---------------------------------------------------------------------------
# Trade list import
# ---------------------------------------------------------------------------

def import_trade_list(
    conn: sqlite3.Connection,
    path: Path,
    spec_id: int,
    backtest_id: Optional[int] = None,
    initial_capital: Optional[float] = None,
    dry_run: bool = False,
) -> Tuple[int, int, List[str]]:
    """
    Parse NT8 trade list CSV.

    If backtest_id is given, updates trade_list_json and equity_curve_json on
    that existing backtest row. Otherwise inserts a new minimal backtest row
    derived entirely from the trade list.

    Returns (inserted_or_updated, skipped, errors).
    """
    if not path.exists():
        return 0, 0, [f"File not found: {path}"]

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows   = list(reader)
        fields = set(reader.fieldnames or [])

    if not rows:
        return 0, 0, ["CSV is empty — nothing to import"]

    missing = REQUIRED_TRADE_LIST_COLS - fields
    if missing:
        return 0, 0, [f"Missing required columns: {sorted(missing)}"]

    errors: List[str] = []
    trade_records: List[Dict] = []

    for lineno, row in enumerate(rows, start=2):
        raw_dir = (row.get("Market pos.") or "").strip().capitalize()
        direction = VALID_DIRECTIONS.get(raw_dir)
        if direction is None:
            errors.append(f"Line {lineno}: unknown Market pos. '{raw_dir}' — skipped")
            continue

        entry_price = _clean_num(row.get("Entry price"))
        exit_price  = _clean_num(row.get("Exit price"))
        quantity    = _int_or_none(row.get("Quantity"))
        profit      = _clean_num(row.get("Profit"))

        if any(v is None for v in (entry_price, exit_price, quantity, profit)):
            errors.append(f"Line {lineno}: non-numeric price/quantity/profit — skipped")
            continue

        trade_records.append({
            "direction":    direction,
            "symbol":       (row.get("Instrument") or "").strip(),
            "entry_time":   _parse_trade_dt(row.get("Entry time", "")),
            "exit_time":    _parse_trade_dt(row.get("Exit time", "")),
            "entry_price":  entry_price,
            "exit_price":   exit_price,
            "quantity":     quantity,
            "pnl":          profit,
            "commission":   _clean_num(row.get("Commission")) or 0.0,
            "slippage":     _clean_num(row.get("Slippage")) or 0.0,
            "cum_profit":   _clean_num(row.get("Cum. profit")),
        })

    if not trade_records:
        return 0, 0, errors + ["No valid trade records found"]

    trade_list_json  = json.dumps(trade_records)
    equity_curve_json = _build_equity_curve(rows, initial_capital)

    if dry_run:
        return len(trade_records), len(rows) - len(trade_records), errors

    try:
        if backtest_id is not None:
            # Attach trade data to existing backtest row
            conn.execute("""
                UPDATE backtests
                SET trade_list_json  = ?,
                    equity_curve_json = ?
                WHERE backtest_id = ? AND spec_id = ?
            """, (trade_list_json, equity_curve_json, backtest_id, spec_id))
            conn.commit()
            return 1, 0, errors

        # No backtest row yet — derive aggregate metrics from the trade list
        profits = [t["pnl"] for t in trade_records]
        wins    = [p for p in profits if p > 0]
        losses  = [p for p in profits if p <= 0]

        total   = len(profits)
        wr      = len(wins) / total if total > 0 else None
        gross_p = sum(wins)
        gross_l = abs(sum(losses))
        pf      = (gross_p / gross_l) if gross_l > 0 else None
        net_p   = sum(profits)
        avg_win  = (sum(wins) / len(wins))    if wins   else None
        avg_loss = (sum(losses) / len(losses)) if losses else None
        exp_pt   = (net_p / total)             if total  else None

        symbol     = trade_records[0]["symbol"] if trade_records else None
        start_date = trade_records[0]["exit_time"][:10] if trade_records else None
        end_date   = trade_records[-1]["exit_time"][:10] if trade_records else None
        bt_name    = f"spec_{spec_id} | {symbol} | {start_date} – {end_date}"

        cur = conn.execute("""
            INSERT OR IGNORE INTO backtests (
                spec_id, backtest_name, data_source,
                data_start_date, data_end_date,
                net_profit, gross_profit, gross_loss,
                profit_factor, win_rate, loss_rate,
                total_trades, winning_trades, losing_trades,
                average_win, average_loss,
                expectancy, expectancy_per_trade,
                initial_capital, is_in_sample,
                trade_list_json, equity_curve_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            spec_id, bt_name, "NT8 Strategy Analyzer (trade list)",
            start_date, end_date,
            net_p, gross_p, gross_l,
            pf, wr, (1.0 - wr) if wr is not None else None,
            total, len(wins), len(losses),
            avg_win, avg_loss,
            net_p, exp_pt,
            initial_capital, 1,
            trade_list_json, equity_curve_json,
        ))
        conn.commit()

        if cur.rowcount == 0:
            return 0, 1, errors + [
                f"Duplicate skipped: spec_id={spec_id}, "
                f"start={start_date}, end={end_date} already exists."
            ]

        return 1, 0, errors

    except sqlite3.Error as exc:
        return 0, 0, errors + [f"Database error: {exc}"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import NT8 Strategy Analyzer exports into hermes_research.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--summary",
        metavar="PATH",
        help="Path to NT8 Performance Summary CSV",
    )
    parser.add_argument(
        "--trade-list",
        metavar="PATH",
        help="Path to NT8 Trade List CSV",
    )
    parser.add_argument(
        "--spec-id",
        type=int,
        required=True,
        metavar="ID",
        help="strategy_specs.spec_id this backtest belongs to",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=None,
        metavar="AMOUNT",
        help="Starting account value (used to build equity curve)",
    )
    parser.add_argument(
        "--oos",
        action="store_true",
        help="Mark this backtest as out-of-sample (is_in_sample=0)",
    )
    parser.add_argument(
        "--notes",
        default=None,
        metavar="TEXT",
        help="Optional notes to store with this backtest",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        metavar="PATH",
        help="Path to hermes_research.db",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and parse without writing to the database",
    )
    args = parser.parse_args()

    if not args.summary and not args.trade_list:
        parser.error("At least one of --summary or --trade-list is required")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    print("Hermes Backtest Ingestor")
    print(f"  DB         : {db_path}")
    print(f"  spec-id    : {args.spec_id}")
    if args.summary:
        print(f"  Summary    : {args.summary}")
    if args.trade_list:
        print(f"  Trade list : {args.trade_list}")
    if args.dry_run:
        print("  Mode       : DRY RUN (no writes)")
    print()

    conn = sqlite3.connect(str(db_path))
    exit_code = 0

    try:
        if not args.dry_run:
            _ensure_dedup_index(conn)

        backtest_id: Optional[int] = None

        # ── Summary ──────────────────────────────────────────────────────────
        if args.summary:
            print("--- Performance Summary ---")
            backtest_id, errs = import_backtest_summary(
                conn,
                path=Path(args.summary),
                spec_id=args.spec_id,
                initial_capital=args.initial_capital,
                is_in_sample=not args.oos,
                notes=args.notes,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print("  Parsed     : OK (dry-run)")
            elif backtest_id:
                print(f"  Inserted   : backtest_id={backtest_id}")
            else:
                print("  Skipped    : duplicate or error")
                exit_code = 1
            for e in errs:
                print(f"  NOTE       : {e}")
            print()

        # ── Trade List ───────────────────────────────────────────────────────
        if args.trade_list:
            print("--- Trade List ---")
            ins, skip, errs = import_trade_list(
                conn,
                path=Path(args.trade_list),
                spec_id=args.spec_id,
                backtest_id=backtest_id,
                initial_capital=args.initial_capital,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(f"  Parsed     : {ins} trade(s) valid, {skip} skipped (dry-run)")
            elif backtest_id:
                print(f"  Updated    : backtest_id={backtest_id} — {ins} trade(s) attached")
            else:
                print(f"  Inserted   : {ins} backtest row(s), {skip} duplicate(s)")
                if skip > 0:
                    exit_code = 1
            for e in errs:
                lvl = "WARN" if "Warning" in e else "ERROR"
                print(f"  {lvl:<6}   : {e}")
                if lvl == "ERROR":
                    exit_code = 1
            print()

        status = "DRY RUN" if args.dry_run else ("OK" if exit_code == 0 else "COMPLETED WITH ERRORS")
        print(f"--- Status: {status} ---")

    finally:
        conn.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
