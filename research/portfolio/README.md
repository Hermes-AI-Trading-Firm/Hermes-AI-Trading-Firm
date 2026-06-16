# research/portfolio -- Research Portfolio Constructor

**Phase 30**

**Question:** Among all candidates, where should human attention be focused?

The Three-Question Test:

    Can it produce better evidence?   YES -- cross-portfolio state view
    Can it ask a better question?     YES -- prioritised attention queue
    Does a human still decide?        YES -- REVIEW_REQUIRED is terminal

## What it does

Reads every strategy across all pipeline layers and ranks them by
the value of human attention they deserve right now.

Produces four views:

| View | Question answered |
|------|------------------|
| Attention Queue | Which READY strategies should the human review first? |
| Portfolio Health | How are all strategies distributed across readiness states? |
| Archetype Balance | Which archetypes are well-represented or dangerously thin? |
| Stale Candidates | Which strategies have failed a gate and need archiving? |

## What it does NOT do

- Does not write to any database table
- Does not approve or reject strategies
- Does not move any strategy past `REVIEW_REQUIRED`
- Does not override human judgment

## Priority scoring

Each strategy receives a priority score (0-100 base + modifiers):

**Base score by readiness status:**

| Status | Base |
|--------|------|
| READY_FOR_HUMAN_REVIEW | 80 |
| NEEDS_REGIME_ANALYSIS | 45 |
| NEEDS_MONTE_CARLO | 30 |
| NEEDS_WALK_FORWARD | 20 |
| NEEDS_MORE_TRADES | 10 |
| NEEDS_REAL_NT8_EXPORT | 5 |
| REJECT_RESEARCH_CANDIDATE | 0 |

**Modifiers:**

| Condition | Modifier |
|-----------|----------|
| Comparison verdict: LEADS | +15 |
| Comparison verdict: TRAILS | -8 |
| Comparison verdict: SOLE | +5 |
| Per strength detected | +3 (capped at +12) |
| Per hard blocker | -5 (capped at -20) |
| Composite score >= 80 | +8 |
| Composite score >= 70 | +4 |

## What it reads

| Source | Layer |
|--------|-------|
| Strategy specs + scoring | DB |
| Readiness, blockers, strengths | `reports/decision_packages/` |
| Comparison verdict | `reports/comparison/` |
| Archetype classification | `research/archetype/classifications.json` |

## Usage

```bash
# Full portfolio view
python -m research.portfolio.portfolio_constructor --all

# Top 5 candidates only
python -m research.portfolio.portfolio_constructor --top 5

# Dry-run (console only, no files written)
python -m research.portfolio.portfolio_constructor --all --dry-run
```

## Pipeline position

```
Evidence Layer
  (Backtests, Audit, Walk-Forward, Monte Carlo, Regime, Compliance)
    -> Knowledge Layer
       (Pattern Library, Archetype Classifier, Learning Brain)
         -> Decision Support Layer
            (Comparison Engine -> [Portfolio Constructor] -> Decision Package)
              -> REVIEW_REQUIRED
                -> Human Decision
```

The Portfolio Constructor draws from all upstream layers and surfaces
the highest-value candidates for human attention. The Decision Package
is what the human reads. The Portfolio Constructor is what determines
which packages deserve the human's time first.

## Output

Reports are written to `reports/portfolio/` (gitignored):

```
reports/portfolio/portfolio_20260615.md
reports/portfolio/portfolio_20260615.json
reports/portfolio/portfolio_top5_20260615.md
```
