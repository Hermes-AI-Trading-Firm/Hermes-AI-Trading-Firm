# research/review_journal -- Human Decision Journal

**Phase 40**

**The eighth layer. The only one that decides.**

Records what the human reviewer saw, decided, and would do next.
Committed to git as institutional memory. Every future reviewer
can learn from every prior one.

## What it does

Unlike every prior phase, this module does not read from the pipeline
and produce output for the human. It receives from the human and
preserves it.

| Field | What it captures |
|-------|-----------------|
| reviewer | Who reviewed this strategy |
| date | When the review took place |
| decision | What the reviewer decided |
| reasoning | Why they decided it |
| key_evidence | What evidence mattered most to the decision |
| concerns | What concerns remained unresolved |
| unanswered_questions | What the reviewer would still investigate |
| next_research_actions | Concrete actions that should follow |
| confidence_level | How confident the reviewer was in the decision |

## Decision values

| Decision | Meaning |
|----------|---------|
| `APPROVE_FOR_NEXT_RESEARCH_STAGE` | Evidence sufficient; advance to next research stage |
| `REJECT` | Strategy fails review; archive with documented reason |
| `NEED_MORE_EVIDENCE` | Return to research pipeline with specific gaps named |
| `DEFER` | Not ready to decide; revisit after named condition is met |

## What it does NOT do

- Does not make decisions
- Does not approve or reject strategies automatically
- Does not change strategy state in the database
- Does not advance any strategy past REVIEW_REQUIRED

## Storage

Journal entries live in `research/review_journal/` and are **committed to git**.
They are institutional memory. Not disposable reports.

```
research/review_journal/<spec_name>_review_journal.json
```

Unlike `reports/`, journal files persist across sessions and accumulate
the reasoning history of every human decision made in the pipeline.

## Usage

```bash
# Record a review decision
python -m research.review_journal.review_journal --record \
    --spec-id 1 \
    --spec-name BTC_REGIME_BREAKOUT_v001 \
    --reviewer "your name" \
    --decision NEED_MORE_EVIDENCE \
    --reasoning "Strong IS edge but OOS gap is disqualifying at this stage." \
    --key-evidence "PF=2.43" "Sharpe=1.85" "247 trades" \
    --concerns "No OOS validation" "Single regime window" \
    --unanswered "Does edge hold OOS?" "Regime dependency confirmed?" \
    --next-actions "Run OOS backtest" "Complete regime analysis" \
    --confidence HIGH

# Show journal for one strategy
python -m research.review_journal.review_journal --spec-name BTC_REGIME_BREAKOUT_v001

# Show all journal entries across all strategies
python -m research.review_journal.review_journal --all

# Show decision pattern summary
python -m research.review_journal.review_journal --summary

# Dry-run: print to console, no files written
python -m research.review_journal.review_journal --record ... --dry-run
```

## Journal entry format

```json
{
  "journal_id":            "a1b2c3d4",
  "spec_id":               1,
  "spec_name":             "BTC_REGIME_BREAKOUT_v001",
  "reviewer":              "human",
  "date":                  "2026-06-17",
  "decision":              "NEED_MORE_EVIDENCE",
  "reasoning":             "Strong IS edge but OOS gap is disqualifying at this stage.",
  "key_evidence":          ["PF=2.43", "Sharpe=1.85", "247 trades"],
  "concerns":              ["No OOS validation", "Single regime window"],
  "unanswered_questions":  ["Does edge hold OOS?", "Regime dependency confirmed?"],
  "next_research_actions": ["Run OOS backtest", "Complete regime analysis"],
  "confidence_level":      "HIGH",
  "recorded_at":           "2026-06-17T03:00:00+00:00",
  "recorded_by":           "human"
}
```

## Relationship to the Review Workspace

The Review Workspace (Phase 39) assembles what the pipeline knows.
The Review Journal records what the human decided.

```
Review Workspace (Phase 39)   -- what the pipeline prepared for the human
  -> REVIEW_REQUIRED
    -> Human reads workspace
      -> Human decides
        -> [Review Journal] (Phase 40)
           -- what the human saw, concluded, and left for the next reviewer
```

## Pipeline position

```
Evidence
  -> Validation
    -> Questions (Phase 33)
      -> Priorities (Phase 34)
        -> Outcomes (Phase 35)
          -> Traceability (Phase 38)
            -> Review Workspace (Phase 39)
              -> REVIEW_REQUIRED
                -> Human Decision
                  -> [Review Journal] (Phase 40)
                     -- the reasoning that led to the decision
                     -- committed to git as institutional memory
```

## Rules

1. Journal entries are human-authored only
2. The pipeline records them; it does not generate them
3. Entries are append-only -- never delete a prior decision
4. The decision field does not change pipeline state automatically
5. Human authority unchanged -- the journal records it, not enforces it

Accumulate evidence. Sharpen the question. Human decides. Record the reasoning.
