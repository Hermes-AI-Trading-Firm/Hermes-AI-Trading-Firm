# research/priorities -- Research Priority Engine

**Phase 34**

**Goal:** Rank research work, not strategies.

The Three-Question Test:

    Can it produce better evidence?   YES -- tells you where to look next
    Can it ask a better question?     YES -- surfaces the highest-value gap across all strategies
    Does a human still decide?        YES -- produces a ranked agenda; humans act on it

## What it does

Aggregates every unanswered question from Phase 33 (Research Question Engine)
across all strategies and groups them by type. The output is a ranked research
agenda: which evidence gaps, if resolved first, produce the largest advance
across the entire portfolio.

Each item answers: **If we spend research time here, how many strategies benefit,
and how much does it matter?**

## Key distinction

| Phase 33 asks | Phase 34 answers |
|---------------|-----------------|
| What is the single most important unanswered question *for this strategy*? | What is the highest-value research *action* across all strategies combined? |

Phase 33 gives per-strategy focus.
Phase 34 gives portfolio-level prioritization of research work.

## Priority levels

| Level | Meaning |
|-------|---------|
| CRITICAL | High-urgency gap, affects REVIEW_REQUIRED, cross-strategy |
| HIGH | Gate-level gap for one strategy, or non-gate gap for many |
| MEDIUM | Important for confidence, not a hard gate; multi-strategy |
| LOW | Research improvement; useful but not blocking |

## Research value score (0-125)

The composite score that drives ranking:

```
research_value = priority_score + gate_bonus + strategy_bonus - effort_penalty

  priority_score  = 60 (HIGH) / 30 (MEDIUM) / 10 (LOW)
  gate_bonus      = 40 if the gap blocks REVIEW_REQUIRED, else 0
  strategy_bonus  = min(25, (n_strategies - 1) * 8)
  effort_penalty  = 0 (LOW) / 7 (MEDIUM) / 15 (HIGH)
```

High-effort work is discounted, not ignored. A CRITICAL gap requiring HIGH effort
still outranks a LOW gap requiring LOW effort.

## Priority dimensions

| Dimension | Effect |
|-----------|--------|
| Evidence missing | What type of evidence is absent |
| Decision blocked | Does resolving this unlock REVIEW_REQUIRED? |
| Multiple strategies affected | Cross-portfolio impact multiplies value |
| Archetype-wide impact | Grouped by archetype for shared weaknesses |
| Research effort required | LOW effort = higher ranking (quick wins rise) |

## What it does NOT do

- Does not answer questions
- Does not approve or reject strategies
- Does not change strategy state
- Does not write to the database
- Does not advance any strategy past `REVIEW_REQUIRED`

## Phase compliance

```
[1] No DB writes
[2] No strategy state changes
[3] No approval automation
[4] No path beyond REVIEW_REQUIRED
[5] Outputs are advisory only
[6] Human authority unchanged
```

## Usage

```bash
# Rank all open research questions (top 10 by default)
python -m research.priorities.priority_engine --all

# Dry-run: console output only, no files written
python -m research.priorities.priority_engine --all --dry-run

# Show top 5 priorities
python -m research.priorities.priority_engine --all --top 5
```

## Output

Reports are written to `reports/priorities/` (gitignored):

```
reports/priorities/research_priorities_20260616.md
reports/priorities/research_priorities_20260616.json
```

Each priority item contains:

```
title                   -- the research action
level                   -- CRITICAL / HIGH / MEDIUM / LOW
research_value          -- composite score (0-125)
affected_strategies     -- which strategies benefit
archetype_impact        -- which archetypes are affected
evidence_gap_pct        -- fraction of portfolio missing this
affects_review_required -- is this a gate-level gap?
effort                  -- LOW / MEDIUM / HIGH
suggested_action        -- most direct path to closing the gap
sample_question         -- the sharpest question this action answers
evidence_summary        -- what we currently know
```

## Pipeline position

```
Evidence Layer
  + Knowledge Layer
    + Decision Support Layer
      + Knowledge Graph (Phase 32)
        + Research Question Engine (Phase 33)
          -> [Research Priority Engine] (Phase 34)
             -- agenda for WHERE to direct research effort
             -> REVIEW_REQUIRED
               -> Human Decision
```

## Rules

1. Rank research work, not strategies
2. Do not answer questions -- surface them ranked by value
3. Do not change strategy state
4. Do not write to the DB
5. Do not advance any strategy past REVIEW_REQUIRED
6. Human authority unchanged

Accumulate evidence. Sharpen the question. Human decides.
