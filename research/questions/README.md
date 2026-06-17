# research/questions -- Research Question Engine

**Phase 33**

**Question:** Given everything in the knowledge graph, what is the single most important unanswered question?

The Three-Question Test:

    Can it produce better evidence?   YES -- identifies what remains unknown
    Can it ask a better question?     YES -- this IS the question-sharpening layer
    Does a human still decide?        YES -- the engine asks; it never answers

## What it does

Reads all accumulated research evidence and produces the questions
a human reviewer most needs to answer. Not recommendations. Not verdicts. Questions.

Two scopes:

| Scope | Question |
|-------|---------|
| Per strategy | What is the single unresolved question that most blocks this strategy's advancement? |
| Portfolio (global) | What systemic questions exist across the full research portfolio? |

## Question categories

| Category | What it surfaces |
|----------|-----------------|
| `DATA_QUALITY` | Trade list gaps, audit failures, data integrity |
| `SAMPLE_SIZE` | Trade count sufficiency and statistical confidence |
| `OOS_WALK_FORWARD` | Out-of-sample validation gaps and degradation |
| `MONTE_CARLO` | Robustness under randomness and sequence risk |
| `REGIME_DEPENDENCY` | Market condition concentration and coverage |
| `PROP_FIRM_RISK` | Drawdown compliance against funded account limits |
| `ARCHETYPE_WEAKNESS` | Known failure modes for this strategy archetype |
| `PARAMETER_SENSITIVITY` | Overfitting and parameter stability concerns |
| `EXECUTION_ASSUMPTIONS` | Slippage, fill, and cost assumption transparency |
| `RESEARCH_PRIORITY` | Lifecycle bottlenecks and stalled strategies |

## Question format

Each question contains:

```
question                  -- the precise question to answer
why_it_matters            -- what answering this unlocks
evidence_behind_it        -- what we currently know
missing_evidence          -- what we do not know
suggested_action          -- the most direct path to an answer
priority                  -- HIGH / MEDIUM / LOW
affects_review_required   -- True if this is a gate-level unknown
```

## Priority model

| Priority | Meaning |
|----------|---------|
| HIGH | Answering this is required before REVIEW_REQUIRED can be reached |
| MEDIUM | Important for confidence but not a hard gate |
| LOW | Research improvement -- useful but not blocking |

Questions marked `affects_review_required: true` are gate-level unknowns.
They must be resolved before the strategy can reach `REVIEW_REQUIRED`.

## What it does NOT do

- Does not answer its own questions
- Does not recommend approve or reject
- Does not change strategy state
- Does not modify scores or reports
- Does not write to the database
- Does not advance any strategy past `REVIEW_REQUIRED`

## Rules

1. The engine asks questions only
2. It does not answer approval questions
3. It does not change strategy state
4. It does not modify scores or reports
5. It does not write to the DB
6. It does not promote beyond `REVIEW_REQUIRED`

## Usage

```bash
# Questions for one strategy
python -m research.questions.question_engine --spec-id 3

# Questions for all strategies + global
python -m research.questions.question_engine --all

# Portfolio-level questions only
python -m research.questions.question_engine --global

# Dry-run (console only, no files written)
python -m research.questions.question_engine --all --dry-run

# Show top 10 instead of top 5
python -m research.questions.question_engine --spec-id 3 --top 10
```

## Output

Reports are written to `reports/questions/` (gitignored):

```
reports/questions/MNQ_ORB_FVG_v001_research_questions_20260616.md
reports/questions/MNQ_ORB_FVG_v001_research_questions_20260616.json
reports/questions/global_research_questions_20260616.md
reports/questions/global_research_questions_20260616.json
```

## Pipeline position

```
Evidence Layer
  + Knowledge Layer
    + Decision Support Layer
      + Knowledge Graph (Phase 32)
        -> [Research Question Engine] (Phase 33)
           -- the final sharpening layer
           -> REVIEW_REQUIRED
             -> Human Decision
```

The Research Question Engine is the last automated layer.
It does not produce verdicts. It produces the sharpest possible
questions so the human's judgment is applied where it matters most.

Accumulate evidence. Sharpen the question. Human decides.
