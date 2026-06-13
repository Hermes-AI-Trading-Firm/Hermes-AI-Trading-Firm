#!/usr/bin/env python3
"""
NT8 Import Layer — connectors/ninjatrader/nt8_sync.py

Reads NT8 export files from nt8_export/ and imports them into hermes_research.db.
Safe to re-run: duplicates are skipped via unique indexes added on first run.

No live trading. No broker connection. No order placement. File import only.

Usage
-----
Default (reads from nt8_export/):
    python connectors/ninjatrader/nt8_sync.py

Point at sample or custom files:
    python connectors/ninjatrader/nt8_sync.py \\
        --trades  connectors/ninjatrader/sample_nt8_trades.csv \\
        --account connectors/ninjatrader/sample_nt8_account_state.json

Validate without writing:
    python connectors/ninjatrader/nt8_sync.py --dry-run

Custom database path:
    python connectors/ninjatrader/nt8_sync.py --db path/to/other.db
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

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]

DEFAULT_DB      = _PROJECT_ROOT / "database" / "hermes_research.db"
DEFAULT_TRADES  = _PROJECT_ROOT / "nt8_export" / "nt8_trades.csv"
DEFAULT_ACCOUNT = _PROJECT_ROOT / "nt8_export" / "nt8_account_state.json"

REQUIRED_TRADE_COLS: set[str] = {
    "strategy_id", "symbol", "direction",
    "entry_time", "exit_time",
    "entry_price", "exit_price",
    "quantity", "pnl",
}

REQUIRED_ACCOUNT_KEYS: set[str] = {
    "account_id", "equity", "snapshot_at",
}

VALID_DIRECTIONS = {"LONG", "SHORT"}


# ---------------------------------------------------------------------------
# Schema: unique indexes for idempotent imports
# ---------------------------------------------------------------------------

def _ensure_dedup_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_nt8_trades_dedup
        ON nt8_trades(account_id, symbol, direction, entry_time, entry_price)
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_nt8_snap_dedup
        ON nt8_account_snapshots(account_id, snapshot_at)
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def _float(v: Any) -> Optional[float]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _parse_dt(s: str) -> str:
    """Normalise various NT8 datetime strings to ISO-8601."""
    s = s.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            return datetime.strptime(s, fmt).isoformat(sep="T", timespec="seconds")
        except ValueError:
            continue
    return s  # pass through if already normalised or unrecognised


# ---------------------------------------------------------------------------
# Trade import
# ---------------------------------------------------------------------------

def import_trades(
    conn: sqlite3.Connection,
    path: Path,
    dry_run: bool = False,
) -> Tuple[int, int, List[str]]:
    """Read nt8_trades.csv and insert into nt8_trades. Returns (inserted, skipped, errors)."""
    if not path.exists():
        return 0, 0, [f"File not found: {path}"]

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = set(reader.fieldnames or [])

    if not rows:
        return 0, 0, ["CSV is empty — nothing to import"]

    missing = REQUIRED_TRADE_COLS - fieldnames
    if missing:
        return 0, 0, [f"Missing required columns: {sorted(missing)}"]

    inserted = skipped = 0
    errors: List[str] = []

    for lineno, row in enumerate(rows, start=2):  # line 1 = header
        try:
            direction = (row.get("direction") or "").strip().upper()
            if direction not in VALID_DIRECTIONS:
                errors.append(f"Line {lineno}: direction '{direction}' is not LONG or SHORT — skipped")
                continue

            entry_price = _float(row.get("entry_price"))
            exit_price  = _float(row.get("exit_price"))
            quantity    = _int(row.get("quantity"))
            pnl         = _float(row.get("pnl"))

            if entry_price is None:
                errors.append(f"Line {lineno}: entry_price is not numeric — skipped")
                continue
            if exit_price is None:
                errors.append(f"Line {lineno}: exit_price is not numeric — skipped")
                continue
            if quantity is None:
                errors.append(f"Line {lineno}: quantity is not numeric — skipped")
                continue
            if pnl is None:
                errors.append(f"Line {lineno}: pnl is not numeric — skipped")
                continue

            strategy_id = (row.get("strategy_id") or "").strip()
            if not strategy_id:
                errors.append(f"Line {lineno}: strategy_id is empty — skipped")
                continue

            params = (
                strategy_id,
                (row.get("account_id") or "").strip() or None,
                (row.get("symbol") or "").strip().upper(),
                direction,
                _parse_dt(row.get("entry_time") or ""),
                _parse_dt(row.get("exit_time")  or ""),
                entry_price,
                exit_price,
                quantity,
                pnl,
                _float(row.get("commission")) or 0.0,
                _float(row.get("slippage"))   or 0.0,
                (row.get("atm_template") or "").strip() or None,
            )

            if not dry_run:
                cur = conn.execute("""
                    INSERT OR IGNORE INTO nt8_trades
                        (strategy_id, account_id, symbol, direction,
                         entry_time, exit_time, entry_price, exit_price,
                         quantity, pnl, commission, slippage, atm_template)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, params)
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1
            else:
                inserted += 1  # dry-run: count as would-be-inserted

        except Exception as exc:
            errors.append(f"Line {lineno}: unexpected error — {exc}")

    if not dry_run and inserted > 0:
        conn.commit()

    return inserted, skipped, errors


# ---------------------------------------------------------------------------
# Account snapshot import
# ---------------------------------------------------------------------------

def import_account(
    conn: sqlite3.Connection,
    path: Path,
    dry_run: bool = False,
) -> Tuple[int, int, List[str]]:
    """Read nt8_account_state.json and insert into nt8_account_snapshots. Returns (inserted, skipped, errors)."""
    if not path.exists():
        return 0, 0, [f"File not found: {path}"]

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return 0, 0, [f"JSON parse error: {exc}"]

    records = data if isinstance(data, list) else [data]

    inserted = skipped = 0
    errors: List[str] = []

    for idx, rec in enumerate(records, start=1):
        label = f"Record {idx}"

        missing = REQUIRED_ACCOUNT_KEYS - set(rec.keys())
        if missing:
            errors.append(f"{label}: missing required keys {sorted(missing)} — skipped")
            continue

        equity = _float(rec.get("equity"))
        if equity is None:
            errors.append(f"{label}: equity is not numeric — skipped")
            continue

        account_id = str(rec.get("account_id", "")).strip()
        snapshot_at = _parse_dt(str(rec.get("snapshot_at", "")))

        params = (
            account_id,
            equity,
            _float(rec.get("daily_pnl")),
            _float(rec.get("daily_pnl_pct")),
            _float(rec.get("open_drawdown")),
            _float(rec.get("trailing_drawdown_used")),
            _float(rec.get("trailing_drawdown_limit")),
            _float(rec.get("daily_loss_limit")),
            rec.get("active_strategy_id"),
            snapshot_at,
        )

        if not dry_run:
            cur = conn.execute("""
                INSERT OR IGNORE INTO nt8_account_snapshots
                    (account_id, equity, daily_pnl, daily_pnl_pct,
                     open_drawdown, trailing_drawdown_used, trailing_drawdown_limit,
                     daily_loss_limit, active_strategy_id, snapshot_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, params)
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
        else:
            inserted += 1

    if not dry_run and inserted > 0:
        conn.commit()

    return inserted, skipped, errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import NT8 export files into hermes_research.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--trades",   default=str(DEFAULT_TRADES),  metavar="PATH", help="Path to nt8_trades.csv")
    parser.add_argument("--account",  default=str(DEFAULT_ACCOUNT), metavar="PATH", help="Path to nt8_account_state.json")
    parser.add_argument("--db",       default=str(DEFAULT_DB),      metavar="PATH", help="Path to hermes_research.db")
    parser.add_argument("--dry-run",  action="store_true",                          help="Validate and count without writing to DB")
    args = parser.parse_args()

    trades_path  = Path(args.trades)
    account_path = Path(args.account)
    db_path      = Path(args.db)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    print("Hermes NT8 Import Layer")
    print(f"  DB      : {db_path}")
    print(f"  Trades  : {trades_path}")
    print(f"  Account : {account_path}")
    if args.dry_run:
        print("  Mode    : DRY RUN (no writes)")
    print()

    conn = sqlite3.connect(str(db_path))
    exit_code = 0

    try:
        if not args.dry_run:
            _ensure_dedup_indexes(conn)

        # --- Trades ---
        print("--- Trades ---")
        t_ins, t_skip, t_errs = import_trades(conn, trades_path, dry_run=args.dry_run)
        print(f"  Inserted : {t_ins}")
        print(f"  Skipped  : {t_skip}  (duplicates)")
        for e in t_errs:
            print(f"  ERROR    : {e}")
        print()

        # --- Account Snapshot ---
        print("--- Account Snapshot ---")
        a_ins, a_skip, a_errs = import_account(conn, account_path, dry_run=args.dry_run)
        print(f"  Inserted : {a_ins}")
        print(f"  Skipped  : {a_skip}  (duplicates)")
        for e in a_errs:
            print(f"  ERROR    : {e}")
        print()

        # --- Summary ---
        total_errs = len(t_errs) + len(a_errs)
        status = "DRY RUN" if args.dry_run else ("OK" if total_errs == 0 else "COMPLETED WITH ERRORS")
        print("--- Summary ---")
        print(f"  Trades inserted   : {t_ins}")
        print(f"  Snapshots inserted: {a_ins}")
        print(f"  Errors            : {total_errs}")
        print(f"  Status            : {status}")

        if total_errs:
            exit_code = 1

    finally:
        conn.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
