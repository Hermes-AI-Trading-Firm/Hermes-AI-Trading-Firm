#!/usr/bin/env python3
"""
Strategy Spec Importer — connectors/strategy_specs/spec_importer.py

Reads a strategy specification file (YAML, JSON, or Markdown frontmatter)
and imports it into the strategy_specs table in hermes_research.db.

A minimal strategy_idea row is also created (or found) to carry the
strategy_type field, linked to the spec via idea_id.

Human approval gate: status 'approved' and 'rejected' cannot be set via
import.  Any such value is replaced with 'draft' and a warning is emitted.

No live trading. No broker connection. No order placement. Local file import only.

Usage
-----
Dry-run (validate without writing):
    python connectors/strategy_specs/spec_importer.py \\
        --file connectors/strategy_specs/sample_strategy_spec.json --dry-run

Real import:
    python connectors/strategy_specs/spec_importer.py \\
        --file connectors/strategy_specs/sample_strategy_spec.yaml

Update if spec_name already exists:
    python connectors/strategy_specs/spec_importer.py \\
        --file connectors/strategy_specs/sample_strategy_spec.yaml --update-existing
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB = _PROJECT_ROOT / "database" / "hermes_research.db"

# ---------------------------------------------------------------------------
# PyYAML — optional, detected at runtime
# ---------------------------------------------------------------------------

try:
    import yaml as _yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: set[str] = {
    "spec_name", "instrument", "timeframe", "strategy_type",
    "description", "entry_rules", "exit_rules", "risk_rules",
}

# Aliases accepted in status field → normalised to a safe value
_STATUS_ALIASES: Dict[str, str] = {
    "idea":        "draft",
    "researching": "draft",
    "research":    "draft",
    "pending":     "draft",
    "new":         "draft",
    "in_progress": "draft",
}

VALID_STATUSES: set[str] = {
    "draft", "spec_created", "coding", "backtesting",
    "optimized", "regime_analyzed",
}

# Human approval gate — these cannot be set via import
_PROTECTED_STATUSES: set[str] = {"approved", "rejected"}

DEFAULT_STATUS = "draft"

# Futures root symbols for instrument classification
_FUTURES_ROOTS: set[str] = {
    "ES", "NQ", "MNQ", "MES", "RTY", "MYM", "YM", "M2K",
    "CL", "QM", "GC", "MGC", "SI", "ZB", "ZN", "ZF", "ZT",
    "NG", "ZC", "ZS", "ZW", "6E", "6J", "6B", "6A", "6C", "VX",
}

_CRYPTO_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "PERP")


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Tuple[Dict[str, Any], List[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}, ["JSON root must be an object (dict)"]
        return data, []
    except json.JSONDecodeError as exc:
        return {}, [f"JSON parse error: {exc}"]


def _load_yaml(path: Path) -> Tuple[Dict[str, Any], List[str]]:
    if not YAML_AVAILABLE:
        return {}, ["PyYAML is not installed — cannot parse YAML files"]
    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}, ["YAML root must be a mapping"]
        return data, []
    except Exception as exc:
        return {}, [f"YAML parse error: {exc}"]


def _load_md_frontmatter(path: Path) -> Tuple[Dict[str, Any], List[str]]:
    """Extract YAML frontmatter from a Markdown file (between --- delimiters)."""
    if not YAML_AVAILABLE:
        return {}, ["PyYAML is not installed — cannot parse Markdown frontmatter"]
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}, ["No YAML frontmatter found (expected --- delimiters at top of file)"]
    try:
        data = _yaml.safe_load(match.group(1))
        if not isinstance(data, dict):
            return {}, ["Frontmatter must be a YAML mapping"]
        return data, []
    except Exception as exc:
        return {}, [f"Frontmatter YAML parse error: {exc}"]


def _load_file(path: Path) -> Tuple[Dict[str, Any], List[str]]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json(path)
    if suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    if suffix == ".md":
        return _load_md_frontmatter(path)
    return {}, [f"Unsupported file type '{suffix}' — use .json, .yaml, .yml, or .md"]


# ---------------------------------------------------------------------------
# Instrument classification
# ---------------------------------------------------------------------------

def _classify_instrument(raw: str) -> Tuple[str, str]:
    """
    Map an instrument string to (asset_class, symbol).

    Strips contract months and exchange suffixes before classifying.
    """
    # Take first token: "NQ 09-26" → "NQ", "ESZ26" → keep for stripping
    token = re.split(r"[\s/]", raw.strip())[0].upper()

    # Strip trailing contract month pattern: letters+digits at end (ESZ26 → ES)
    alpha_root_match = re.match(r"^([A-Z]{1,4})[A-Z]\d{2}$", token)
    root = alpha_root_match.group(1) if alpha_root_match else token

    if root in _FUTURES_ROOTS:
        return "futures", root

    for suffix in _CRYPTO_QUOTE_SUFFIXES:
        if token.endswith(suffix) and token != suffix:
            return "crypto", token

    # Options heuristic: contains spaces like "SPX OPTION"
    if "OPTION" in raw.upper():
        return "options", root

    return "stocks", root


# ---------------------------------------------------------------------------
# Validation and normalisation
# ---------------------------------------------------------------------------

def _normalize_status(raw: Any) -> Tuple[str, List[str]]:
    """Normalise status string; block protected values; warn on aliases."""
    warnings: List[str] = []
    s = str(raw).strip().lower() if raw else ""

    if not s:
        return DEFAULT_STATUS, []

    if s in _PROTECTED_STATUSES:
        warnings.append(
            f"Human approval gate: status '{s}' cannot be set via import — "
            f"defaulting to '{DEFAULT_STATUS}'"
        )
        return DEFAULT_STATUS, warnings

    if s in _STATUS_ALIASES:
        mapped = _STATUS_ALIASES[s]
        warnings.append(f"Status alias '{s}' normalised to '{mapped}'")
        return mapped, warnings

    if s in VALID_STATUSES:
        return s, warnings

    warnings.append(
        f"Unknown status '{s}' — using '{DEFAULT_STATUS}'. "
        f"Valid values: {sorted(VALID_STATUSES)}"
    )
    return DEFAULT_STATUS, warnings


def _validate_and_normalize(raw: Dict[str, Any]) -> Tuple[Optional[Dict], List[str]]:
    """
    Validate required fields and build a normalised dict ready for DB insertion.
    Returns (normalised, errors). errors is empty on success.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # Check required fields
    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        return None, [f"Missing required fields: {sorted(missing)}"]

    # Strip strings
    def _s(key: str, fallback: str = "") -> str:
        v = raw.get(key, fallback)
        return str(v).strip() if v is not None else fallback

    spec_name = _s("spec_name")
    if not spec_name:
        errors.append("spec_name must not be empty")

    instrument = _s("instrument")
    if not instrument:
        errors.append("instrument must not be empty")

    entry_rules = _s("entry_rules")
    exit_rules  = _s("exit_rules")
    if not entry_rules:
        errors.append("entry_rules must not be empty")
    if not exit_rules:
        errors.append("exit_rules must not be empty")

    if errors:
        return None, errors

    asset_class, symbol = _classify_instrument(instrument)
    # explicit symbol override
    if raw.get("symbol"):
        symbol = _s("symbol")

    status, status_warns = _normalize_status(raw.get("status"))
    warnings.extend(status_warns)

    # description maps to why_edge_exists; explicit why_edge_exists takes precedence
    description      = _s("description")
    why_edge_exists  = _s("why_edge_exists") or description

    norm: Dict[str, Any] = {
        "spec_name":              spec_name,
        "asset_class":            asset_class,
        "symbol":                 symbol,
        "timeframe":              _s("timeframe"),
        "session":                _s("session") or None,
        "entry_rules":            entry_rules,
        "exit_rules":             exit_rules,
        "risk_rules":             _s("risk_rules") or None,
        "filters":                _s("filters") or None,
        "optimization_variables": _s("optimization_variables") or None,
        "stop_loss_type":         _s("stop_loss_type") or None,
        "stop_loss_value":        raw.get("stop_loss_value"),
        "profit_target_type":     _s("profit_target_type") or None,
        "profit_target_value":    raw.get("profit_target_value"),
        "why_edge_exists":        why_edge_exists or None,
        "why_strategy_may_fail":  _s("why_strategy_may_fail") or None,
        "status":                 status,
        # idea fields
        "strategy_type":  _s("strategy_type"),
        "description":    description,
    }

    return norm, warnings


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _find_existing_spec(conn: sqlite3.Connection, spec_name: str) -> Optional[int]:
    row = conn.execute(
        "SELECT spec_id FROM strategy_specs WHERE spec_name = ? LIMIT 1",
        (spec_name,),
    ).fetchone()
    return row[0] if row else None


def _find_or_create_idea(
    conn: sqlite3.Connection,
    norm: Dict[str, Any],
    dry_run: bool,
) -> Optional[int]:
    """Return idea_id for this spec_name; create a minimal idea row if needed."""
    existing = conn.execute(
        "SELECT idea_id FROM strategy_ideas WHERE idea_name = ? LIMIT 1",
        (norm["spec_name"],),
    ).fetchone()

    if existing:
        return existing[0]

    if dry_run:
        return None  # would create

    cur = conn.execute("""
        INSERT INTO strategy_ideas
            (idea_name, asset_class, symbol, timeframe,
             strategy_type, description, source, status)
        VALUES (?, ?, ?, ?, ?, ?, 'spec_import', 'spec_created')
    """, (
        norm["spec_name"],
        norm["asset_class"],
        norm["symbol"],
        norm["timeframe"],
        norm["strategy_type"],
        norm["description"],
    ))
    conn.commit()
    return cur.lastrowid


def _insert_spec(
    conn: sqlite3.Connection,
    idea_id: Optional[int],
    norm: Dict[str, Any],
) -> int:
    cur = conn.execute("""
        INSERT INTO strategy_specs (
            idea_id, spec_name, asset_class, symbol, timeframe, session,
            entry_rules, exit_rules, risk_rules, filters, optimization_variables,
            stop_loss_type, stop_loss_value, profit_target_type, profit_target_value,
            why_edge_exists, why_strategy_may_fail, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        idea_id,
        norm["spec_name"],
        norm["asset_class"],
        norm["symbol"],
        norm["timeframe"],
        norm["session"],
        norm["entry_rules"],
        norm["exit_rules"],
        norm["risk_rules"],
        norm["filters"],
        norm["optimization_variables"],
        norm["stop_loss_type"],
        norm["stop_loss_value"],
        norm["profit_target_type"],
        norm["profit_target_value"],
        norm["why_edge_exists"],
        norm["why_strategy_may_fail"],
        norm["status"],
    ))
    conn.commit()
    return cur.lastrowid


def _update_spec(
    conn: sqlite3.Connection,
    spec_id: int,
    idea_id: Optional[int],
    norm: Dict[str, Any],
) -> None:
    conn.execute("""
        UPDATE strategy_specs SET
            idea_id               = COALESCE(?, idea_id),
            asset_class           = ?,
            symbol                = ?,
            timeframe             = ?,
            session               = COALESCE(?, session),
            entry_rules           = ?,
            exit_rules            = ?,
            risk_rules            = COALESCE(?, risk_rules),
            filters               = COALESCE(?, filters),
            optimization_variables = COALESCE(?, optimization_variables),
            stop_loss_type        = COALESCE(?, stop_loss_type),
            stop_loss_value       = COALESCE(?, stop_loss_value),
            profit_target_type    = COALESCE(?, profit_target_type),
            profit_target_value   = COALESCE(?, profit_target_value),
            why_edge_exists       = COALESCE(?, why_edge_exists),
            why_strategy_may_fail = COALESCE(?, why_strategy_may_fail),
            status                = ?,
            updated_at            = datetime('now')
        WHERE spec_id = ?
    """, (
        idea_id,
        norm["asset_class"],
        norm["symbol"],
        norm["timeframe"],
        norm["session"],
        norm["entry_rules"],
        norm["exit_rules"],
        norm["risk_rules"],
        norm["filters"],
        norm["optimization_variables"],
        norm["stop_loss_type"],
        norm["stop_loss_value"],
        norm["profit_target_type"],
        norm["profit_target_value"],
        norm["why_edge_exists"],
        norm["why_strategy_may_fail"],
        norm["status"],
        spec_id,
    ))
    conn.commit()


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------

def import_spec(
    conn: sqlite3.Connection,
    path: Path,
    dry_run: bool = False,
    update_existing: bool = False,
) -> Dict[str, Any]:
    """
    Import one strategy spec file into strategy_specs.

    Returns a result dict with keys:
        action      : "inserted" | "updated" | "skipped" | "dry_run" | "error"
        spec_id     : int or None
        spec_name   : str
        warnings    : list[str]
        errors      : list[str]
    """
    result: Dict[str, Any] = {
        "action": "error", "spec_id": None, "spec_name": None,
        "warnings": [], "errors": [],
    }

    if not path.exists():
        result["errors"].append(f"File not found: {path}")
        return result

    raw, load_errors = _load_file(path)
    if load_errors:
        result["errors"].extend(load_errors)
        return result

    norm, val_messages = _validate_and_normalize(raw)
    if norm is None:
        result["errors"].extend(val_messages)
        return result

    result["spec_name"] = norm["spec_name"]
    result["warnings"].extend(val_messages)

    existing_spec_id = _find_existing_spec(conn, norm["spec_name"])

    if dry_run:
        result["action"] = "dry_run"
        result["spec_id"] = existing_spec_id
        if existing_spec_id:
            result["warnings"].append(
                f"Spec '{norm['spec_name']}' already exists (spec_id={existing_spec_id}) — "
                f"would {'update' if update_existing else 'skip'}"
            )
        else:
            result["warnings"].append(f"Would insert new spec: '{norm['spec_name']}'")
        return result

    idea_id = _find_or_create_idea(conn, norm, dry_run=False)

    if existing_spec_id is not None:
        if update_existing:
            _update_spec(conn, existing_spec_id, idea_id, norm)
            result["action"]  = "updated"
            result["spec_id"] = existing_spec_id
        else:
            result["action"]  = "skipped"
            result["spec_id"] = existing_spec_id
            result["warnings"].append(
                f"Spec '{norm['spec_name']}' already exists (spec_id={existing_spec_id}). "
                "Use --update-existing to overwrite."
            )
        return result

    new_id = _insert_spec(conn, idea_id, norm)
    result["action"]  = "inserted"
    result["spec_id"] = new_id
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_result(result: Dict, norm_summary: Optional[Dict] = None) -> None:
    action = result["action"].upper()
    name   = result["spec_name"] or "unknown"
    sid    = result["spec_id"]

    print(f"  [{action}]  {name}  (spec_id={sid})")
    for w in result["warnings"]:
        print(f"  WARN  : {w}")
    for e in result["errors"]:
        print(f"  ERROR : {e}")

    if norm_summary and action in ("DRY_RUN", "INSERTED", "UPDATED"):
        print(f"  asset_class = {norm_summary.get('asset_class')}  "
              f"symbol = {norm_summary.get('symbol')}  "
              f"status = {norm_summary.get('status')}")
        print(f"  strategy_type = {norm_summary.get('strategy_type')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a strategy spec file into hermes_research.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--file",
        required=True,
        metavar="PATH",
        help="Path to spec file (.json, .yaml, .yml, .md)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and normalise without writing to the database",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update the existing row if spec_name already exists (default: skip)",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        metavar="PATH",
        help="Path to hermes_research.db",
    )
    args = parser.parse_args()

    path   = Path(args.file)
    db_path = Path(args.db)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    mode = "DRY RUN (no writes)" if args.dry_run else "LIVE"
    print("Hermes Spec Importer")
    print(f"  DB     : {db_path}")
    print(f"  File   : {path}")
    print(f"  Mode   : {mode}")
    print(f"  YAML   : {'available' if YAML_AVAILABLE else 'not installed'}")
    print()

    # Pre-load for summary display on dry-run
    raw, _ = _load_file(path) if path.exists() else ({}, [])
    norm, _ = _validate_and_normalize(raw) if raw else (None, [])

    conn = sqlite3.connect(str(db_path))
    try:
        result = import_spec(conn, path, dry_run=args.dry_run, update_existing=args.update_existing)
        _print_result(result, norm_summary=norm)
        print()

        total = conn.execute("SELECT COUNT(*) FROM strategy_specs").fetchone()[0]
        print(f"strategy_specs total rows: {total}")

        status = "DRY RUN" if args.dry_run else (
            "OK" if result["action"] in ("inserted", "updated") else
            ("SKIPPED" if result["action"] == "skipped" else "FAILED")
        )
        print(f"Status: {status}")

    finally:
        conn.close()

    sys.exit(0 if result["action"] not in ("error",) else 1)


if __name__ == "__main__":
    main()
