#!/usr/bin/env python3
"""
NT8 Backtest Ingestor -- connectors/ninjatrader/backtest_ingestor.py

Reads NinjaTrader 8 Strategy Analyzer export files and imports them into
the backtests table in hermes_research.db.

Two accepted file formats (use one or both per run):

  --summary   NT8 Performance Summary CSV (one row = one backtest)
              Produced by: Strategy Analyzer -> Performance tab -> Export
              Expected columns: Strategy, Instrument, Start Date, End Date,
              Net Profit, Profit Factor, Max. Drawdown, Sharpe Ratio, etc.

  --trade-list  NT8 Trade List CSV (one row per trade)
                Produced by: Strategy Analyzer -> Trades tab -> Export
                Expected columns: Trade #, Instrument, Market pos., Quantity,
                Entry time, Exit time, Entry price, Exit price, Profit, etc.

When both are supplied, summary provides aggregate metrics and the trade list
provides trade_list_json + equity_curve_json on the same backtest row.

No live trading. No broker connection. No order placement. File import only.

Usage
-----
Probe a file (no DB required, no spec-id required):
    python connectors/ninjatrader/backtest_ingestor.py --probe \\
        --summary path/to/performance_summary.csv

Probe both files:
    python connectors/ninjatrader/backtest_ingestor.py --probe \\
        --summary path/to/performance_summary.csv \\
        --trade-list path/to/trade_list.csv

Probe with log output:
    python connectors/ninjatrader/backtest_ingestor.py --probe \\
        --summary path/to/performance_summary.csv \\
        --log-dir logs/nt8_probe

Validate against a specific spec (no DB write):
    python connectors/ninjatrader/backtest_ingestor.py --validate-only \\
        --summary path/to/performance_summary.csv \\
        --spec-id 3

Dry-run import (parse + validate, no write):
    python connectors/ninjatrader/backtest_ingestor.py --dry-run \\
        --summary connectors/ninjatrader/sample_nt8_backtest_summary.csv \\
        --spec-id 1

Import summary only:
    python connectors/ninjatrader/backtest_ingestor.py \\
        --summary connectors/ninjatrader/sample_nt8_backtest_summary.csv \\
        --spec-id 1

Import both together (recommended):
    python connectors/ninjatrader/backtest_ingestor.py \\
        --summary  path/to/performance_summary.csv \\
        --trade-list path/to/trade_list.csv \\
        --spec-id 1 --initial-capital 50000
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

# NT8 column name -> backtests column name
_SUMMARY_MAP: Dict[str, str] = {
    "Net Profit":            "net_profit",
    "Net Profit %":          "net_profit_pct",
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

# Structural summary columns that carry metadata but are not in _SUMMARY_MAP
_SUMMARY_STRUCTURAL: set[str] = {"Strategy", "Instrument", "Period", "Start Date", "End Date"}

VALID_DIRECTIONS = {"Long": "LONG", "Short": "SHORT"}

# NT8 column name aliases -- real exports use different names than documented
_COLUMN_ALIASES: Dict[str, str] = {
    "Qty":             "Quantity",
    "Trade number":    "Trade #",
    "Cum. net profit": "Cum. profit",
}

# Grid-format row label (lowercase) -> standard column name
_GRID_LABEL_MAP: Dict[str, str] = {
    "total net profit":     "Net Profit",
    "gross profit":         "Gross Profit",
    "gross loss":           "Gross Loss",
    "commission":           "Commission",
    "profit factor":        "Profit Factor",
    "max. drawdown":        "Max. Drawdown",
    "sharpe ratio":         "Sharpe Ratio",
    "sortino ratio":        "Sortino Ratio",
    "recovery factor":      "Recovery Factor",
    "total # of trades":    "# of Trades",
    "probability":          "% Profitable",
    "avg. trade":           "Avg. Trade",
    "avg. win":             "Avg. Win",
    "avg. loss":            "Avg. Loss",
    "max. win":             "Max. Win",
    "max. loss":            "Max. Loss",
    "max. consec. winners": "Max. Consec. Winners",
    "max. consec. losers":  "Max. Consec. Losers",
    "start date":           "Start Date",
    "end date":             "End Date",
}

# ---------------------------------------------------------------------------
# Sample-file detection
# ---------------------------------------------------------------------------

_SAMPLE_FILENAME_MARKERS = ("sample", "demo", "example", "fixture")


def _is_sample_file(path: Path) -> bool:
    return any(m in path.stem.lower() for m in _SAMPLE_FILENAME_MARKERS)


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
    """Strip $, %, commas, parentheses; return float or None.
    Handles NT8 currency notation: ($182.00) -> -182.00, $359.00 -> 359.00.
    """
    if v is None or str(v).strip() in ("", "-", "N/A", "n/a"):
        return None
    s = str(v).strip()
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = s.lstrip("$").replace(",", "").rstrip("%")
    try:
        result = float(s)
        return -result if negative else result
    except (TypeError, ValueError):
        return None


def _apply_aliases(rows: List[Dict]) -> List[Dict]:
    """Normalize NT8 column name variations to expected names."""
    if not rows:
        return rows
    needs_rename = {k: v for k, v in _COLUMN_ALIASES.items() if k in rows[0]}
    if not needs_rename:
        return rows
    out = []
    for row in rows:
        new_row = {}
        for k, val in row.items():
            new_row[needs_rename.get(k, k)] = val
        out.append(new_row)
    return out


def _is_grid_format(fieldnames: List[str]) -> bool:
    """True if CSV is NT8 grid format (Performance / All trades / Long trades ...)."""
    return bool(fieldnames) and fieldnames[0].strip().lower() in ("performance", "")


def _parse_grid_to_standard_row(rows: List[Dict]) -> Dict[str, str]:
    """Convert grid-format rows to a standard column-oriented row dict.
    Takes the 'All trades' column as the aggregate value for each metric.
    """
    result: Dict[str, str] = {"Strategy": "", "Instrument": ""}
    for row in rows:
        label = (row.get("Performance") or "").strip().lower()
        value = (row.get("All trades") or "").strip()
        std_col = _GRID_LABEL_MAP.get(label)
        if std_col:
            result[std_col] = value
    return result


def _pct_to_decimal(v: Any) -> Optional[float]:
    """Convert NT8 percentage string (e.g. '62.00%' or '62.00') to 0-1 decimal."""
    f = _clean_num(v)
    if f is None:
        return None
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
        rows        = list(reader)
        fields_list = list(reader.fieldnames or [])
        fields      = set(fields_list)

    if not rows:
        return None, ["CSV is empty"]

    # Grid format: convert to standard row before processing
    if _is_grid_format(fields_list):
        rows   = [_parse_grid_to_standard_row(rows)]
        fields = set(rows[0].keys())

    missing = REQUIRED_SUMMARY_COLS - fields
    if missing:
        return None, [f"Missing required columns: {sorted(missing)}"]

    errors: List[str] = []
    row = rows[0]

    if len(rows) > 1:
        errors.append(
            f"Warning: {len(rows)} data rows found; only the first row will be imported. "
            "Export one strategy at a time from Strategy Analyzer."
        )

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

    loss_rate = round(1.0 - win_rate, 6) if win_rate is not None else None
    expectancy = net_profit if total_trades and total_trades > 0 and net_profit is not None else None
    winning_trades = round(total_trades * win_rate) if total_trades and win_rate is not None else None
    losing_trades  = (total_trades - winning_trades) if total_trades and winning_trades is not None else None

    strategy_name = (row.get("Strategy") or "").strip()
    instrument    = (row.get("Instrument") or "").strip()
    backtest_name = f"{strategy_name} | {instrument} | {start_date} - {end_date}"

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
    is_in_sample: bool = True,
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
        return 0, 0, ["CSV is empty -- nothing to import"]

    # Normalize column name aliases
    rows   = _apply_aliases(rows)
    fields = set(rows[0].keys()) if rows else fields

    missing = REQUIRED_TRADE_LIST_COLS - fields
    if missing:
        return 0, 0, [f"Missing required columns: {sorted(missing)}"]

    errors: List[str] = []
    trade_records: List[Dict] = []

    for lineno, row in enumerate(rows, start=2):
        raw_dir = (row.get("Market pos.") or "").strip().capitalize()
        direction = VALID_DIRECTIONS.get(raw_dir)
        if direction is None:
            errors.append(f"Line {lineno}: unknown Market pos. '{raw_dir}' -- skipped")
            continue

        entry_price = _clean_num(row.get("Entry price"))
        exit_price  = _clean_num(row.get("Exit price"))
        quantity    = _int_or_none(row.get("Quantity"))
        profit      = _clean_num(row.get("Profit"))

        if any(v is None for v in (entry_price, exit_price, quantity, profit)):
            errors.append(f"Line {lineno}: non-numeric price/quantity/profit -- skipped")
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

    trade_list_json   = json.dumps(trade_records)
    equity_curve_json = _build_equity_curve(rows, initial_capital)

    if dry_run:
        return len(trade_records), len(rows) - len(trade_records), errors

    try:
        if backtest_id is not None:
            conn.execute("""
                UPDATE backtests
                SET trade_list_json   = ?,
                    equity_curve_json = ?
                WHERE backtest_id = ? AND spec_id = ?
            """, (trade_list_json, equity_curve_json, backtest_id, spec_id))
            conn.commit()
            return 1, 0, errors

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
        bt_name    = f"spec_{spec_id} | {symbol} | {start_date} - {end_date}"

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
            initial_capital, 1 if is_in_sample else 0,
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
# Probe: column map for trade list (probe only -- import_trade_list unchanged)
# ---------------------------------------------------------------------------

_TRADE_LIST_PROBE_MAP: Dict[str, Tuple[str, str]] = {
    "Trade #":      ("trade_num",    "int"),
    "Instrument":   ("symbol",       "str"),
    "Market pos.":  ("direction",    "str"),
    "Quantity":     ("quantity",     "int"),
    "Entry time":   ("entry_time",   "dt"),
    "Exit time":    ("exit_time",    "dt"),
    "Entry price":  ("entry_price",  "float"),
    "Exit price":   ("exit_price",   "float"),
    "Profit":       ("pnl",          "float"),
    "Cum. profit":  ("cum_profit",   "float"),
    "Commission":   ("commission",   "float"),
    "Slippage":     ("slippage",     "float"),
    "MAE":          ("mae",          "float"),
    "MFE":          ("mfe",          "float"),
    "ETD":          ("etd",          "float"),
}


# ---------------------------------------------------------------------------
# Probe: Performance Summary
# ---------------------------------------------------------------------------

def probe_summary(path: Path) -> Dict[str, Any]:
    """
    Inspect a Performance Summary CSV without touching the database.
    Returns a structured result dict. Never writes anything.
    """
    r: Dict[str, Any] = {
        "file":             str(path),
        "exists":           path.exists(),
        "is_sample":        _is_sample_file(path),
        "row_count":        0,
        "columns_found":    [],
        "required_present": [],
        "required_missing": [],
        "mapped":           {},
        "unmapped":         [],
        "parse_warnings":   [],
        "verdict":          "UNKNOWN",
    }

    if not path.exists():
        r["verdict"] = "ERROR -- file not found"
        return r

    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            rows   = list(reader)
            fields = list(reader.fieldnames or [])
    except Exception as exc:
        r["verdict"] = f"ERROR -- could not read: {exc}"
        return r

    r["columns_found"] = fields
    r["row_count"]     = len(rows)

    # Grid format: convert to standard row before column checks
    if _is_grid_format(fields):
        r["is_grid_format"] = True
        std_row = _parse_grid_to_standard_row(rows)
        rows    = [std_row]
        fields  = list(std_row.keys())
        r["columns_found"] = fields

    fields_set = set(fields)
    r["required_present"] = sorted(REQUIRED_SUMMARY_COLS & fields_set)
    r["required_missing"] = sorted(REQUIRED_SUMMARY_COLS - fields_set)

    if rows:
        row = rows[0]
        for nt8_col, db_col in _SUMMARY_MAP.items():
            if nt8_col not in fields_set:
                continue
            raw = row.get(nt8_col)
            if db_col in ("win_rate", "max_drawdown_pct", "net_profit_pct"):
                parsed: Any = _pct_to_decimal(raw)
            elif db_col in ("total_trades", "max_consecutive_wins", "max_consecutive_losses"):
                parsed = _int_or_none(raw)
            else:
                parsed = _clean_num(raw)
            r["mapped"][nt8_col] = {"db_col": db_col, "raw": raw, "parsed": parsed}
            if parsed is None and raw not in (None, "", "-", "N/A", "n/a"):
                r["parse_warnings"].append(
                    f"'{nt8_col}' raw='{raw}' could not be parsed to a number"
                )

    r["unmapped"] = sorted(
        c for c in fields
        if c not in _SUMMARY_MAP and c not in _SUMMARY_STRUCTURAL
    )

    if rows and len(rows) > 1:
        r["parse_warnings"].append(
            f"{len(rows)} data rows found; only row 1 will be imported -- "
            "export one strategy at a time from Strategy Analyzer"
        )

    if r["required_missing"]:
        r["verdict"] = "FAIL -- missing required columns"
    elif r["parse_warnings"]:
        r["verdict"] = "WARN -- parse issues detected"
    else:
        r["verdict"] = "READY"

    return r


# ---------------------------------------------------------------------------
# Probe: Trade List
# ---------------------------------------------------------------------------

def probe_trade_list(path: Path, initial_capital: Optional[float] = None) -> Dict[str, Any]:
    """
    Inspect a Trade List CSV without touching the database.
    Returns a structured result dict. Never writes anything.
    """
    r: Dict[str, Any] = {
        "file":                    str(path),
        "exists":                  path.exists(),
        "is_sample":               _is_sample_file(path),
        "row_count":               0,
        "columns_found":           [],
        "required_present":        [],
        "required_missing":        [],
        "mapped":                  {},
        "unmapped":                [],
        "valid_rows":              0,
        "skipped_rows":            0,
        "trade_list_json_count":   0,
        "equity_curve_json_count": 0,
        "parse_warnings":          [],
        "verdict":                 "UNKNOWN",
    }

    if not path.exists():
        r["verdict"] = "ERROR -- file not found"
        return r

    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            rows   = list(reader)
            fields = list(reader.fieldnames or [])
    except Exception as exc:
        r["verdict"] = f"ERROR -- could not read: {exc}"
        return r

    # Normalize column name aliases before any column checks
    rows   = _apply_aliases(rows)
    fields = list(rows[0].keys()) if rows else fields

    r["columns_found"] = fields
    r["row_count"]     = len(rows)

    fields_set = set(fields)
    r["required_present"] = sorted(REQUIRED_TRADE_LIST_COLS & fields_set)
    r["required_missing"] = sorted(REQUIRED_TRADE_LIST_COLS - fields_set)

    if rows:
        row = rows[0]
        for nt8_col, (db_col, kind) in _TRADE_LIST_PROBE_MAP.items():
            if nt8_col not in fields_set:
                continue
            raw = row.get(nt8_col)
            if kind == "float":
                parsed = _clean_num(raw)
            elif kind == "int":
                parsed = _int_or_none(raw)
            elif kind == "dt":
                parsed = _parse_trade_dt(raw or "")
            else:
                parsed = str(raw).strip() if raw else None
            r["mapped"][nt8_col] = {"db_col": db_col, "raw": raw, "parsed": parsed}

    r["unmapped"] = sorted(c for c in fields if c not in _TRADE_LIST_PROBE_MAP)

    valid: List[Dict] = []
    for lineno, row in enumerate(rows, start=2):
        raw_dir   = (row.get("Market pos.") or "").strip().capitalize()
        direction = VALID_DIRECTIONS.get(raw_dir)
        if direction is None:
            r["parse_warnings"].append(
                f"Line {lineno}: unknown Market pos. '{raw_dir}' -- will skip"
            )
            continue
        ep  = _clean_num(row.get("Entry price"))
        xp  = _clean_num(row.get("Exit price"))
        qty = _int_or_none(row.get("Quantity"))
        pnl = _clean_num(row.get("Profit"))
        if any(v is None for v in (ep, xp, qty, pnl)):
            r["parse_warnings"].append(
                f"Line {lineno}: non-numeric price/qty/profit -- will skip"
            )
            continue
        valid.append(row)

    r["valid_rows"]            = len(valid)
    r["skipped_rows"]          = len(rows) - len(valid)
    r["trade_list_json_count"] = len(valid)

    if valid:
        eq_json = _build_equity_curve(valid, initial_capital)
        r["equity_curve_json_count"] = len(json.loads(eq_json))

    if r["required_missing"]:
        r["verdict"] = "FAIL -- missing required columns"
    elif r["valid_rows"] == 0:
        r["verdict"] = "FAIL -- no valid trade rows"
    elif r["skipped_rows"] > 0 or r["parse_warnings"]:
        r["verdict"] = "WARN -- some rows will be skipped"
    else:
        r["verdict"] = "READY"

    return r


# ---------------------------------------------------------------------------
# Probe report formatter
# ---------------------------------------------------------------------------

def _probe_lines(title: str, r: Dict[str, Any]) -> List[str]:
    """Return probe report as a list of printable lines (ASCII-safe)."""
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    p(f"Hermes NT8 Probe [{title}]")
    p(f"  File       : {r['file']}")
    if r.get("is_sample"):
        p("  [WARN] Sample file detected -- replace with a real NT8 Strategy Analyzer export")

    if not r.get("exists"):
        p("  [ERROR] File not found")
        p()
        return lines

    p(f"  Rows found : {r['row_count']}")
    p()

    # Required columns table
    req_present = set(r.get("required_present", []))
    req_missing = set(r.get("required_missing", []))
    all_req     = sorted(req_present | req_missing)
    p("  Required columns")
    p(f"  {'Column':<32} Status")
    p(f"  {'-'*32} ------")
    for col in all_req:
        status = "OK" if col in req_present else "MISSING"
        p(f"  {col:<32} {status}")
    p()

    # Mapped columns with first-row sample values
    mapped = r.get("mapped", {})
    if mapped:
        p("  Mapped columns  [first row sample]")
        p(f"  {'NT8 column':<26} {'DB column':<26} Parsed value")
        p(f"  {'-'*26} {'-'*26} {'-'*20}")
        for nt8_col, info in mapped.items():
            pv = str(info["parsed"]) if info["parsed"] is not None else "(null)"
            p(f"  {nt8_col:<26} {info['db_col']:<26} {pv}")
        p()

    # Unmapped columns
    unmapped = r.get("unmapped", [])
    if unmapped:
        p(f"  Unmapped columns ({len(unmapped)} -- present in file, not in mapping):")
        for c in unmapped:
            p(f"    {c}")
        p()

    # Trade-list-specific parse counts
    if "valid_rows" in r:
        p("  Parse results")
        p(f"  {'Valid trade rows':<34}: {r['valid_rows']}")
        p(f"  {'Skipped rows':<34}: {r['skipped_rows']}")
        p(f"  {'trade_list_json count':<34}: {r['trade_list_json_count']} trades")
        p(f"  {'equity_curve_json count':<34}: {r['equity_curve_json_count']} points")
        p()

    # Parse warnings
    warnings = r.get("parse_warnings", [])
    if warnings:
        p("  Parse warnings:")
        for w in warnings:
            p(f"    [WARN] {w}")
    else:
        p("  Parse warnings: none")
    p()

    p(f"  Verdict: {r.get('verdict', 'UNKNOWN')}")
    p()

    return lines


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
        default=None,
        metavar="ID",
        help="strategy_specs.spec_id this backtest belongs to (not required for --probe)",
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
        "--probe",
        action="store_true",
        help=(
            "Inspect CSV file(s) and report column mapping, parse results, "
            "and readiness. No DB connection required. No writes."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "Run probe diagnostics AND verify --spec-id exists in the DB. "
            "No import. No writes."
        ),
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        metavar="DIR",
        help=(
            "Directory to write probe/validate log files. "
            "Logs are NOT written unless this flag is provided."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and parse without writing to the database",
    )
    args = parser.parse_args()

    if not args.summary and not args.trade_list:
        parser.error("At least one of --summary or --trade-list is required")

    # --probe: no DB, no spec-id needed
    if args.probe:
        exit_code = 0
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir   = Path(args.log_dir) if args.log_dir else None

        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)

        if args.summary:
            result = probe_summary(Path(args.summary))
            lines  = _probe_lines("Performance Summary", result)
            for line in lines:
                print(line)
            if log_dir:
                stem     = Path(args.summary).stem
                log_path = log_dir / f"probe_summary_{stem}_{timestamp}.log"
                log_path.write_text("\n".join(lines), encoding="utf-8")
                print(f"  Log: {log_path}")
                print()
            if result["verdict"].startswith("FAIL"):
                exit_code = 1

        if args.trade_list:
            result = probe_trade_list(Path(args.trade_list), args.initial_capital)
            lines  = _probe_lines("Trade List", result)
            for line in lines:
                print(line)
            if log_dir:
                stem     = Path(args.trade_list).stem
                log_path = log_dir / f"probe_tradelist_{stem}_{timestamp}.log"
                log_path.write_text("\n".join(lines), encoding="utf-8")
                print(f"  Log: {log_path}")
                print()
            if result["verdict"].startswith("FAIL"):
                exit_code = 1

        sys.exit(exit_code)

    # --validate-only: probe + spec existence check, no writes
    if args.validate_only:
        if args.spec_id is None:
            parser.error("--spec-id is required for --validate-only")

        exit_code = 0
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir   = Path(args.log_dir) if args.log_dir else None
        all_lines: List[str] = []

        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)

        if args.summary:
            result = probe_summary(Path(args.summary))
            lines  = _probe_lines("Performance Summary", result)
            for line in lines:
                print(line)
            all_lines.extend(lines)
            if result["verdict"].startswith("FAIL"):
                exit_code = 1

        if args.trade_list:
            result = probe_trade_list(Path(args.trade_list), args.initial_capital)
            lines  = _probe_lines("Trade List", result)
            for line in lines:
                print(line)
            all_lines.extend(lines)
            if result["verdict"].startswith("FAIL"):
                exit_code = 1

        # Check spec exists in DB
        db_path = Path(args.db)
        spec_check_lines: List[str] = []

        def sp(s: str = "") -> None:
            spec_check_lines.append(s)
            print(s)

        sp("Hermes NT8 Validate-Only [Spec Check]")
        sp(f"  spec_id    : {args.spec_id}")
        sp(f"  DB         : {db_path}")

        if not db_path.exists():
            sp("  [ERROR] Database not found")
            sp(f"  Verdict: FAIL -- DB missing")
            sp()
            exit_code = 1
        else:
            try:
                conn = sqlite3.connect(f"file:///{db_path}?mode=ro", uri=True)
                row = conn.execute(
                    "SELECT spec_name, status FROM strategy_specs WHERE spec_id = ?",
                    (args.spec_id,),
                ).fetchone()
                conn.close()
                if row:
                    sp(f"  Spec found : {row[0]}  status={row[1]}")
                    sp("  Verdict: READY")
                else:
                    sp(f"  [ERROR] spec_id={args.spec_id} not found in strategy_specs")
                    sp("  Verdict: FAIL -- spec not found")
                    exit_code = 1
            except sqlite3.Error as exc:
                sp(f"  [ERROR] DB error: {exc}")
                sp("  Verdict: FAIL -- DB error")
                exit_code = 1
        sp()
        all_lines.extend(spec_check_lines)

        if log_dir:
            log_path = log_dir / f"validate_{args.spec_id}_{timestamp}.log"
            log_path.write_text("\n".join(all_lines), encoding="utf-8")
            print(f"  Log: {log_path}")
            print()

        sys.exit(exit_code)

    # Normal import / dry-run mode
    if args.spec_id is None:
        parser.error("--spec-id is required for import mode (use --probe to inspect without a spec-id)")

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

        if args.trade_list:
            print("--- Trade List ---")
            ins, skip, errs = import_trade_list(
                conn,
                path=Path(args.trade_list),
                spec_id=args.spec_id,
                backtest_id=backtest_id,
                initial_capital=args.initial_capital,
                is_in_sample=not args.oos,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(f"  Parsed     : {ins} trade(s) valid, {skip} skipped (dry-run)")
            elif backtest_id:
                print(f"  Updated    : backtest_id={backtest_id} -- {ins} trade(s) attached")
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
