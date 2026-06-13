"""
Prop firm rule definitions and loaders.

Normalizes prop_firm_profiles DB rows into typed PropRule objects
that compliance.py can evaluate without touching the DB directly.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Templates — per-firm defaults for fields not present in the DB row
# ---------------------------------------------------------------------------

FIRM_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "apex": {
        "trailing_drawdown_limit": 0.08,
        "daily_loss_limit":        0.02,
        "profit_target":           0.10,
        "min_trading_days":        0,
        "max_position_size":       None,
        "consistency_rule":        False,
        "notes": (
            "Apex Trader Funding. EOD trailing drawdown from peak equity. "
            "No minimum trading days. No consistency rule."
        ),
    },
    "topstep": {
        "trailing_drawdown_limit": 0.06,
        "daily_loss_limit":        0.02,
        "profit_target":           0.10,
        "min_trading_days":        0,
        "max_position_size":       None,
        "consistency_rule":        False,
        "notes": (
            "TopStep. Intraday trailing drawdown from highest equity. "
            "5 winning days required before first withdrawal."
        ),
    },
    "ftmo": {
        "trailing_drawdown_limit": 0.10,
        "daily_loss_limit":        0.05,
        "profit_target":           0.10,
        "min_trading_days":        4,
        "max_position_size":       None,
        "consistency_rule":        True,
        "notes": (
            "FTMO. Max overall drawdown (not trailing). "
            "30-day evaluation window. Consistency rule enforced."
        ),
    },
    "custom": {
        "trailing_drawdown_limit": 0.05,
        "daily_loss_limit":        0.02,
        "profit_target":           0.08,
        "min_trading_days":        0,
        "max_position_size":       None,
        "consistency_rule":        False,
        "notes": "User-defined rules.",
    },
}


# ---------------------------------------------------------------------------
# PropRule dataclass
# ---------------------------------------------------------------------------

@dataclass
class PropRule:
    """Normalized prop firm rule set derived from a prop_firm_profiles row."""

    profile_id:              Optional[int]
    firm_name:               str
    account_label:           str
    account_size:            float          # starting equity ($)
    trailing_drawdown_limit: float          # fraction, e.g. 0.08 = 8 %
    daily_loss_limit:        float          # fraction, e.g. 0.02 = 2 %
    profit_target:           float          # fraction, e.g. 0.10 = 10 %
    min_trading_days:        int   = 0
    max_position_size:       Optional[int] = None
    consistency_rule:        bool  = False  # best day must be ≤ 30 % of total profit
    notes:                   str   = ""

    # Dollar thresholds — derived from account_size × fractions
    dd_limit_dollars:         float = field(init=False)
    daily_loss_limit_dollars: float = field(init=False)
    profit_target_dollars:    float = field(init=False)

    def __post_init__(self) -> None:
        self.dd_limit_dollars          = round(self.account_size * self.trailing_drawdown_limit, 2)
        self.daily_loss_limit_dollars  = round(self.account_size * self.daily_loss_limit, 2)
        self.profit_target_dollars     = round(self.account_size * self.profit_target, 2)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "profile_id":                self.profile_id,
            "firm_name":                 self.firm_name,
            "account_label":             self.account_label,
            "account_size":              self.account_size,
            "trailing_drawdown_limit":   self.trailing_drawdown_limit,
            "daily_loss_limit":          self.daily_loss_limit,
            "profit_target":             self.profit_target,
            "min_trading_days":          self.min_trading_days,
            "max_position_size":         self.max_position_size,
            "consistency_rule":          self.consistency_rule,
            "notes":                     self.notes,
            "dd_limit_dollars":          self.dd_limit_dollars,
            "daily_loss_limit_dollars":  self.daily_loss_limit_dollars,
            "profit_target_dollars":     self.profit_target_dollars,
        }


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _closest_template(firm_name: str) -> Dict[str, Any]:
    """Return the template dict whose key best matches firm_name."""
    key = (firm_name or "custom").strip().lower().split()[0]
    return dict(FIRM_TEMPLATES.get(key, FIRM_TEMPLATES["custom"]))


def from_db_record(row: Dict[str, Any]) -> PropRule:
    """Build a PropRule from a prop_firm_profiles row dict."""
    tpl = _closest_template(row.get("firm_name") or "custom")
    return PropRule(
        profile_id=              row.get("profile_id"),
        firm_name=               row.get("firm_name")               or tpl.get("firm_name", "Custom"),
        account_label=           row.get("account_label")           or "Eval",
        account_size=            float(row.get("account_size")      or 50_000.0),
        trailing_drawdown_limit= float(row.get("trailing_drawdown_limit") or tpl["trailing_drawdown_limit"]),
        daily_loss_limit=        float(row.get("daily_loss_limit")  or tpl["daily_loss_limit"]),
        profit_target=           float(row.get("profit_target")     or tpl["profit_target"]),
        min_trading_days=        int(row.get("min_trading_days")    or tpl["min_trading_days"]),
        max_position_size=       row.get("max_position_size")       or tpl["max_position_size"],
        consistency_rule=        bool(row.get("consistency_rule", tpl["consistency_rule"])),
        notes=                   row.get("notes")                   or tpl["notes"],
    )


def from_template(
    name:         str,
    account_size: float = 50_000.0,
    **overrides:  Any,
) -> PropRule:
    """Build a PropRule from a named template: apex / topstep / ftmo / custom."""
    tpl = _closest_template(name)
    tpl.update(overrides)
    return PropRule(
        profile_id=              None,
        firm_name=               overrides.get("firm_name",     name.capitalize()),
        account_label=           overrides.get("account_label", name.upper()),
        account_size=            account_size,
        trailing_drawdown_limit= tpl["trailing_drawdown_limit"],
        daily_loss_limit=        tpl["daily_loss_limit"],
        profit_target=           tpl["profit_target"],
        min_trading_days=        int(tpl.get("min_trading_days", 0)),
        max_position_size=       tpl.get("max_position_size"),
        consistency_rule=        bool(tpl.get("consistency_rule", False)),
        notes=                   tpl.get("notes", ""),
    )


def load_all_from_db(conn: sqlite3.Connection) -> List[PropRule]:
    """Return all active prop_firm_profiles rows as PropRule objects."""
    try:
        cur = conn.execute("""
            SELECT profile_id, firm_name, account_label, account_size,
                   trailing_drawdown_limit, daily_loss_limit, profit_target,
                   min_trading_days, max_position_size, consistency_rule, notes
            FROM prop_firm_profiles
            WHERE is_active = 1
            ORDER BY profile_id
        """)
        cols = [d[0] for d in cur.description]
        rows = [{cols[i]: r[i] for i in range(len(cols))} for r in cur.fetchall()]
        return [from_db_record(r) for r in rows]
    except Exception:
        return []
