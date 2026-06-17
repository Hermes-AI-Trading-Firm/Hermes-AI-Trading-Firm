# research/outcomes -- Research Outcome Tracker

**Phase 35**

**Question:** When we ran the experiment, what did we learn?

The Three-Question Test:

    Can it produce better evidence?   YES -- accumulates a record of what was tried and found
    Can it ask a better question?     YES -- patterns in outcomes sharpen which questions to ask
    Does a human still decide?        YES -- outcomes are human-recorded; tracker observes only

## Approved scope

    MAY record human-observed outcomes
    MAY summarize patterns
    MAY suggest which research questions are historically useful
    MAY NOT decide whether a strategy is approved, rejected, or advanced

## What it does

Each time a research action is completed -- a Monte Carlo run, an OOS import,
an audit resolution -- the tracker records:

- What question was open
- What action was taken
- What the finding was
- Whether the strategy advanced as a result
- Any new blockers the finding revealed

Over time, this builds institutional memory: which question types, when answered,
most reliably advance strategies toward REVIEW_REQUIRED.

## What it does NOT do

- Does not decide whether a strategy is approved, rejected, or advanced
- Does not change strategy state
- Does not write to the database
- Does not advance any strategy past `REVIEW_REQUIRED`

## Storage

Outcome logs live in `research/outcomes/` and are **committed to git**.
They are institutional memory, not disposable reports.

```
research/outcomes/<spec_name>_outcomes.json
```

Unlike `reports/`, outcome files persist across sessions and accumulate
understanding over the life of the pipeline.

## Functions

| Function | Purpose |
|----------|---------|
| `record_outcome()` | Write one human-observed outcome to the strategy log |
| `load_outcomes(spec_name)` | Read outcome history for one strategy |
| `load_all_outcomes()` | Read all outcomes across all strategies |
| `identify_patterns(outcomes)` | Which question types most often advance strategies? |
| `summarize_outcomes(outcomes)` | Portfolio-level advancement rate summary |
| `suggest_high_value_questions(conn, spec_id, patterns)` | Overlay historical rates onto current open questions |

## Usage

```bash
# Record a completed research action (human-triggered)
python -m research.outcomes.outcome_tracker --record \
    --spec-id 1 \
    --question-id mc_missing \
    --action "Ran Monte Carlo with 10000 trials" \
    --finding "MC survival 91.2%, passes 85% gate" \
    --advanced yes \
    --lifecycle-before VALIDATED_WF \
    --lifecycle-after VALIDATED_WF

# Show outcome history for one strategy
python -m research.outcomes.outcome_tracker --spec-id 1

# Show all outcomes + portfolio summary
python -m research.outcomes.outcome_tracker --all

# Show pattern analysis
python -m research.outcomes.outcome_tracker --patterns

# Suggest high-value open questions (overlays historical patterns on current unknowns)
python -m research.outcomes.outcome_tracker --suggest --spec-id 1

# Dry-run: console only, no files written
python -m research.outcomes.outcome_tracker --all --dry-run
```

## Outcome record format

```json
{
  "outcome_id":       "a1b2c3d4",
  "spec_id":          1,
  "spec_name":        "BTC_REGIME_BREAKOUT_v001",
  "question_id":      "mc_missing",
  "category":         "MONTE_CARLO",
  "action_taken":     "Ran Monte Carlo with 10000 trials",
  "finding":          "MC survival 91.2%, passes 85% gate",
  "evidence_added":   "mc_pass=True, mc_score=0.912",
  "lifecycle_before": "VALIDATED_WF",
  "lifecycle_after":  "VALIDATED_WF",
  "advanced":         true,
  "new_blockers":     [],
  "recorded_at":      "2026-06-16T17:45:00",
  "recorded_by":      "human"
}
```

## Advancement rate

The tracker computes per-question-type advancement rates from all recorded
outcomes. This powers `suggest_high_value_questions()`, which overlays
historical effectiveness onto current priority rankings.

| Rate | Interpretation |
|------|---------------|
| >= 75% | Historically effective -- high-yield research action |
| 50-74% | Moderate history -- outcome depends on strategy specifics |
| < 50%  | Low historical yield -- consider whether the blocker is elsewhere |
| No data | Effectiveness unknown -- neutral weight applied |

These rates inform suggestions only. They do not decide anything.

## Pipeline position

```
Evidence Layer
  + Knowledge Layer
    + Decision Support Layer
      + Knowledge Graph (Phase 32)
        + Research Question Engine (Phase 33)
          + Research Priority Engine (Phase 34)
            -> [Research Outcome Tracker] (Phase 35)
               -- what did we learn when we acted?
               -> REVIEW_REQUIRED
                 -> Human Decision
```

## Rules

1. Record human-observed outcomes only
2. Summarize patterns; do not prescribe actions
3. Suggest; do not decide
4. Do not change strategy state
5. Do not write to the DB
6. Do not advance any strategy past REVIEW_REQUIRED
7. Human authority unchanged

Accumulate evidence. Sharpen the question. Human decides.
