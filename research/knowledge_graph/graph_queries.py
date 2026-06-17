#!/usr/bin/env python3
"""
Research Knowledge Graph -- Queries
research/knowledge_graph/graph_queries.py

Read-only query functions operating on a Graph object built by graph_builder.py.
No DB access. No file writes. Pure graph traversal.

Functions
---------
query_strategy_neighborhood(graph, spec_id)
    All nodes and edges within 1 hop of a strategy.

query_shared_failure_patterns(graph)
    Failure patterns connected to more than one strategy.

query_archetype_evidence(graph, archetype_id)
    All evidence nodes reachable from strategies of a given archetype.

query_review_ready_candidates(graph)
    Strategy nodes whose lifecycle state is REVIEW_REQUIRED.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from research.knowledge_graph.graph_builder import (
    Graph, Node, Edge,
    NT_STRATEGY, NT_ARCHETYPE, NT_PATTERN, NT_LIFECYCLE,
    REL_HAS_ARCHETYPE, REL_MATCHES_PAT, REL_HAS_LIFECYCLE,
)


# ---------------------------------------------------------------------------
# query_strategy_neighborhood
# ---------------------------------------------------------------------------

def query_strategy_neighborhood(
    graph:   Graph,
    spec_id: int,
) -> Tuple[List[Node], List[Edge]]:
    """
    Return all nodes and edges within one hop of the strategy node
    for the given spec_id.

    Returns (nodes, edges). Nodes include the strategy node itself
    and all directly connected nodes. Empty if spec_id not found.
    """
    strategy_id = f"strategy:{spec_id}"
    if strategy_id not in graph.nodes:
        return [], []

    neighbor_ids: Set[str] = {strategy_id}
    relevant_edges: List[Edge] = []

    for e in graph.edges:
        if e.source == strategy_id:
            neighbor_ids.add(e.target)
            relevant_edges.append(e)
        elif e.target == strategy_id:
            neighbor_ids.add(e.source)
            relevant_edges.append(e)

    nodes = [graph.nodes[nid] for nid in neighbor_ids if nid in graph.nodes]
    return nodes, relevant_edges


# ---------------------------------------------------------------------------
# query_shared_failure_patterns
# ---------------------------------------------------------------------------

def query_shared_failure_patterns(
    graph: Graph,
) -> List[Tuple[Node, List[Node]]]:
    """
    Return failure patterns that appear in more than one strategy.

    Returns a list of (pattern_node, [strategy_nodes]) sorted by
    number of strategies descending.
    """
    # Map pattern_id -> set of strategy_ids
    pat_to_strats: Dict[str, Set[str]] = {}

    for e in graph.edges:
        if e.relationship != REL_MATCHES_PAT:
            continue
        if e.properties.get("pattern_type") != "failure":
            continue
        pat_id   = e.target
        strat_id = e.source
        if pat_id not in graph.nodes or strat_id not in graph.nodes:
            continue
        if graph.nodes[strat_id].node_type != NT_STRATEGY:
            continue
        pat_to_strats.setdefault(pat_id, set()).add(strat_id)

    # Only patterns shared across 2+ strategies
    shared = [
        (graph.nodes[pat_id], [graph.nodes[sid] for sid in strat_ids
                               if sid in graph.nodes])
        for pat_id, strat_ids in pat_to_strats.items()
        if len(strat_ids) >= 2 and pat_id in graph.nodes
    ]

    shared.sort(key=lambda x: -len(x[1]))
    return shared


# ---------------------------------------------------------------------------
# query_archetype_evidence
# ---------------------------------------------------------------------------

def query_archetype_evidence(
    graph:        Graph,
    archetype_id: str,
) -> Tuple[List[Node], List[Edge]]:
    """
    Return all nodes and edges reachable from strategies classified
    under the given archetype_id (e.g. 'orb', 'vwap_pullback').

    Includes:
    - The archetype node itself
    - All strategy nodes linked to this archetype
    - All evidence nodes linked to those strategies

    Returns (nodes, edges).
    """
    arch_node_id = f"archetype:{archetype_id}"

    # Find strategy nodes linked to this archetype
    strategy_ids: Set[str] = set()
    arch_edges:   List[Edge] = []

    for e in graph.edges:
        if e.relationship == REL_HAS_ARCHETYPE and e.target == arch_node_id:
            strategy_ids.add(e.source)
            arch_edges.append(e)

    if not strategy_ids and arch_node_id not in graph.nodes:
        return [], []

    # Collect all nodes reachable from those strategies
    all_node_ids: Set[str] = set()
    all_edges:    List[Edge] = list(arch_edges)

    if arch_node_id in graph.nodes:
        all_node_ids.add(arch_node_id)

    for sid in strategy_ids:
        all_node_ids.add(sid)
        for e in graph.edges:
            if e.source == sid or e.target == sid:
                all_node_ids.add(e.source)
                all_node_ids.add(e.target)
                if e not in all_edges:
                    all_edges.append(e)

    nodes = [graph.nodes[nid] for nid in all_node_ids if nid in graph.nodes]
    return nodes, all_edges


# ---------------------------------------------------------------------------
# query_review_ready_candidates
# ---------------------------------------------------------------------------

def query_review_ready_candidates(graph: Graph) -> List[Node]:
    """
    Return strategy nodes whose current lifecycle state is REVIEW_REQUIRED.

    These are strategies that have completed the automated research pipeline
    and are awaiting human review. REVIEW_REQUIRED is the terminal automated
    state -- no further automated action will advance them.
    """
    rr_lifecycle_id = "lifecycle:REVIEW_REQUIRED"

    strategy_ids_at_rr: Set[str] = set()
    for e in graph.edges:
        if (e.relationship == REL_HAS_LIFECYCLE
                and e.target == rr_lifecycle_id):
            strategy_ids_at_rr.add(e.source)

    candidates = [
        graph.nodes[sid]
        for sid in strategy_ids_at_rr
        if sid in graph.nodes and graph.nodes[sid].node_type == NT_STRATEGY
    ]
    candidates.sort(key=lambda n: n.properties.get("spec_id", 0))
    return candidates
