# research/knowledge_graph -- Research Knowledge Graph

**Phase 32**

**Question:** What relationships exist across everything we have learned?

The Three-Question Test:

    Can it produce better evidence?   YES -- maps all knowledge connections
    Can it ask a better question?     YES -- surfaces cross-strategy relationships
    Does a human still decide?        YES -- read-only advisory, REVIEW_REQUIRED is terminal

## What it does

Builds an in-memory graph from all available research evidence and maps
the relationships between strategies, archetypes, validation results,
patterns, and lifecycle states.

Answers:
- Which strategies share the same failure patterns?
- What evidence has been accumulated for each archetype?
- Which strategies are ready for human review?
- What does the complete evidence neighborhood of a strategy look like?

## Graph model

### Node types

| Type | Description |
|------|-------------|
| `Strategy` | A strategy spec in the research pipeline |
| `Archetype` | Strategy archetype (ORB, VWAP Pullback, etc.) |
| `Backtest` | In-sample backtest result |
| `Score` | Composite scoring result |
| `AuditResult` | Audit report |
| `MonteCarloResult` | Monte Carlo validation result |
| `WalkForwardResult` | Walk-forward validation result |
| `RegimeFinding` | Regime analysis result |
| `Pattern` | Failure or strength pattern from pattern library |
| `LearningReview` | AI learning brain review |
| `DecisionPackage` | Decision package (readiness assessment) |
| `LifecycleState` | Lifecycle state node (shared across strategies) |

### Edge relationships

| Relationship | From | To |
|-------------|------|-----|
| `HAS_ARCHETYPE` | Strategy | Archetype |
| `HAS_BACKTEST` | Strategy | Backtest |
| `HAS_SCORE` | Strategy | Score |
| `HAS_AUDIT_FINDING` | Strategy | AuditResult |
| `HAS_MONTE_CARLO_RESULT` | Strategy | MonteCarloResult |
| `HAS_WALK_FORWARD_RESULT` | Strategy | WalkForwardResult |
| `HAS_REGIME_FINDING` | Strategy | RegimeFinding |
| `MATCHES_PATTERN` | Strategy | Pattern |
| `HAS_LEARNING_REVIEW` | Strategy | LearningReview |
| `HAS_DECISION_PACKAGE` | Strategy | DecisionPackage |
| `HAS_LIFECYCLE_STATE` | Strategy | LifecycleState |
| `HAS_PRIOR` | Archetype | Pattern |

## Query functions (`graph_queries.py`)

| Function | Returns |
|----------|---------|
| `query_strategy_neighborhood(graph, spec_id)` | All nodes + edges within 1 hop of a strategy |
| `query_shared_failure_patterns(graph)` | Patterns shared across 2+ strategies |
| `query_archetype_evidence(graph, archetype_id)` | All evidence for strategies of an archetype |
| `query_review_ready_candidates(graph)` | Strategies at REVIEW_REQUIRED lifecycle state |

## What it does NOT do

- Does not write to any database table
- Does not modify strategy specs, scores, or classifications
- Does not approve or reject strategies
- Does not advance any strategy past `REVIEW_REQUIRED`
- Does not persist the graph (derived fresh on each run)

## Usage

```bash
# Build full graph and export reports
python -m research.knowledge_graph.graph_builder --build

# Build with dry-run (console only, no files written)
python -m research.knowledge_graph.graph_builder --build --dry-run

# Show neighborhood for one strategy
python -m research.knowledge_graph.graph_builder --spec-id 3

# Show evidence map for an archetype
python -m research.knowledge_graph.graph_builder --archetype orb
```

## Output

Reports are written to `reports/knowledge_graph/` (gitignored):

```
reports/knowledge_graph/knowledge_graph_20260616.md
reports/knowledge_graph/knowledge_graph_20260616.json
```

The JSON export contains the full node and edge lists and can be
loaded into any graph visualization tool.

## Pipeline position

```
Evidence Layer + Knowledge Layer + Decision Support Layer
  -> [Research Knowledge Graph]
     -- reads from all layers
     -- maps all relationships
     -- surfaces cross-strategy patterns
     -> REVIEW_REQUIRED
       -> Human Decision
```

The Knowledge Graph is the final synthesis layer before `REVIEW_REQUIRED`.
It does not replace the Decision Package — it answers a different question:
not "is this strategy ready?" but "what does everything we know connect to?"
