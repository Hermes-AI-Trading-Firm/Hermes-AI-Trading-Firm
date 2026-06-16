# research/decision -- Unified Decision Package

Consolidates all upstream research evidence into a single human-review
document per strategy.

```
Spec -> Backtest -> Score -> Audit -> Walk-Forward -> Monte Carlo
  -> Regime Analysis -> Decision Package -> REVIEW_REQUIRED -> Human Review
```

No DB writes. No schema changes. No live trading.
REVIEW_REQUIRED is the terminal automated state.

---

## What It Produces

One document per strategy in the decision queue (all specs with scoring results):

```
reports/decision_packages/
  MNQ_ORB_FVG_v001_decision_package_20260615.md
  MNQ_ORB_FVG_v001_decision_package_20260615.json
```

Each package has 11 sections:

| Section | Content |
|---------|---------|
| Executive Summary | Readiness status, evidence count, required action |
| Strategy Identity | Name, symbol, timeframe, status |
| Backtest Summary | P&L, PF, win rate, drawdown, date range |
| Score / Rank | Composite score, grade, recommendation |
| Audit Findings | Full check table (PASS/WARN/FAIL per check) |
| Walk-Forward | IS vs OOS comparison table + score |
| Monte Carlo | Survival rate, probability positive, drawdown distribution |
| Regime Analysis | Window performance table |
| Compliance | Prop-firm parameters and support status |
| Strengths | Positive evidence points |
| Blockers / Warnings | Issues by severity |

---

## Readiness Statuses

| Status | Meaning |
|--------|---------|
| READY_FOR_HUMAN_REVIEW | All hard checks cleared. Human may approve or reject. |
| NEEDS_REAL_NT8_EXPORT | No real backtest or trade_list_json. |
| NEEDS_MORE_TRADES | Trade count below minimum (30). |
| NEEDS_WALK_FORWARD | No OOS import or walk-forward score. |
| NEEDS_MONTE_CARLO | No Monte Carlo score. |
| NEEDS_REGIME_ANALYSIS | [Warning only — not a hard blocker] |
| REJECT_RESEARCH_CANDIDATE | Failed a critical validation gate. |

---

## Blocker Rules

| Issue | Severity |
|-------|----------|
| FAIL audit finding | BLOCKER |
| Missing real backtest / trade_list_json | BLOCKER |
| Trade count < 30 | BLOCKER |
| walk_forward_score is null | BLOCKER |
| monte_carlo_score is null | BLOCKER |
| walk_forward_score < 0.50 (FAIL tier) | BLOCKER |
| monte_carlo_score < 0.70 (FAIL tier) | BLOCKER |
| Trade count < 100 | WARNING |
| Regime analysis not run | WARNING |
| Prop-firm not supported | WARNING |

---

## Usage

```powershell
# One strategy
python -m research.decision.decision_package --spec-id N

# All scored strategies
python -m research.decision.decision_package --all

# Dry-run (console only, no files)
python -m research.decision.decision_package --spec-id N --dry-run
```

---

## Data Sources

| Data | Source |
|------|--------|
| Spec, backtest, scoring | `hermes_research.db` |
| Audit checks | `reports/audits/{name}_*_audit.json` |
| Walk-forward detail | `reports/validation/{name}_walk_forward_*.json` |
| Monte Carlo detail | `reports/validation/{name}_monte_carlo_*.json` |
| Regime windows | `reports/regime/{name}_regime_analysis_*.json` |

If a report file is missing, the DB score is used where available.
The package always notes what is missing.

---

## What It Does NOT Do

- Does not write to any database table
- Does not change strategy status
- Does not approve or reject any strategy
- Does not connect to any broker or live data source
- Does not move any strategy past REVIEW_REQUIRED

Human approval is required for all promotion decisions.
