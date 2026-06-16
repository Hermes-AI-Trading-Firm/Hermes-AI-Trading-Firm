# research/archetype -- Strategy Archetype Classifier

Automatically classifies strategies into one of 10 archetypes using
keyword matching against spec metadata. Classifications are stored in
`research/archetype/classifications.json` (committed to git).

## Archetypes

| Archetype | Description |
|-----------|-------------|
| ORB | Opening Range Breakout -- trades the break of the first N-minute range |
| VWAP Pullback | VWAP-anchored mean-reversion pullback entries |
| Mean Reversion | Statistical reversion to equilibrium -- fades extended moves |
| Trend Following | Directional momentum -- rides sustained trends |
| FVG Continuation | Fair Value Gap -- enters on return to unfilled imbalance |
| Liquidity Sweep | Stop hunt / liquidity grab reversal |
| Breakout | Level or range breakout entry |
| Options Income | Premium selling / income generation |
| Statistical Arbitrage | Pairs / cointegration / relative value |
| Other | Unclassified or mixed archetype |

## Classification method

Rule-based keyword matching across available text fields:

| Field | Weight |
|-------|--------|
| spec_name | 3 |
| description | 2 |
| entry_rules | 2 |
| exit_rules | 1 |
| notes | 1 |

Each archetype receives a confidence score (0.0–1.0). The archetype
with the highest score above `0.20` becomes the primary. Additional
archetypes above `0.12` become secondaries. If nothing clears `0.20`,
the strategy is classified as `Other`.

A strategy can have one primary and multiple secondaries — for example,
`MNQ_ORB_FVG_v001` classifies as ORB (primary) + FVG Continuation (secondary).

## What it does NOT do

- Does not write to any database table
- Does not modify strategy specs or scores
- Does not approve or reject strategies
- Does not run any validation engine

Classification is idempotent — re-classifying replaces the prior record.

## Persistence model

`research/archetype/classifications.json` is committed to git. It is
research knowledge, not a generated report.

`reports/archetypes/` is gitignored. It holds generated human-readable
reports.

## Usage

```bash
# Classify all scored strategies
python -m research.archetype.archetype_classifier --all

# Classify one strategy
python -m research.archetype.archetype_classifier --spec-id 6

# Dry-run (console only, no files written)
python -m research.archetype.archetype_classifier --all --dry-run
```

## Adding a new archetype

Edit `research/archetype/archetypes.json`:
1. Add a new key with `label`, `description`, `keywords`, and `prior_key`
2. If the archetype has known priors, add them to `research/memory/strategy_type_priors.json`
3. Re-run `--all` to reclassify all strategies

## Pipeline position

```
Spec Import -> [Archetype Classifier] -> Learning Brain -> Pattern Library
```

The archetype classifier runs after a spec is imported and scored.
Its output enriches learning reviews with archetype-specific priors.
