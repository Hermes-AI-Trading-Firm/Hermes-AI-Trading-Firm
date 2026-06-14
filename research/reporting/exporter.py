"""Report exporter — writes Markdown and JSON to reports/.

Public API
----------
export_strategy_report(report)              -> (md_path, json_path)
export_firm_summary(reports, generated_at)  -> (md_path, json_path)
export_all(reports)                         -> Dict with all paths
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .templates import render_strategy_md, render_firm_summary_md

# ---------------------------------------------------------------------------
# Output directories
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REPORTS_DIR  = _PROJECT_ROOT / "reports"
_STRAT_DIR    = _REPORTS_DIR  / "strategies"
_SUMMARY_DIR  = _REPORTS_DIR  / "summaries"


def _ensure_dirs() -> None:
    _STRAT_DIR.mkdir(parents=True, exist_ok=True)
    _SUMMARY_DIR.mkdir(parents=True, exist_ok=True)


def _datestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Strategy report export
# ---------------------------------------------------------------------------


def export_strategy_report(report: Dict[str, Any]) -> Tuple[Path, Path]:
    """Write one strategy report as .md and .json.

    Returns (md_path, json_path).
    """
    _ensure_dirs()
    name = report.get("spec_name", f"spec_{report.get('spec_id', 'unknown')}")
    stem = f"{name}_{_datestamp()}"

    md_path   = _STRAT_DIR / f"{stem}.md"
    json_path = _STRAT_DIR / f"{stem}.json"

    md_path.write_text(render_strategy_md(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )

    return md_path, json_path


# ---------------------------------------------------------------------------
# Firm summary export
# ---------------------------------------------------------------------------


def export_firm_summary(
    reports: List[Dict[str, Any]],
    generated_at: str = "",
) -> Tuple[Path, Path]:
    """Write firm-level summary report as .md and .json.

    Returns (md_path, json_path).
    """
    _ensure_dirs()
    if not generated_at:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    stem = f"firm_summary_{_datestamp()}"

    md_path   = _SUMMARY_DIR / f"{stem}.md"
    json_path = _SUMMARY_DIR / f"{stem}.json"

    md_path.write_text(
        render_firm_summary_md(reports, generated_at), encoding="utf-8"
    )
    json_path.write_text(
        json.dumps(
            {"generated_at": generated_at, "count": len(reports), "reports": reports},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return md_path, json_path


# ---------------------------------------------------------------------------
# Batch export
# ---------------------------------------------------------------------------


def export_all(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Export every strategy report plus the firm summary.

    Returns a dict mapping spec_name → {md, json} paths plus summary paths.
    """
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result: Dict[str, Any] = {"strategies": {}, "summary": {}}

    for report in reports:
        md_path, json_path = export_strategy_report(report)
        result["strategies"][report.get("spec_name", str(report.get("spec_id")))] = {
            "md":   str(md_path),
            "json": str(json_path),
        }

    sum_md, sum_json = export_firm_summary(reports, generated_at)
    result["summary"] = {"md": str(sum_md), "json": str(sum_json)}

    return result
