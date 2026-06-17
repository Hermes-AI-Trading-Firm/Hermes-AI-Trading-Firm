# research/timeline -- Event Timeline

**Phase 42**

**What happened to Hermes.**

Reads across all six pipeline layers and merges them into a single
chronological event stream. Every backtest, every scoring event, every
human review, every governance change -- in order.

## Six layers

| Layer | What it reads |
|-------|--------------|
| `EVIDENCE` | Backtests, scoring, regime analysis, optimizations |
| `KNOWLEDGE` | Strategy ideas, specs created, approvals, rejections |
| `QUESTION` | Questions generated (current state; no persistent history yet) |
| `PRIORITY` | Priority rankings (current state; no persistent history yet) |
| `REVIEW` | Human decisions from `research/review_journal/` |
| `GOVERNANCE` | Pipeline changes from `research/governance/governance_ledger.json` |

## What it does NOT do

- Does not write to the database
- Does not change strategy state
- Does not make decisions
- Does not advance any strategy past REVIEW_REQUIRED
- Does not generate questions or priorities

## Usage

```bash
# Full timeline -- all strategies, all layers
python -m research.timeline.event_timeline

# Filter to one strategy
python -m research.timeline.event_timeline --spec-id 1
python -m research.timeline.event_timeline --spec-name BTC_REGIME_BREAKOUT_v001

# Filter by layer
python -m research.timeline.event_timeline --layer EVIDENCE
python -m research.timeline.event_timeline --layer REVIEW
python -m research.timeline.event_timeline --layer GOVERNANCE

# Filter by date
python -m research.timeline.event_timeline --since 2026-06-01

# Combine filters
python -m research.timeline.event_timeline --spec-id 1 --layer EVIDENCE --since 2026-01-01

# Write report to reports/timeline/
python -m research.timeline.event_timeline --report
python -m research.timeline.event_timeline --spec-id 1 --report

# Dry-run: build timeline, do not write report files
python -m research.timeline.event_timeline --report --dry-run
```

## Event types

### Evidence Layer
| Event type | Source |
|------------|--------|
| `BACKTEST_RUN` | `backtests` table (IS and OOS) |
| `STRATEGY_SCORED` | `scoring_results` table |
| `REGIME_ANALYZED` | `regime_analysis` table |
| `OPTIMIZATION_RUN` | `optimizations` table |

### Knowledge Layer
| Event type | Source |
|------------|--------|
| `IDEA_CREATED` | `strategy_ideas` table |
| `SPEC_CREATED` | `strategy_specs` table |
| `STRATEGY_APPROVED` | `approved_strategies` table |
| `STRATEGY_REJECTED` | `rejected_strategies` table |

### Review Layer
| Event type | Source |
|------------|--------|
| `HUMAN_REVIEW` | `research/review_journal/*_review_journal.json` |

### Governance Layer
| Event type | Source |
|------------|--------|
| `PHASE_ADDED` | `research/governance/governance_ledger.json` |
| `RULE_ADDED` | `research/governance/governance_ledger.json` |
| `RULE_CHANGED` | `research/governance/governance_ledger.json` |
| `PIPELINE_CHANGED` | `research/governance/governance_ledger.json` |
| `SCORING_CHANGED` | `research/governance/governance_ledger.json` |
| `VALIDATION_CHANGED` | `research/governance/governance_ledger.json` |
| `GOVERNANCE_DECISION` | `research/governance/governance_ledger.json` |
| `HUMAN_PROCESS_CHANGE` | `research/governance/governance_ledger.json` |

## Output format

```
  2026-01-15
    KNOWLEDGE    SPEC_CREATED              [BTC_REGIME_BREAKOUT_v001]
                 Spec created: BTC_REGIME_BREAKOUT_v001  CRYPTO BTC 1h
    EVIDENCE     BACKTEST_RUN              [BTC_REGIME_BREAKOUT_v001]
                 IS backtest: PF=2.43  trades=247  Sharpe=1.85
  2026-06-16
    REVIEW       HUMAN_REVIEW              [BTC_REGIME_BREAKOUT_v001]
                 Human review: NEED_MORE_EVIDENCE  confidence=HIGH  reviewer=human
    GOVERNANCE   PHASE_ADDED               [system]
                 PHASE_ADDED  Phase 40: Preserve human judgment as institutional memory.
```

## Storage

Timeline output is written to `reports/timeline/` (gitignored -- ephemeral).

The underlying sources are persistent:
- `research/review_journal/*.json` -- committed to git
- `research/governance/governance_ledger.json` -- committed to git
- Database (`database/hermes.db`) -- not committed

## Pipeline position

```
Evidence
  -> Knowledge
    -> Questions
      -> Priorities
        -> Traceability (Phase 38)
          -> Review Workspace (Phase 39)
            -> REVIEW_REQUIRED
              -> Human Decision
                -> Review Journal (Phase 40)

Governance Ledger (Phase 41)  -- records changes to the pipeline

Event Timeline (Phase 42)     -- reads all of the above
                              -- merges into one chronological stream
                              -- answers: what happened to Hermes?
```

## Rules

1. Read-only -- no writes to database or pipeline state
2. Advisory only -- the timeline illuminates, does not decide
3. Human authority unchanged -- REVIEW_REQUIRED is still the terminal gate
4. Authority impact: NONE
5. REVIEW_REQUIRED impact: UNCHANGED

More evidence. Better questions. Same human authority.
