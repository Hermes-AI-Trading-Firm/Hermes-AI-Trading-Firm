# research/traceability -- Belief Provenance Engine

**Phase 38**

**Question:** Why do we believe what we believe?

The Three-Question Test:

    Can it produce better evidence?   YES -- shows which evidence is load-bearing
    Can it ask a better question?     YES -- exposes gaps in the belief chain
    Does a human still decide?        YES -- traces only; never recommends approval

## What it does

For any strategy, the engine traces every current pipeline belief back to the
evidence that produced it -- a vertical read through all eight pipeline layers.

| Layer | What it traces |
|-------|---------------|
| Evidence chain | Every backtest, scoring run, regime analysis, and outcome in chronological order |
| Evidence quality | How each cell (BT/OOS/WF/MC/Regime) was derived and why the overall grade is what it is |
| Research priority | Why open questions have the priority level they do |
| Question trace | What generated a specific question, what was done about it, and whether it is resolved |
| Decision package | What evidence is present, what is missing, and what is blocking human review |

## What it does NOT do

- Does not change strategy state
- Does not write to the database
- Does not approve or reject strategies
- Does not advance any strategy past REVIEW_REQUIRED

## Functions

| Function | Purpose |
|----------|---------|
| `build_evidence_chain(conn, spec_id)` | Chronological chain of all evidence events |
| `trace_evidence_quality(conn, spec_id)` | How each quality cell was derived |
| `trace_priority(conn, spec_id)` | Why research questions have the priority they do |
| `trace_question(conn, spec_id, question_id)` | What generated a question and what was done |
| `trace_decision_package(conn, spec_id)` | What is present, missing, and blocking review |
| `generate_trace_report(conn, spec_id)` | Assemble all traces into Markdown + JSON report |

## Usage

```bash
# Show all traces for a strategy
python -m research.traceability.trace_engine --spec-id 1

# Show only the evidence chain
python -m research.traceability.trace_engine --spec-id 1 --chain

# Trace how the evidence quality grade was derived
python -m research.traceability.trace_engine --spec-id 1 --quality

# Trace research priority
python -m research.traceability.trace_engine --spec-id 1 --priority

# Trace a specific research question
python -m research.traceability.trace_engine --spec-id 1 --question mc_missing

# Trace decision package readiness
python -m research.traceability.trace_engine --spec-id 1 --decision-package

# Generate full Markdown + JSON report (written to reports/traceability/)
python -m research.traceability.trace_engine --spec-id 1 --report

# Dry-run: print to console, no files written
python -m research.traceability.trace_engine --spec-id 1 --report --dry-run
```

## Output

Reports are written to `reports/traceability/` (gitignored):

```
reports/traceability/trace_<spec_name>_<date>.md
reports/traceability/trace_<spec_name>_<date>.json
```

Trace reports are ephemeral. They are generated on demand and not committed to git.
The evidence they describe is committed -- the traces are not.

## Pipeline position

```
Evidence Quality (Phase 37)        <- what is the overall evidence grade?
  + Decision Packages              <- what does a human need to decide?
    + Lifecycle                    <- where is each strategy in the pipeline?
      + Outcomes (Phase 35)        <- what did we learn when we acted?
        + Priorities (Phase 34)    <- what should we learn next?
          + Questions (Phase 33)   <- what don't we know?
            + Knowledge            <- what patterns have we accumulated?
              + Evidence           <- what did we measure?

[Belief Provenance Engine] (Phase 38)
  -- a vertical read through all eight layers for a single strategy
  -- answers: why do we believe what we believe?
  -> REVIEW_REQUIRED
    -> Human Decision
```

## Rules

1. Read-only across all layers
2. Summarize and expose; do not prescribe
3. Trace the belief chain; do not validate it
4. Do not change strategy state
5. Do not write to the database
6. Do not advance any strategy past REVIEW_REQUIRED
7. Human authority unchanged

Accumulate evidence. Sharpen the question. Human decides.
