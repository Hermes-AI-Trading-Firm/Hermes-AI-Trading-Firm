#!/usr/bin/env python3
"""
Research Knowledge Graph -- research/knowledge_graph/graph_builder.py

Phase 32

Question: "What relationships exist across everything we've learned?"

The Three-Question Test:
  Can it produce better evidence?   YES -- maps all knowledge connections
  Can it ask a better question?     YES -- surfaces cross-strategy relationships
  Does a human still decide?        YES -- read-only advisory, no automated actions

Graph model
-----------
Nodes
  Strategy          -- a strategy spec in the research pipeline
  Archetype         -- a strategy archetype (ORB, VWAP Pullback, etc.)
  Backtest          -- an in-sample backtest result
  Score             -- a composite scoring result
  AuditResult       -- an audit report
  MonteCarloResult  -- Monte Carlo validation result
  WalkForwardResult -- walk-forward validation result
  RegimeFinding     -- regime analysis result
  Pattern           -- a failure or strength pattern from the pattern library
  LearningReview    -- an AI learning brain review
  DecisionPackage   -- a decision package (readiness assessment)
  LifecycleState    -- a lifecycle state node (shared across strategies)

Edges
  strategy  HAS_ARCHETYPE          archetype
  strategy  HAS_BACKTEST           backtest
  strategy  HAS_SCORE              score
  strategy  HAS_AUDIT_FINDING      audit_result
  strategy  HAS_MONTE_CARLO_RESULT mc_result
  strategy  HAS_WALK_FORWARD_RESULT wf_result
  strategy  HAS_REGIME_FINDING     regime_finding
  strategy  MATCHES_PATTERN        pattern
  strategy  HAS_LEARNING_REVIEW    learning_review
  strategy  HAS_DECISION_PACKAGE   decision_package
  strategy  HAS_LIFECYCLE_STATE    lifecycle_state
  archetype HAS_PRIOR              pattern

Node IDs
  strategy:{spec_id}          e.g. strategy:3
  archetype:{archetype_id}    e.g. archetype:orb
  backtest:{backtest_id}      e.g. backtest:7
  score:{scoring_id}          e.g. score:12
  audit:{spec_id}             e.g. audit:3
  mc:{spec_id}                e.g. mc:3
  wf:{spec_id}                e.g. wf:3
  regime:{spec_id}            e.g. regime:3
  pattern:{key}               e.g. pattern:pf_overfit_risk
  learning:{spec_id}          e.g. learning:3
  decision_pkg:{spec_id}      e.g. decision_pkg:3
  lifecycle:{state}           e.g. lifecycle:REVIEW_REQUIRED

What it does NOT do
-------------------
- Does not write to any database table
- Does not modify strategy specs, scores, or classifications
- Does not approve or reject strategies
- Does not advance any strategy past REVIEW_REQUIRED

Usage
-----
    python -m research.knowledge_graph.graph_builder --build
    python -m research.knowledge_graph.graph_builder --build --dry-run
    python -m research.knowledge_graph.graph_builder --spec-id N
    python -m research.knowledge_graph.graph_builder --archetype orb
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB           = _PROJECT_ROOT / "database"  / "hermes_research.db"
REPORTS_DIR          = _PROJECT_ROOT / "reports"   / "knowledge_graph"
AUDIT_DIR            = _PROJECT_ROOT / "reports"   / "audits"
VALIDATION_DIR       = _PROJECT_ROOT / "reports"   / "validation"
REGIME_DIR           = _PROJECT_ROOT / "reports"   / "regime"
DECISION_PKG_DIR     = _PROJECT_ROOT / "reports"   / "decision_packages"
LEARNING_DIR         = _PROJECT_ROOT / "reports"   / "learning"
CLASSIFICATION_PATH  = _PROJECT_ROOT / "research"  / "archetype" / "classifications.json"
PATTERN_LIB_PATH     = _PROJECT_ROOT / "research"  / "memory"    / "pattern_library.json"
ARCHETYPES_PATH      = _PROJECT_ROOT / "research"  / "archetype" / "archetypes.json"
APPROVED_DIR         = _PROJECT_ROOT / "research"  / "approved"
REJECTED_DIR         = _PROJECT_ROOT / "research"  / "rejected"
ARCHIVED_DIR         = _PROJECT_ROOT / "research"  / "archived"

# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------
NT_STRATEGY    = "Strategy"
NT_ARCHETYPE   = "Archetype"
NT_BACKTEST    = "Backtest"
NT_SCORE       = "Score"
NT_AUDIT       = "AuditResult"
NT_MC          = "MonteCarloResult"
NT_WF          = "WalkForwardResult"
NT_REGIME      = "RegimeFinding"
NT_PATTERN     = "Pattern"
NT_LEARNING    = "LearningReview"
NT_DECISION    = "DecisionPackage"
NT_LIFECYCLE   = "LifecycleState"

# ---------------------------------------------------------------------------
# Edge relationships
# ---------------------------------------------------------------------------
REL_HAS_ARCHETYPE = "HAS_ARCHETYPE"
REL_HAS_BACKTEST  = "HAS_BACKTEST"
REL_HAS_SCORE     = "HAS_SCORE"
REL_HAS_AUDIT     = "HAS_AUDIT_FINDING"
REL_HAS_MC        = "HAS_MONTE_CARLO_RESULT"
REL_HAS_WF        = "HAS_WALK_FORWARD_RESULT"
REL_HAS_REGIME    = "HAS_REGIME_FINDING"
REL_MATCHES_PAT   = "MATCHES_PATTERN"
REL_HAS_LEARNING  = "HAS_LEARNING_REVIEW"
REL_HAS_DECISION  = "HAS_DECISION_PACKAGE"
REL_HAS_LIFECYCLE = "HAS_LIFECYCLE_STATE"
REL_HAS_PRIOR     = "HAS_PRIOR"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Node:
    id:         str
    node_type:  str
    label:      str
    properties: Dict = field(default_factory=dict)


@dataclass
class Edge:
    source:       str
    target:       str
    relationship: str
    properties:   Dict = field(default_factory=dict)


@dataclass
class Graph:
    nodes:    Dict[str, Node] = field(default_factory=dict)
    edges:    List[Edge]      = field(default_factory=list)
    built_at: str             = ""

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node

    def add_edge(self, source: str, target: str,
                 rel: str, props: Optional[Dict] = None) -> None:
        if source in self.nodes and target in self.nodes:
            self.edges.append(Edge(source, target, rel, props or {}))

    def neighbors(self, node_id: str) -> List[str]:
        result: Set[str] = set()
        for e in self.edges:
            if e.source == node_id:
                result.add(e.target)
            elif e.target == node_id:
                result.add(e.source)
        return list(result)

    def edges_for(self, node_id: str) -> List[Edge]:
        return [e for e in self.edges
                if e.source == node_id or e.target == node_id]

    def nodes_of_type(self, node_type: str) -> List[Node]:
        return [n for n in self.nodes.values() if n.node_type == node_type]


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name)


def _latest_json(directory: Path, prefix: str, infix: str) -> Optional[Dict]:
    if not directory.exists():
        return None
    matches = sorted(directory.glob(f"{prefix}{infix}*.json"))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def _any_file(directory: Path, prefix: str) -> bool:
    if not directory.exists():
        return False
    return any(directory.glob(f"{prefix}*"))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_specs(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute(
        "SELECT spec_id, spec_name, COALESCE(symbol,''), COALESCE(timeframe,'') "
        "FROM strategy_specs ORDER BY spec_id"
    ).fetchall()
    return [{"spec_id": r[0], "spec_name": r[1],
             "symbol": r[2], "timeframe": r[3]} for r in rows]


def _load_backtest(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT backtest_id, net_profit, profit_factor, win_rate,
               total_trades, max_drawdown_pct,
               trade_list_json IS NOT NULL AND trade_list_json != '' AS has_tl
        FROM backtests
        WHERE spec_id = ? AND is_in_sample = 1
        ORDER BY backtest_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    return {
        "backtest_id": row[0], "net_profit": row[1],
        "profit_factor": row[2], "win_rate": row[3],
        "total_trades": row[4], "max_drawdown_pct": row[5],
        "has_trade_list": bool(row[6]),
    }


def _load_score(conn: sqlite3.Connection, spec_id: int) -> Optional[Dict]:
    row = conn.execute("""
        SELECT scoring_id, composite_score, grade, recommendation,
               walk_forward_score, walk_forward_pass,
               monte_carlo_score, monte_carlo_pass,
               prop_firm_supported, overfitting_risk
        FROM scoring_results
        WHERE spec_id = ? ORDER BY scoring_id DESC LIMIT 1
    """, (spec_id,)).fetchone()
    if not row:
        return None
    return {
        "scoring_id": row[0], "composite_score": row[1],
        "grade": row[2], "recommendation": row[3],
        "walk_forward_score": row[4], "walk_forward_pass": bool(row[5]),
        "monte_carlo_score": row[6], "monte_carlo_pass": bool(row[7]),
        "prop_firm_supported": bool(row[8]), "overfitting_risk": row[9],
    }


# ---------------------------------------------------------------------------
# Lifecycle inference (simplified -- no import of lifecycle module)
# ---------------------------------------------------------------------------

def _infer_lifecycle(
    conn:     sqlite3.Connection,
    spec_id:  int,
    safe_name: str,
    score:    Optional[Dict],
) -> str:
    if _any_file(ARCHIVED_DIR, safe_name):
        return "ARCHIVED"
    if _any_file(REJECTED_DIR, safe_name):
        return "HUMAN_REJECTED"
    if _any_file(APPROVED_DIR, safe_name):
        return "HUMAN_APPROVED"

    pkg = _latest_json(DECISION_PKG_DIR, safe_name, "_decision_package_")
    if pkg:
        if pkg.get("readiness_status") == "READY_FOR_HUMAN_REVIEW":
            return "REVIEW_REQUIRED"
        return "DECISION_PACKAGED"

    if _latest_json(REGIME_DIR, safe_name, "_regime_analysis_"):
        return "REGIME_ANALYZED"

    wf = (score or {}).get("walk_forward_score")
    if wf is not None or _latest_json(VALIDATION_DIR, safe_name, "_walk_forward_"):
        return "VALIDATED_WF"

    mc = (score or {}).get("monte_carlo_score")
    if mc is not None or _latest_json(VALIDATION_DIR, safe_name, "_monte_carlo_"):
        return "VALIDATED_MC"

    if _latest_json(AUDIT_DIR, safe_name, "_"):
        return "AUDITED"

    if score:
        return "SCORED"

    row = conn.execute(
        "SELECT 1 FROM backtests WHERE spec_id = ? AND is_in_sample = 1 LIMIT 1",
        (spec_id,)
    ).fetchone()
    if row:
        return "BACKTEST_IMPORTED"

    return "SPEC_IMPORTED"


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------

def _build_strategy_node(spec: Dict) -> Node:
    sid = spec["spec_id"]
    return Node(
        id        = f"strategy:{sid}",
        node_type = NT_STRATEGY,
        label     = spec["spec_name"],
        properties = {
            "spec_id":   sid,
            "spec_name": spec["spec_name"],
            "symbol":    spec["symbol"],
            "timeframe": spec["timeframe"],
        },
    )


def _attach_archetype(
    graph:           Graph,
    strategy_id:     str,
    spec_id:         int,
    classifications: Dict,
) -> Optional[str]:
    clsf  = classifications.get("classifications", {}).get(str(spec_id), {})
    arch  = clsf.get("primary",       "unknown")
    label = clsf.get("primary_label", "Unknown")
    node_id = f"archetype:{arch}"
    if node_id not in graph.nodes:
        graph.add_node(Node(
            id        = node_id,
            node_type = NT_ARCHETYPE,
            label     = label,
            properties = {"archetype_id": arch, "label": label},
        ))
    graph.add_edge(strategy_id, node_id, REL_HAS_ARCHETYPE,
                   {"confidence": clsf.get("confidence")})
    return arch


def _attach_backtest(
    graph:       Graph,
    strategy_id: str,
    bt:          Dict,
) -> None:
    node_id = f"backtest:{bt['backtest_id']}"
    graph.add_node(Node(
        id        = node_id,
        node_type = NT_BACKTEST,
        label     = f"Backtest {bt['backtest_id']}",
        properties = bt,
    ))
    graph.add_edge(strategy_id, node_id, REL_HAS_BACKTEST)


def _attach_score(
    graph:       Graph,
    strategy_id: str,
    sc:          Dict,
) -> None:
    node_id = f"score:{sc['scoring_id']}"
    graph.add_node(Node(
        id        = node_id,
        node_type = NT_SCORE,
        label     = f"Score {sc['scoring_id']} ({sc.get('grade','?')})",
        properties = sc,
    ))
    graph.add_edge(strategy_id, node_id, REL_HAS_SCORE,
                   {"composite_score": sc.get("composite_score")})


def _attach_audit(
    graph:       Graph,
    strategy_id: str,
    spec_id:     int,
    spec_name:   str,
) -> None:
    data = _latest_json(AUDIT_DIR, _safe(spec_name), "_")
    if not data:
        return
    node_id = f"audit:{spec_id}"
    graph.add_node(Node(
        id        = node_id,
        node_type = NT_AUDIT,
        label     = f"Audit: {spec_name}",
        properties = {
            "pass_count":     data.get("pass_count", 0),
            "warn_count":     data.get("warn_count", 0),
            "fail_count":     data.get("fail_count", 0),
            "recommendation": data.get("recommendation", ""),
            "audited_at":     data.get("audited_at", ""),
            "fail_checks": [
                c for c in data.get("checks", []) if c.get("status") == "FAIL"
            ],
        },
    ))
    graph.add_edge(strategy_id, node_id, REL_HAS_AUDIT,
                   {"fail_count": data.get("fail_count", 0)})


def _attach_mc(
    graph:       Graph,
    strategy_id: str,
    spec_id:     int,
    spec_name:   str,
    score:       Optional[Dict],
) -> None:
    mc_score = (score or {}).get("monte_carlo_score")
    data = _latest_json(VALIDATION_DIR, _safe(spec_name), "_monte_carlo_")
    if mc_score is None and not data:
        return
    node_id = f"mc:{spec_id}"
    props = {
        "monte_carlo_score": mc_score,
        "monte_carlo_pass":  (score or {}).get("monte_carlo_pass"),
    }
    if data:
        props.update({
            "survival_rate":        data.get("survival_rate"),
            "probability_positive": data.get("probability_positive"),
            "simulations":          data.get("simulations"),
            "worst_drawdown":       data.get("worst_drawdown"),
            "method":               data.get("method"),
        })
    graph.add_node(Node(
        id        = node_id,
        node_type = NT_MC,
        label     = f"Monte Carlo: {spec_name}",
        properties = props,
    ))
    graph.add_edge(strategy_id, node_id, REL_HAS_MC,
                   {"score": mc_score})


def _attach_wf(
    graph:       Graph,
    strategy_id: str,
    spec_id:     int,
    spec_name:   str,
    score:       Optional[Dict],
) -> None:
    wf_score = (score or {}).get("walk_forward_score")
    data = _latest_json(VALIDATION_DIR, _safe(spec_name), "_walk_forward_")
    if wf_score is None and not data:
        return
    node_id = f"wf:{spec_id}"
    props = {
        "walk_forward_score": wf_score,
        "walk_forward_pass":  (score or {}).get("walk_forward_pass"),
    }
    if data:
        comps = data.get("components") or {}
        props.update({
            "pf_retention":        comps.get("pf_retention"),
            "expectancy_retention": comps.get("expectancy_retention"),
            "dd_component":        comps.get("dd_component"),
        })
    graph.add_node(Node(
        id        = node_id,
        node_type = NT_WF,
        label     = f"Walk-Forward: {spec_name}",
        properties = props,
    ))
    graph.add_edge(strategy_id, node_id, REL_HAS_WF,
                   {"score": wf_score})


def _attach_regime(
    graph:       Graph,
    strategy_id: str,
    spec_id:     int,
    spec_name:   str,
) -> None:
    data = _latest_json(REGIME_DIR, _safe(spec_name), "_regime_analysis_")
    if not data:
        return
    node_id = f"regime:{spec_id}"
    graph.add_node(Node(
        id        = node_id,
        node_type = NT_REGIME,
        label     = f"Regime: {spec_name}",
        properties = {
            "mode":         data.get("mode"),
            "best_window":  data.get("best_window"),
            "worst_window": data.get("worst_window"),
            "window_count": len(data.get("windows", [])),
        },
    ))
    graph.add_edge(strategy_id, node_id, REL_HAS_REGIME,
                   {"best_window": data.get("best_window")})


def _attach_patterns(
    graph:       Graph,
    strategy_id: str,
    spec_id:     int,
    pattern_lib: Dict,
) -> None:
    records = pattern_lib.get("strategy_records", {})
    rec     = records.get(str(spec_id)) or records.get(spec_id)
    if not rec:
        return

    for pat in rec.get("failure_patterns", []):
        pat_key = str(pat).replace(" ", "_").lower()[:40]
        node_id = f"pattern:{pat_key}"
        if node_id not in graph.nodes:
            graph.add_node(Node(
                id        = node_id,
                node_type = NT_PATTERN,
                label     = str(pat),
                properties = {"key": pat_key, "pattern_type": "failure"},
            ))
        graph.add_edge(strategy_id, node_id, REL_MATCHES_PAT,
                       {"pattern_type": "failure"})

    for pat in rec.get("strength_patterns", []):
        pat_key = f"strength_{str(pat).replace(' ', '_').lower()[:35]}"
        node_id = f"pattern:{pat_key}"
        if node_id not in graph.nodes:
            graph.add_node(Node(
                id        = node_id,
                node_type = NT_PATTERN,
                label     = str(pat),
                properties = {"key": pat_key, "pattern_type": "strength"},
            ))
        graph.add_edge(strategy_id, node_id, REL_MATCHES_PAT,
                       {"pattern_type": "strength"})


def _attach_learning(
    graph:       Graph,
    strategy_id: str,
    spec_id:     int,
    spec_name:   str,
) -> None:
    data = _latest_json(LEARNING_DIR, _safe(spec_name), "_learning_review_")
    if not data:
        return
    node_id = f"learning:{spec_id}"
    graph.add_node(Node(
        id        = node_id,
        node_type = NT_LEARNING,
        label     = f"Learning: {spec_name}",
        properties = {
            "readiness_status":  data.get("readiness_status"),
            "failure_count":     len(data.get("failure_patterns", [])),
            "strength_count":    len(data.get("strength_patterns", [])),
            "action_count":      len(data.get("next_actions", [])),
            "generated_at":      data.get("generated_at"),
        },
    ))
    graph.add_edge(strategy_id, node_id, REL_HAS_LEARNING,
                   {"readiness_status": data.get("readiness_status")})


def _attach_decision_pkg(
    graph:       Graph,
    strategy_id: str,
    spec_id:     int,
    spec_name:   str,
) -> None:
    data = _latest_json(DECISION_PKG_DIR, _safe(spec_name), "_decision_package_")
    if not data:
        return
    node_id = f"decision_pkg:{spec_id}"
    hard_blockers = sum(
        1 for b in data.get("blockers", []) if b.get("severity") == "BLOCKER"
    )
    graph.add_node(Node(
        id        = node_id,
        node_type = NT_DECISION,
        label     = f"Decision Package: {spec_name}",
        properties = {
            "readiness_status": data.get("readiness_status"),
            "hard_blockers":    hard_blockers,
            "warning_count":    sum(
                1 for b in data.get("blockers", []) if b.get("severity") == "WARNING"
            ),
            "strength_count":   len(data.get("strengths", [])),
            "generated_at":     data.get("generated_at"),
        },
    ))
    graph.add_edge(strategy_id, node_id, REL_HAS_DECISION,
                   {"readiness_status": data.get("readiness_status"),
                    "hard_blockers": hard_blockers})


def _attach_lifecycle(
    graph:       Graph,
    strategy_id: str,
    conn:        sqlite3.Connection,
    spec_id:     int,
    spec_name:   str,
    score:       Optional[Dict],
) -> str:
    state   = _infer_lifecycle(conn, spec_id, _safe(spec_name), score)
    node_id = f"lifecycle:{state}"
    if node_id not in graph.nodes:
        graph.add_node(Node(
            id        = node_id,
            node_type = NT_LIFECYCLE,
            label     = state,
            properties = {"state": state},
        ))
    graph.add_edge(strategy_id, node_id, REL_HAS_LIFECYCLE)
    return state


def _attach_archetype_priors(
    graph:        Graph,
    archetypes:   Dict,
    pattern_lib:  Dict,
) -> None:
    priors = pattern_lib.get("strategy_type_priors", {})
    for arch_id, arch_data in archetypes.items():
        if arch_id in ("hybrid", "unknown"):
            continue
        arch_node_id = f"archetype:{arch_id}"
        if arch_node_id not in graph.nodes:
            graph.add_node(Node(
                id        = arch_node_id,
                node_type = NT_ARCHETYPE,
                label     = arch_data.get("label", arch_id),
                properties = {"archetype_id": arch_id},
            ))
        prior_data = priors.get(arch_id) or priors.get(arch_data.get("prior_key"))
        if not prior_data:
            continue
        for obs in prior_data.get("observations", []):
            metric = obs.get("metric", "")
            if not metric:
                continue
            pat_key = f"prior_{arch_id}_{metric.replace(' ', '_').lower()[:30]}"
            node_id = f"pattern:{pat_key}"
            if node_id not in graph.nodes:
                graph.add_node(Node(
                    id        = node_id,
                    node_type = NT_PATTERN,
                    label     = f"{arch_id}: {metric}={obs.get('value','')}",
                    properties = {
                        "key":          pat_key,
                        "pattern_type": "prior",
                        "archetype":    arch_id,
                        "metric":       metric,
                        "value":        obs.get("value"),
                        "note":         obs.get("note", ""),
                    },
                ))
            graph.add_edge(arch_node_id, node_id, REL_HAS_PRIOR)


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_graph(conn: sqlite3.Connection) -> Graph:
    """
    Build the full research knowledge graph from DB + file evidence.
    No writes. No state changes. Pure read.
    """
    graph = Graph(built_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

    # Load supporting data
    classifications: Dict = {}
    if CLASSIFICATION_PATH.exists():
        try:
            classifications = json.loads(
                CLASSIFICATION_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    pattern_lib: Dict = {}
    if PATTERN_LIB_PATH.exists():
        try:
            pattern_lib = json.loads(
                PATTERN_LIB_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    archetypes: Dict = {}
    if ARCHETYPES_PATH.exists():
        try:
            archetypes = json.loads(
                ARCHETYPES_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    specs = _load_specs(conn)

    for spec in specs:
        sid  = spec["spec_id"]
        name = spec["spec_name"]

        # Strategy node
        s_node = _build_strategy_node(spec)
        graph.add_node(s_node)
        sid_str = s_node.id

        # Evidence nodes + edges
        _attach_archetype(graph, sid_str, sid, classifications)

        bt = _load_backtest(conn, sid)
        if bt:
            _attach_backtest(graph, sid_str, bt)

        sc = _load_score(conn, sid)
        if sc:
            _attach_score(graph, sid_str, sc)

        _attach_audit(graph, sid_str, sid, name)
        _attach_mc(graph, sid_str, sid, name, sc)
        _attach_wf(graph, sid_str, sid, name, sc)
        _attach_regime(graph, sid_str, sid, name)
        _attach_patterns(graph, sid_str, sid, pattern_lib)
        _attach_learning(graph, sid_str, sid, name)
        _attach_decision_pkg(graph, sid_str, sid, name)
        _attach_lifecycle(graph, sid_str, conn, sid, name, sc)

    # Archetype priors
    _attach_archetype_priors(graph, archetypes, pattern_lib)

    return graph


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def graph_stats(graph: Graph) -> Dict:
    type_counts: Dict[str, int] = {}
    for n in graph.nodes.values():
        type_counts[n.node_type] = type_counts.get(n.node_type, 0) + 1

    rel_counts: Dict[str, int] = {}
    for e in graph.edges:
        rel_counts[e.relationship] = rel_counts.get(e.relationship, 0) + 1

    return {
        "node_count":  len(graph.nodes),
        "edge_count":  len(graph.edges),
        "node_types":  type_counts,
        "edge_types":  rel_counts,
        "built_at":    graph.built_at,
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_graph_json(graph: Graph, path: Path) -> None:
    stats = graph_stats(graph)
    data = {
        "built_at":   graph.built_at,
        "statistics": stats,
        "nodes": [
            {
                "id":         n.id,
                "node_type":  n.node_type,
                "label":      n.label,
                "properties": n.properties,
            }
            for n in graph.nodes.values()
        ],
        "edges": [
            {
                "source":       e.source,
                "target":       e.target,
                "relationship": e.relationship,
                "properties":   e.properties,
            }
            for e in graph.edges
        ],
    }
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def export_graph_markdown(
    graph: Graph,
    path:  Path,
    query_results: Optional[Dict] = None,
) -> None:
    from research.knowledge_graph.graph_queries import (
        query_shared_failure_patterns,
        query_review_ready_candidates,
    )

    stats = graph_stats(graph)
    lines: List[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    p("# Research Knowledge Graph")
    p(f"**Built:** {graph.built_at[:10]}  |  "
      f"**Nodes:** {stats['node_count']}  |  "
      f"**Edges:** {stats['edge_count']}")
    p()
    p("> What relationships exist across everything we have learned?")
    p()
    p("---")
    p()

    # Node distribution
    p("## Node Distribution")
    p()
    p("| Node Type | Count |")
    p("|-----------|-------|")
    for nt, count in sorted(stats["node_types"].items(), key=lambda x: -x[1]):
        p(f"| {nt} | {count} |")
    p()

    # Edge distribution
    p("## Edge Distribution")
    p()
    p("| Relationship | Count |")
    p("|-------------|-------|")
    for rel, count in sorted(stats["edge_types"].items(), key=lambda x: -x[1]):
        p(f"| {rel} | {count} |")
    p()

    # Review-ready candidates
    rr = query_review_ready_candidates(graph)
    p("## Strategies at REVIEW_REQUIRED")
    p()
    if rr:
        for n in rr:
            p(f"- **{n.label}**  (spec_id={n.properties.get('spec_id')})")
        p()
        p("*These strategies have completed the automated pipeline. "
          "Human review is required.*")
    else:
        p("*No strategies currently at REVIEW_REQUIRED.*")
    p()

    # Shared failure patterns
    shared = query_shared_failure_patterns(graph)
    p("## Shared Failure Patterns")
    p()
    if shared:
        p("Patterns appearing across multiple strategies:")
        p()
        p("| Pattern | Strategies |")
        p("|---------|-----------|")
        for pat_node, strat_nodes in shared:
            names = ", ".join(n.label for n in strat_nodes)
            p(f"| {pat_node.label} | {names} |")
    else:
        p("*No shared failure patterns detected across strategies.*")
    p()

    # Strategy neighborhood table
    p("## Strategy Evidence Map")
    p()
    strategy_nodes = graph.nodes_of_type(NT_STRATEGY)
    if strategy_nodes:
        p("| Strategy | Archetype | Score | MC | WF | Regime | Lifecycle |")
        p("|----------|-----------|-------|----|----|--------|-----------|")
        for sn in sorted(strategy_nodes, key=lambda n: n.properties.get("spec_id", 0)):
            sid     = sn.properties.get("spec_id")
            sid_str = f"strategy:{sid}"

            arch    = next(
                (graph.nodes[e.target].label
                 for e in graph.edges
                 if e.source == sid_str and e.relationship == REL_HAS_ARCHETYPE
                 and e.target in graph.nodes),
                "-"
            )
            score   = next(
                (f"{graph.nodes[e.target].properties.get('composite_score','?')}"
                 for e in graph.edges
                 if e.source == sid_str and e.relationship == REL_HAS_SCORE
                 and e.target in graph.nodes),
                "-"
            )
            has_mc  = any(e.source == sid_str and e.relationship == REL_HAS_MC
                          for e in graph.edges)
            has_wf  = any(e.source == sid_str and e.relationship == REL_HAS_WF
                          for e in graph.edges)
            has_reg = any(e.source == sid_str and e.relationship == REL_HAS_REGIME
                          for e in graph.edges)
            lc      = next(
                (graph.nodes[e.target].label
                 for e in graph.edges
                 if e.source == sid_str and e.relationship == REL_HAS_LIFECYCLE
                 and e.target in graph.nodes),
                "-"
            )
            p(f"| {sn.label} | {arch} | {score} "
              f"| {'Y' if has_mc else '-'} | {'Y' if has_wf else '-'} "
              f"| {'Y' if has_reg else '-'} | {lc} |")
    p()

    p("---")
    p()
    p("*Read-only advisory output. No database writes. No strategy changes.*")
    p("*Accumulate evidence. Improve questions. Preserve authority.*")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_reports(
    graph:       Graph,
    reports_dir: Path = REPORTS_DIR,
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y%m%d")
    json_path = reports_dir / f"knowledge_graph_{date_str}.json"
    md_path   = reports_dir / f"knowledge_graph_{date_str}.md"
    export_graph_json(graph, json_path)
    export_graph_markdown(graph, md_path)
    return md_path, json_path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_stats(graph: Graph) -> None:
    stats = graph_stats(graph)
    print(f"Knowledge Graph  |  {stats['node_count']} nodes  |  "
          f"{stats['edge_count']} edges")
    print()
    print("  Node types:")
    for nt, count in sorted(stats["node_types"].items(), key=lambda x: -x[1]):
        print(f"    {nt:<25}  {count:>4}")
    print()
    print("  Edge types:")
    for rel, count in sorted(stats["edge_types"].items(), key=lambda x: -x[1]):
        print(f"    {rel:<30}  {count:>4}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    from research.knowledge_graph.graph_queries import (
        query_strategy_neighborhood,
        query_shared_failure_patterns,
        query_archetype_evidence,
        query_review_ready_candidates,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Research Knowledge Graph (Phase 32). "
            "Question: What relationships exist across everything we have learned? "
            "No DB writes. No strategy changes. REVIEW_REQUIRED is terminal."
        )
    )
    parser.add_argument("--build",     action="store_true",
                        help="Build graph and export reports")
    parser.add_argument("--spec-id",   type=int, metavar="ID",
                        help="Show neighborhood for one strategy")
    parser.add_argument("--archetype", metavar="ID",
                        help="Show evidence map for an archetype")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Console output only -- no files written")
    parser.add_argument("--db",        default=str(DEFAULT_DB), metavar="PATH")
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR), metavar="DIR")
    args = parser.parse_args()

    if not args.build and args.spec_id is None and not args.archetype:
        parser.error("Specify --build, --spec-id N, or --archetype ID")

    db_path     = Path(args.db)
    reports_dir = Path(args.reports_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Hermes Research Knowledge Graph  [{mode}]")
    print(f"  DB           : {db_path}")
    if not args.dry_run:
        print(f"  Reports      : {reports_dir}")
    print(f"  Advisory only -- no DB writes, no strategy changes")
    print()

    conn = sqlite3.connect(str(db_path))
    try:
        print("Building graph...")
        graph = build_graph(conn)
    finally:
        conn.close()

    _print_stats(graph)

    # Review-ready
    rr = query_review_ready_candidates(graph)
    if rr:
        print(f"  Strategies at REVIEW_REQUIRED ({len(rr)}):")
        for n in rr:
            print(f"    {n.label}  (spec_id={n.properties.get('spec_id')})")
        print()

    # Shared failure patterns
    shared = query_shared_failure_patterns(graph)
    if shared:
        print(f"  Shared failure patterns ({len(shared)}):")
        for pat_node, strat_nodes in shared:
            names = ", ".join(n.label for n in strat_nodes)
            print(f"    {pat_node.label}")
            print(f"      -> {names}")
        print()

    # Spec neighborhood
    if args.spec_id is not None:
        nodes, edges = query_strategy_neighborhood(graph, args.spec_id)
        if not nodes:
            print(f"  spec_id={args.spec_id} not found in graph")
        else:
            print(f"  Neighborhood: spec_id={args.spec_id}")
            print(f"    {len(nodes)} nodes  {len(edges)} edges")
            for n in sorted(nodes, key=lambda x: x.node_type):
                print(f"    [{n.node_type:<22}]  {n.label}")
            print()

    # Archetype evidence
    if args.archetype:
        nodes, edges = query_archetype_evidence(graph, args.archetype)
        if not nodes:
            print(f"  Archetype '{args.archetype}' not found in graph")
        else:
            strats = [n for n in nodes if n.node_type == NT_STRATEGY]
            print(f"  Archetype '{args.archetype}': "
                  f"{len(strats)} strategies, {len(nodes)} total nodes, "
                  f"{len(edges)} edges")
            for n in strats:
                print(f"    {n.label}")
            print()

    # Write reports
    if not args.dry_run and (args.build or args.spec_id is not None):
        md_path, json_path = write_reports(graph, reports_dir)
        print(f"  Reports")
        print(f"    MD  : {md_path}")
        print(f"    JSON: {json_path}")
        print()
    elif args.dry_run:
        print("DRY-RUN complete. No files written. No DB changes.")


if __name__ == "__main__":
    main()
