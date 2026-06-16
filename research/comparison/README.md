# research/comparison -- Cross-Strategy Comparison Engine

Answers one question per strategy:

> **"Is this strategy better than our existing alternatives?"**

Not: "Is this strategy good?" -- that is the wrong question.

## What it does

- Loads composite scores, walk-forward scores, Monte Carlo survival, win rate,
  drawdown, and trade count for all scored strategies
- Reads archetype classifications from `research/archetype/classifications.json`
- Ranks each strategy within its **archetype peer group** (primary comparison)
  and across the **full firm** (secondary context)
- Assigns a per-strategy verdict: `LEADS`, `COMPETITIVE`, `TRAILS`, `SOLE`,
  or `INSUFFICIENT_DATA`
- Writes human-readable Markdown + machine-readable JSON reports to
  `reports/comparison/` (gitignored)

## What it does NOT do

- Does not write to any database table
- Does not modify strategy specs, scores, or classifications
- Does not approve or reject strategies
- Does not move any strategy past `REVIEW_REQUIRED`

All output is advisory. Humans decide.

## Verdicts

| Tag | Verdict | Meaning |
|-----|---------|---------|
| `[+]` | LEADS | Top third of archetype peers across key metrics |
| `[=]` | COMPETITIVE | Mid-range -- not clearly better or worse |
| `[-]` | TRAILS | Bottom third -- existing alternatives are stronger |
| `[S]` | SOLE | Only representative of this archetype; no peers yet |
| `[?]` | INSUFFICIENT_DATA | Too few scored metrics to compare |

Verdict is based on average archetype rank across 6 metrics:
composite score, walk-forward score, MC survival, win rate,
max drawdown (lower is better), and trade count.

## Metrics compared

| Metric | Direction |
|--------|-----------|
| Composite Score | Higher is better |
| Walk-Forward Score | Higher is better |
| MC Survival | Higher is better |
| Win Rate | Higher is better |
| Max Drawdown | Lower is better |
| Trade Count | Higher is better |

## Usage

```bash
# Compare all scored strategies
python -m research.comparison.comparison_engine --all

# Compare only strategies of one archetype
python -m research.comparison.comparison_engine --archetype vwap_pullback

# Compare one strategy against its peers
python -m research.comparison.comparison_engine --spec-id 6

# Dry-run (console output only -- no files written)
python -m research.comparison.comparison_engine --all --dry-run
```

## Archetype-first ranking

The comparison engine groups strategies by archetype before ranking:

- A VWAP Pullback is ranked against other VWAP Pullbacks
- An ORB is ranked against other ORBs
- Firm-wide rank is secondary context, not the primary verdict

If a strategy is the only member of its archetype (`SOLE`), it is
compared against the firm-wide distribution instead.

## Pipeline position

```
Spec Import
   -> Scoring Pipeline
   -> Archetype Classifier    (research/archetype/)
   -> Learning Brain          (research/learning/)
   -> Pattern Library         (research/memory/)
   -> [Comparison Engine]     (research/comparison/)
   -> Decision Package
   -> REVIEW_REQUIRED
   -> Human Review
```

The comparison engine reads from all upstream layers but writes nothing
back to them.

## Output files

Reports are written to `reports/comparison/` (gitignored):

```
reports/comparison/comparison_20260613.md
reports/comparison/comparison_20260613.json
reports/comparison/comparison_vwap_pullback_20260613.md  # archetype-filtered
```

## Persistence model

No persistent store. Rankings are derived fresh from current DB state
on each run. As new strategies are added, all ranks update automatically.
