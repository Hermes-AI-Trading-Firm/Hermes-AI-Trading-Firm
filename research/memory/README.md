# research/memory -- Research Memory & Pattern Library

Persistent cross-strategy knowledge base. Accumulates failure patterns,
strength patterns, and blocker statistics from all completed learning reviews.
Builds a firm-wide picture of what is working, what is failing, and where
the pipeline is most often blocked.

## What it does

1. **Ingests** completed learning review JSONs from `reports/learning/`
2. **Accumulates** failure and strength patterns across all strategies
3. **Indexes** by pattern ID, category, strength type, action type, and readiness status
4. **Derives** cross-strategy insights: firm-wide blockers, most-needed actions,
   patterns that recur across multiple strategies
5. **Writes** a cross-strategy pattern report to `reports/memory/`

## What it does NOT do

- Does not write to any database table
- Does not modify strategy specs, scores, or learning reviews
- Does not approve or reject strategies
- Does not change readiness status
- Does not run any validation engine

## Persistence model

`research/memory/pattern_library.json` is committed to git. It is research
knowledge, not a generated report. It accumulates over time as new strategies
are researched and reviewed.

Ingestion is idempotent: re-ingesting a spec_id replaces its prior record and
rebuilds all indexes from scratch.

`reports/patterns/` is gitignored. It holds generated human-readable reports.

## Usage

```bash
# Ingest all learning reviews + write report
python -m research.memory.pattern_library --all

# Ingest one strategy + write report
python -m research.memory.pattern_library --spec-id 6

# Write report from existing library (no ingestion)
python -m research.memory.pattern_library --report

# Dry-run (console only, no files written, library not saved)
python -m research.memory.pattern_library --all --dry-run
```

## Output files

```
research/memory/
  pattern_library.json          -- persistent knowledge base (committed to git)

reports/patterns/
  pattern_report_{YYYYMMDD}.md
  pattern_report_{YYYYMMDD}.json
```

## What gets tracked

| Index | Key | Stored |
|-------|-----|--------|
| failure_index | pattern_id | severity, category, spec_ids, count |
| strength_index | classified key | spec_ids, count |
| action_index | action_type | spec_ids, count |
| readiness_index | readiness_status | spec_ids |
| category_index | category | HIGH/MEDIUM/LOW/total counts |

## Pipeline position

```
... -> AI Learning Brain (per-strategy) -> [Research Memory] -> Human Reviewer
```

The pattern library reads all upstream learning review outputs and writes only
to `research/memory/pattern_library.json` and `reports/memory/`. It has no
upstream effects.
