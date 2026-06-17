# research/review -- Learning Review Workspace

**Phase 39**

**Question:** Given everything we know from all prior strategies, what does that tell us about the one in front of us now?

The Three-Question Test:

    Can it produce better evidence?   YES -- synthesizes accumulated learning into one place
    Can it ask a better question?     YES -- surfaces patterns a reviewer might miss
    Does a human still decide?        YES -- workspace informs; never concludes

## What it does

Assembles all thirteen decision-support sections into one workspace document
at the REVIEW_REQUIRED gate. Every layer of the pipeline contributes.
The final status is always REVIEW_REQUIRED. The pipeline cannot change it.

## Thirteen sections

| # | Section | Source |
|---|---------|--------|
| 1 | Strategy Summary | strategy_specs, scoring_results |
| 2 | Evidence Quality | Phase 37 / Phase 38 trace_engine |
| 3 | Audit Summary | Phase 25 strategy_auditor |
| 4 | Walk-Forward Summary | scoring_results, backtests (OOS) |
| 5 | Monte Carlo Summary | scoring_results |
| 6 | Regime Summary | regime_analysis |
| 7 | Learning Review Summary | pattern_library, classifications, outcome_tracker |
| 8 | Open Research Questions | Phase 33 question_engine |
| 9 | Research Priorities | Phase 34 priority_engine |
| 10 | Traceability Links | Phase 38 trace_engine |
| 11 | Outcome History | Phase 35 outcome_tracker |
| 12 | Human Review Notes | Human-provided (--notes parameter) |
| 13 | Final Status | REVIEW_REQUIRED (hardcoded; pipeline cannot change it) |

## Section 7: Learning Review Summary

The key new layer. Synthesizes:

- **Archetype classification**: what archetype this strategy belongs to and its known weaknesses
- **Pattern library**: what the pipeline knows about this strategy's historical performance
- **Outcome patterns**: for each open question this strategy has, what is the historical advancement rate when that question type is answered?
- **Archetype peers**: how many other strategies share this archetype and what have they taught us?

This is the gap the prior pipeline could not close: accumulated learning from all prior
strategies, synthesized at the point of decision.

## What it does NOT do

- Does not approve or reject strategies
- Does not change strategy state
- Does not write to the database
- Does not advance any strategy past REVIEW_REQUIRED

## Usage

```bash
# Generate full workspace (writes to reports/review/)
python -m research.review.review_workspace --spec-id 1

# Full workspace with human notes
python -m research.review.review_workspace --spec-id 1 --notes "Strong edge, OOS gap is the key concern"

# Print to console only (no files written)
python -m research.review.review_workspace --spec-id 1 --dry-run

# Print one section to console
python -m research.review.review_workspace --spec-id 1 --section learning
python -m research.review.review_workspace --spec-id 1 --section questions
python -m research.review.review_workspace --spec-id 1 --section evidence

# Available section names:
#   summary / evidence / audit / wf / mc / regime /
#   learning / questions / priorities / trace / outcomes / notes / status
```

## Output

Reports are written to `reports/review/` (gitignored):

```
reports/review/<strategy>_review_workspace_<date>.md
reports/review/<strategy>_review_workspace_<date>.json
```

Workspace reports are ephemeral. Generated on demand at each review session.
The evidence they draw from is committed. The workspace itself is not.

## Final Status

Every workspace ends with:

    REVIEW_REQUIRED. The pipeline stops here. Human authority begins.

This is not a formality. It is the design.

## Pipeline position

```
Evidence (raw)
  -> Knowledge (patterns, archetypes)
    -> Questions (Phase 33)
      -> Priorities (Phase 34)
        -> Outcomes (Phase 35)
          -> Certification (Phase 36)
            -> Evidence Quality (Phase 37)
              -> Traceability (Phase 38)
                -> [Learning Review Workspace] (Phase 39)
                   -- all thirteen layers in one place
                   -> REVIEW_REQUIRED
                     -> Human Decision
```

## Rules

1. Read-only across all layers
2. Assemble and present; do not conclude
3. Section 12 is for the human; leave it editable
4. Section 13 is always REVIEW_REQUIRED; do not change it
5. Do not write to the database
6. Do not change strategy state
7. Do not advance any strategy past REVIEW_REQUIRED
8. Human authority unchanged

Accumulate evidence. Sharpen the question. Human decides.
