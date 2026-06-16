# research/audit -- Strategy Auditor

Read-only pre-approval audit checklist for the human reviewer. Surfaces
data gaps, sample size issues, overfit risk, and out-of-sample readiness
before a strategy is considered for forward testing or approval.

No DB writes. No schema changes. No scoring changes. No promotion.
Human approval gate remains mandatory.

---

## Files

| File | Purpose |
|------|---------|
| `strategy_auditor.py` | Main auditor CLI |

---

## Audit Categories

| # | Category | What it checks |
|---|----------|----------------|
| 1 | Data Completeness | Spec, backtest, trade list, equity curve, scoring result |
| 2 | Sample Size | Trade count thresholds (FAIL <30, WARN <100) |
| 3 | Backtest Quality | Date range, performance summary, trade list, initial capital |
| 4 | Overfit Risk | PF/Sharpe/win-rate suspicion flags vs trade count |
| 5 | Out-of-Sample Readiness | OOS backtest, walk-forward score, Monte Carlo score |
| 6 | Prop-Firm Readiness | Drawdown limit, prop_firm_supported flag |

---

## Status Codes

| Symbol | Code | Meaning |
|--------|------|---------|
| `[+]` | PASS | Check satisfied |
| `[!]` | WARN | Non-fatal issue -- review before advancing |
| `[X]` | FAIL | Blocking issue -- must be resolved first |
| `[i]` | INFO | Informational only |

---

## Recommendations

| Recommendation | Meaning |
|----------------|---------|
| `NEEDS_REAL_NT8_EXPORT` | No trade list data -- run nt8_import_pipeline with a real export |
| `NEEDS_MORE_TRADES` | Trade count below 30 -- extend backtest window |
| `NEEDS_WALK_FORWARD` | No OOS backtest or WF/MC validation missing |
| `READY_FOR_HUMAN_REVIEW` | No FAILs, ready for human decision |
| `REJECT_RESEARCH_CANDIDATE` | Multiple critical FAILs -- further research needed |

---

## Usage

### Audit one strategy
```powershell
python -m research.audit.strategy_auditor --spec-id 1
```

### Audit all strategies
```powershell
python -m research.audit.strategy_auditor --all
```

### Dry-run (no report files written)
```powershell
python -m research.audit.strategy_auditor --spec-id 1 --dry-run
python -m research.audit.strategy_auditor --all --dry-run
```

### Custom database or report directory
```powershell
python -m research.audit.strategy_auditor --spec-id 1 `
    --db database/hermes_research.db `
    --reports-dir reports/audits
```

---

## Output

Reports are written to `reports/audits/` (gitignored):

```
reports/audits/
  MNQ_ORB_FVG_v001_20260615_audit.md
  MNQ_ORB_FVG_v001_20260615_audit.json
```

The JSON report contains the full checklist as a machine-readable array.
The Markdown report is formatted for the human reviewer.

---

## Where This Fits in the Pipeline

```
Backtest Import (nt8_import_pipeline.py)
  -> Score (score_from_backtests.py)
    -> Audit (strategy_auditor.py)     <-- here
      -> Human Review
        -> Approval / Rejection
```

The auditor runs AFTER scoring. It reads scoring results and backtest
data to produce the checklist. It does not modify any data.

---

## Placement in the Approval Workflow

Run the auditor to generate the human review package:

```powershell
# 1. Import and score
python connectors/ninjatrader/nt8_import_pipeline.py `
    --summary path/to/summary.csv `
    --trade-list path/to/trades.csv `
    --spec-id 3

# 2. Audit
python -m research.audit.strategy_auditor --spec-id 3

# 3. Human reviews reports/audits/<strategy>_<date>_audit.md
# 4. Human makes approval decision (manual DB action)
```
