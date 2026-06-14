"""Report templates and rendering functions.

All Markdown output is produced here.  report_generator.py builds the
data dict; templates.py turns it into human-readable text.
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Classification map  recommendation → canonical label
# ---------------------------------------------------------------------------

CLASSIFICATION_MAP: Dict[str, str] = {
    "Live Candidate":  "LIVE_CANDIDATE",
    "Forward Test":    "FORWARD_TEST_CANDIDATE",
    "Optimize":        "OPTIMIZATION_CANDIDATE",
    "Retest":          "NEEDS_RETEST",
    "Reject":          "REJECTED",
}

# ---------------------------------------------------------------------------
# Grade decorators
# ---------------------------------------------------------------------------

GRADE_LABEL: Dict[str, str] = {
    "A+": "A+  ★★★★★",
    "A":  "A   ★★★★☆",
    "B":  "B   ★★★☆☆",
    "C":  "C   ★★☆☆☆",
    "D":  "D   ★☆☆☆☆",
    "Reject": "Reject  ✗",
}


def _pct(v: Any, decimals: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{float(v) * 100:.{decimals}f}%"


def _num(v: Any, decimals: int = 2) -> str:
    if v is None:
        return "n/a"
    return f"{float(v):.{decimals}f}"


def _bool(v: Any) -> str:
    if v is None:
        return "n/a"
    return "Pass" if v else "Fail"


# ---------------------------------------------------------------------------
# Strategy report renderer
# ---------------------------------------------------------------------------


def render_strategy_md(r: Dict[str, Any]) -> str:
    sc   = r.get("scoring") or {}
    bt   = r.get("backtest") or {}
    comp = r.get("component_scores") or {}
    warn = r.get("overfit_warnings") or []
    gate = r.get("gate_failures") or []

    grade_line = GRADE_LABEL.get(sc.get("grade", ""), sc.get("grade", "n/a"))
    classification = r.get("classification", "UNCLASSIFIED")

    lines: List[str] = [
        f"# Strategy Report — {r.get('spec_name', 'Unknown')}",
        "",
        f"**Generated:** {r.get('generated_at', '')}  ",
        f"**Spec ID:** {r.get('spec_id', '')}  ",
        f"**Asset Class:** {r.get('asset_class', 'n/a')}  ",
        f"**Symbol:** {r.get('symbol', 'n/a')}  ",
        f"**Timeframe:** {r.get('timeframe', 'n/a')}  ",
        f"**Status:** {r.get('status', 'n/a')}  ",
        "",
        "---",
        "",
        "## Composite Score",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Score | **{_num(sc.get('composite_score'))}** / 100 |",
        f"| Grade | **{grade_line}** |",
        f"| Recommendation | **{sc.get('recommendation', 'n/a')}** |",
        f"| Classification | `{classification}` |",
        f"| Scored At | {sc.get('scored_at', 'n/a')} |",
        "",
        "---",
        "",
        "## Component Scores",
        "",
        "| Component | Score | Weight |",
        "|-----------|-------|--------|",
        f"| Profitability | {_num(comp.get('profitability'))} | 30% |",
        f"| Drawdown | {_num(comp.get('drawdown'))} | 20% |",
        f"| Consistency | {_num(comp.get('consistency'))} | 15% |",
        f"| Regime | {_num(comp.get('regime'))} | 10% |",
        f"| Monte Carlo | {_num(comp.get('monte_carlo'))} | 10% |",
        f"| Walk-Forward | {_num(comp.get('walk_forward'))} | 5% |",
        f"| Robustness | {_num(comp.get('robustness'))} | 5% |",
        f"| Prop Firm | {_num(comp.get('prop_firm'))} | 3% |",
        f"| Explainability | {_num(comp.get('explainability'))} | 2% |",
        f"| Overfitting Risk | {_num(comp.get('overfitting_risk'))} | penalty |",
        "",
        "---",
        "",
        "## Validation Gates",
        "",
        f"| Gate | Result |",
        f"|------|--------|",
        f"| Walk-Forward | {_bool(sc.get('walk_forward_pass'))} |",
        f"| Monte Carlo | {_bool(sc.get('monte_carlo_pass'))} |",
        f"| Prop Firm | {_bool(sc.get('prop_firm_supported'))} |",
    ]

    if gate:
        lines += ["", "**Hard Gate Failures:**", ""]
        for g in gate:
            lines.append(f"- ❌ {g}")
    else:
        lines += ["", "**Hard Gates:** All passed ✓"]

    lines += [
        "",
        "---",
        "",
        "## Backtest Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Profit Factor | {_num(bt.get('profit_factor'))} |",
        f"| Sharpe Ratio | {_num(bt.get('sharpe_ratio'))} |",
        f"| Max Drawdown | {_num(bt.get('max_drawdown_pct'))} |",
        f"| Win Rate | {_pct(bt.get('win_rate'))} |",
        f"| Total Trades | {bt.get('total_trades') or 'n/a'} |",
    ]

    if warn:
        lines += [
            "",
            "---",
            "",
            "## Overfit Warnings",
            "",
        ]
        for w in warn:
            lines.append(f"- ⚠️  {w}")

    lines += [
        "",
        "---",
        "",
        "## Next Steps",
        "",
    ]

    rec = sc.get("recommendation", "")
    if rec == "Forward Test":
        lines += [
            "- [ ] Human approval required before forward testing",
            "- [ ] Submit to Forward Testing Journal",
            "- [ ] Monitor live performance vs backtest baseline",
        ]
    elif rec == "Optimize":
        lines += [
            "- [ ] Submit to Optimization Lab",
            "- [ ] Re-run walk-forward after optimization",
            "- [ ] Re-score after optimization completes",
        ]
    elif rec == "Retest":
        lines += [
            "- [ ] Extend backtest window with more data",
            "- [ ] Review entry/exit rule clarity",
            "- [ ] Re-submit to Backtesting Lab",
        ]
    elif rec == "Reject":
        lines += [
            "- [ ] Archive under `research/rejected/` with documented reason",
            "- [ ] Log pattern to AI Learning Brain",
        ]
    else:
        lines += ["- [ ] Pending human review"]

    lines += ["", "---", "", "*Hermes AI Trading Firm — Read-only research pipeline*", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Firm summary renderer
# ---------------------------------------------------------------------------


def render_firm_summary_md(reports: List[Dict[str, Any]], generated_at: str = "") -> str:
    total   = len(reports)
    by_grade: Dict[str, int] = {}
    by_rec:   Dict[str, int] = {}
    for r in reports:
        sc = r.get("scoring") or {}
        by_grade[sc.get("grade", "?")] = by_grade.get(sc.get("grade", "?"), 0) + 1
        by_rec[sc.get("recommendation", "?")] = by_rec.get(sc.get("recommendation", "?"), 0) + 1

    lines: List[str] = [
        "# Hermes AI Trading Firm — Research Summary",
        "",
        f"**Generated:** {generated_at}  ",
        f"**Strategies scored:** {total}  ",
        "",
        "---",
        "",
        "## Rankings",
        "",
        "| Rank | Strategy | Score | Grade | Recommendation |",
        "|------|----------|-------|-------|----------------|",
    ]

    sorted_reports = sorted(reports, key=lambda r: (r.get("scoring") or {}).get("composite_score") or 0, reverse=True)
    for rank, r in enumerate(sorted_reports, 1):
        sc = r.get("scoring") or {}
        lines.append(
            f"| {rank} | {r.get('spec_name', '?')} "
            f"| {_num(sc.get('composite_score'))} "
            f"| {sc.get('grade', '?')} "
            f"| {sc.get('recommendation', '?')} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Grade Distribution",
        "",
        "| Grade | Count |",
        "|-------|-------|",
    ]
    for grade in ["A+", "A", "B", "C", "D", "Reject"]:
        n = by_grade.get(grade, 0)
        if n:
            lines.append(f"| {grade} | {n} |")

    lines += [
        "",
        "---",
        "",
        "## Action Queue",
        "",
        "| Action | Count |",
        "|--------|-------|",
    ]
    for rec in ["Live Candidate", "Forward Test", "Optimize", "Retest", "Reject"]:
        n = by_rec.get(rec, 0)
        if n:
            lines.append(f"| {rec} | {n} |")

    lines += [
        "",
        "---",
        "",
        "## Strategy Detail",
        "",
    ]
    for r in sorted_reports:
        sc  = r.get("scoring") or {}
        bt  = r.get("backtest") or {}
        lines += [
            f"### {r.get('spec_name', '?')}",
            "",
            f"- **Score:** {_num(sc.get('composite_score'))} | **Grade:** {sc.get('grade', 'n/a')} | **Rec:** {sc.get('recommendation', 'n/a')}",
            f"- **PF:** {_num(bt.get('profit_factor'))} | **Sharpe:** {_num(bt.get('sharpe_ratio'))} | **DD:** {_num(bt.get('max_drawdown_pct'))} | **Win Rate:** {_pct(bt.get('win_rate'))}",
            f"- **WF:** {_bool(sc.get('walk_forward_pass'))} | **MC:** {_bool(sc.get('monte_carlo_pass'))} | **Prop Firm:** {_bool(sc.get('prop_firm_supported'))}",
            "",
        ]
        warn = r.get("overfit_warnings") or []
        if warn:
            for w in warn:
                lines.append(f"  - ⚠️  {w}")
            lines.append("")

    lines += ["---", "", "*Hermes AI Trading Firm — Read-only research pipeline*", ""]
    return "\n".join(lines)
