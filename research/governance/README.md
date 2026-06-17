# research/governance -- Governance Ledger

**Phase 41**

**The pipeline's record of its own history.**

Records changes to the pipeline itself -- not strategy decisions, but system decisions.
Committed to git as institutional memory. Every future reviewer can see what changed,
when it changed, and why.

## What it records

| Change Type | What it captures |
|-------------|-----------------|
| `PHASE_ADDED` | A new phase entered the pipeline |
| `RULE_ADDED` | A new rule was introduced |
| `RULE_CHANGED` | An existing rule was modified |
| `PIPELINE_CHANGED` | The pipeline structure changed |
| `SCORING_CHANGED` | Scoring logic or thresholds changed |
| `VALIDATION_CHANGED` | Validation logic or thresholds changed |
| `GOVERNANCE_DECISION` | A governance decision was recorded |
| `HUMAN_PROCESS_CHANGE` | How the human interacts with the pipeline changed |

## Why this matters

If scoring changed between Phase 34 and Phase 37, evidence scored before the change
was graded differently. The pipeline did not know that. The next reviewer did not know that.

The governance ledger closes that gap. It gives every future reviewer the ability to ask:
"What was the pipeline doing when this evidence was produced?"

## What it does NOT do

- Does not make decisions
- Does not modify the pipeline
- Does not change strategy state
- Does not advance any strategy past REVIEW_REQUIRED

## Storage

Ledger entries live in `research/governance/governance_ledger.json` and are
**committed to git**. They are institutional memory. Not disposable reports.

Unlike `reports/`, ledger entries persist across sessions and accumulate
the change history of the pipeline itself.

## Usage

```bash
# Record a governance change
python -m research.governance.governance_ledger --record \
    --type PHASE_ADDED \
    --phase "Phase 41" \
    --question "What changed about the pipeline and why?" \
    --summary "Record changes to the pipeline itself." \
    --tqt-evidence PASS \
    --tqt-questions PASS \
    --tqt-authority PASS \
    --authority-impact NONE \
    --review-required-impact UNCHANGED \
    --author "human" \
    --commit abc1234

# Show all ledger entries
python -m research.governance.governance_ledger --all

# Filter by change type
python -m research.governance.governance_ledger --filter PHASE_ADDED

# Show summary counts
python -m research.governance.governance_ledger --show-summary

# Dry-run: print to console, no files written
python -m research.governance.governance_ledger --record ... --dry-run
```

## Ledger entry format

```json
{
  "ledger_id":              "00000040",
  "date":                   "2026-06-16",
  "change_type":            "PHASE_ADDED",
  "phase":                  "Phase 40",
  "question":               "What reasoning led the human to the decision?",
  "summary":                "Preserve human judgment as institutional memory.",
  "three_question_test": {
    "better_evidence":      "PASS",
    "better_questions":     "PASS",
    "human_decides":        "PASS"
  },
  "authority_impact":       "NONE",
  "review_required_impact": "UNCHANGED",
  "affected_phases":        [],
  "author":                 "human",
  "commit":                 "fb54a2e",
  "recorded_at":            "2026-06-16T00:00:00+00:00"
}
```

## Relationship to the Human Decision Journal

The Human Decision Journal (Phase 40) records what the human decided about strategies.
The Governance Ledger records what changed about the system that produces the evidence.

```
Human Decision Journal (Phase 40)  -- what the human decided about a strategy
Governance Ledger     (Phase 41)   -- what changed about the pipeline itself
```

Both are committed to git. Both are institutional memory. Neither is a disposable report.

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
                  -> Human Decision Journal (Phase 40)
                     -- the reasoning that led to the decision

Governance Ledger (Phase 41)
  -- runs alongside the full pipeline
  -- records changes to the pipeline itself
  -- not strategy-specific; system-wide
```

## Rules

1. Ledger entries are human-authored only
2. The pipeline records them; it does not generate them
3. Entries are append-only -- never delete a prior entry
4. The ledger does not change pipeline state
5. Human authority unchanged -- the ledger records the system, not the human

More evidence. Better questions. Same human authority.
