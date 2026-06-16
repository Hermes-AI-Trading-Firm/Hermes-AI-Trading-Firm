# research/lifecycle -- Strategy Lifecycle Management

**Phase 31**

**Question:** Where is each strategy in its research journey?

The Three-Question Test:

    Can it produce better evidence?   YES -- full lifecycle state view across all strategies
    Can it ask a better question?     YES -- shows exactly what is missing and what comes next
    Does a human still decide?        YES -- REVIEW_REQUIRED is the maximum automated state

## Lifecycle States

States are sequential. Each represents a milestone in the research journey.

| State | Description |
|-------|-------------|
| `IDEA` | Concept exists; no spec in database |
| `SPEC_IMPORTED` | Strategy spec exists in `strategy_specs` table |
| `BACKTEST_IMPORTED` | IS backtest with trade list imported |
| `SCORED` | Scoring results exist |
| `AUDITED` | Audit report found in `reports/audits/` |
| `VALIDATED_MC` | Monte Carlo score in DB or report in `reports/validation/` |
| `VALIDATED_WF` | Walk-forward score in DB or report in `reports/validation/` |
| `REGIME_ANALYZED` | Regime report found in `reports/regime/` |
| `DECISION_PACKAGED` | Decision package exists (any readiness status) |
| `REVIEW_REQUIRED` | Decision package readiness == `READY_FOR_HUMAN_REVIEW` |
| `HUMAN_APPROVED` | Approval file found in `research/approved/` |
| `HUMAN_REJECTED` | Rejection file found in `research/rejected/` |
| `ARCHIVED` | Archive marker found in `research/archived/` |

**Maximum automated state: `REVIEW_REQUIRED`**

## Rules

1. Automated pipeline may advance state only up to `REVIEW_REQUIRED`
2. `HUMAN_APPROVED` and `HUMAN_REJECTED` require file evidence only a human command creates
3. No lifecycle state triggers execution of any kind
4. Lifecycle output is advisory and state-tracking only
5. This module does not modify strategy logic or broker/execution systems

## Human state detection

Human states are inferred from file presence — no database flags:

| State | Detected by |
|-------|-------------|
| `HUMAN_APPROVED` | Any file in `research/approved/` prefixed with strategy safe-name |
| `HUMAN_REJECTED` | Any file in `research/rejected/` prefixed with strategy safe-name |
| `ARCHIVED` | Any file in `research/archived/` prefixed with strategy safe-name |

State inference is read-only. No files are created or modified to determine state.

## Evidence bar

The evidence bar shows which milestones have been reached for each strategy:

```
[S][B][Sc][Au][MC][WF][Re][Dp][RR]
```

| Symbol | Milestone |
|--------|-----------|
| `[S]` | Spec imported |
| `[B]` | Backtest with trade list |
| `[Sc]` | Scored |
| `[Au]` | Audited |
| `[MC]` | Monte Carlo validated |
| `[WF]` | Walk-forward validated |
| `[Re]` | Regime analyzed |
| `[Dp]` | Decision package generated |
| `[RR]` | Package ready for human review |

Dots (`[..]`) indicate a milestone not yet reached.

## What it does NOT do

- Does not write to any database table
- Does not approve or reject strategies
- Does not move any strategy past `REVIEW_REQUIRED`
- Does not modify strategy logic
- Does not connect to brokers or execution systems

## Usage

```bash
# Show lifecycle for all strategies
python -m research.lifecycle.lifecycle --all

# Show lifecycle for one strategy
python -m research.lifecycle.lifecycle --spec-id 6

# Dry-run (console output only, no files written)
python -m research.lifecycle.lifecycle --all --dry-run
```

## Pipeline position

```
Evidence Layer
  (Backtests, Audit, Walk-Forward, Monte Carlo, Regime, Compliance)
    -> Knowledge Layer
       (Pattern Library, Archetype Classifier, Learning Brain)
         -> Decision Support Layer
            (Comparison Engine, Portfolio Constructor, [Lifecycle], Decision Package)
              -> REVIEW_REQUIRED
                -> Human Decision
```

The Lifecycle module reads from all layers and answers: where is each strategy right now, and what comes next?

## Output

Reports are written to `reports/lifecycle/` (gitignored):

```
reports/lifecycle/lifecycle_summary_20260615.md
reports/lifecycle/lifecycle_summary_20260615.json
```
