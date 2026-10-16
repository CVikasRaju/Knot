"""Topological graph algorithms exposed to the swarm as agent tools.

Design note: these run over a subgraph *projected out of FalkorDB by Cypher*
rather than through FalkorDB's bundled GraphBLAS procedures. That is a
deliberate trade-off. The bundled ``algo.*`` procedures vary between FalkorDB
builds (``algo.Betweenness`` returns an empty result set on several released
images), whereas projection-plus-compute is stable everywhere, keeps the
mathematics inspectable in Python, and lets every result be traced back to the
exact node/edge set that produced it - which is what the explainability
requirement in Track 01 actually demands.

Every algorithm returns :class:`AlgorithmResult` carrying both the numbers and
a reproducible justification string.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import networkx as nx

from .ontology import CAUSAL_CHAIN_RELATIONSHIPS, TRAVERSABLE_RELATIONSHIPS
from .store import GraphSnapshot, GraphStore


@dataclass
class AlgorithmResult:
    algorithm: str
    graph: str
    scores: dict[str, float] = field(default_factory=dict)
    path: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    justification: str = ""

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm,
            "graph": self.graph,
            "scores": self.scores,
            "path": self.path,
            "cycles": self.cycles,
            "details": self.details,
            "justification": self.justification,
        }


def _build_digraph(
    snapshot: GraphSnapshot,
    node_label: str | None = None,
    edge_types: Sequence[str] | None = None,
) -> nx.DiGraph:
    graph = nx.DiGraph()
    allowed = set(edge_types) if edge_types else None
    for node in snapshot.nodes:
        if node_label and node.get("label") != node_label:
            continue
        graph.add_node(node["id"], **node)
    for edge in snapshot.edges:
        if allowed and edge["type"] not in allowed:
            continue
        if edge["source"] in graph and edge["target"] in graph:
            graph.add_edge(
                edge["source"],
                edge["target"],
                type=edge["type"],
                **(edge.get("properties") or {}),
            )
    return graph


def _snapshot_for(
    store: GraphStore,
    graph: str,
    node_label: str | None,
    edge_types: Sequence[str] | None,
    limit: int = 4000,
) -> GraphSnapshot:
    snapshot = store.snapshot(graph, limit=limit)
    if node_label:
        keep = {n["id"] for n in snapshot.nodes if n.get("label") == node_label}
        snapshot = GraphSnapshot(
            nodes=[n for n in snapshot.nodes if n["id"] in keep],
            edges=[
                e
                for e in snapshot.edges
                if e["source"] in keep and e["target"] in keep
            ],
        )
    return snapshot


def pagerank_criticality(
    store: GraphStore,
    graph: str,
    node_label: str | None = "Service",
    edge_types: Sequence[str] | None = None,
    top_k: int = 10,
) -> AlgorithmResult:
    """Rank nodes by how much of the system depends on them.

    PageRank on a call graph approximates "if this node misbehaves, how much
    traffic-weighted blame reaches it". It is the tool used to find the
    critical-but-undocumented modules that nobody owns.
    """
    edge_types = edge_types or ["CALLS"]
    snapshot = _snapshot_for(store, graph, node_label, edge_types)
    digraph = _build_digraph(snapshot, node_label, edge_types)
    if digraph.number_of_nodes() == 0:
        return AlgorithmResult(
            algorithm="pagerank_criticality",
            graph=graph,
            justification="No nodes matched the projection, so no ranking exists.",
        )
    try:
        raw = nx.pagerank(digraph, alpha=0.85, max_iter=200)
    except nx.PowerIterationFailedConvergence:
        raw = nx.pagerank(digraph, alpha=0.85, max_iter=1000, tol=1e-06)
    ranked = dict(sorted(raw.items(), key=lambda kv: kv[1], reverse=True)[:top_k])
    top_name = ranked and next(iter(ranked))
    justification = (
        f"PageRank(alpha=0.85) over {digraph.number_of_nodes()} nodes and "
        f"{digraph.number_of_edges()} {edge_types} edges. "
        f"Highest criticality: {top_name} at score {ranked[top_name]:.4f}."
        if top_name
        else "PageRank produced no ranking."
    )
    return AlgorithmResult(
        algorithm="pagerank_criticality",
        graph=graph,
        scores={k: round(v, 6) for k, v in ranked.items()},
        details={
            "node_count": digraph.number_of_nodes(),
            "edge_count": digraph.number_of_edges(),
            "edge_types": list(edge_types),
        },
        justification=justification,
    )


def betweenness_centrality(
    store: GraphStore,
    graph: str,
    node_label: str | None = "Service",
    edge_types: Sequence[str] | None = None,
    top_k: int = 10,
) -> AlgorithmResult:
    """Find single points of failure: nodes on the most shortest paths."""
    edge_types = edge_types or ["CALLS"]
    snapshot = _snapshot_for(store, graph, node_label, edge_types)
    digraph = _build_digraph(snapshot, node_label, edge_types)
    if digraph.number_of_nodes() < 3:
        return AlgorithmResult(
            algorithm="betweenness_centrality",
            graph=graph,
            justification="Fewer than three nodes; centrality is undefined.",
        )
    raw = nx.betweenness_centrality(digraph, normalized=True)
    ranked = dict(sorted(raw.items(), key=lambda kv: kv[1], reverse=True)[:top_k])
    top_name = next(iter(ranked))
    justification = (
        f"Normalised betweenness over {digraph.number_of_nodes()} nodes. "
        f"{top_name} carries the most shortest paths at {ranked[top_name]:.4f}, "
        f"making it the primary single point of failure."
    )
    return AlgorithmResult(
        algorithm="betweenness_centrality",
        graph=graph,
        scores={k: round(v, 6) for k, v in ranked.items()},
        details={
            "node_count": digraph.number_of_nodes(),
            "edge_count": digraph.number_of_edges(),
        },
        justification=justification,
    )


def shortest_dependency_path(
    store: GraphStore,
    graph: str,
    source_id: str,
    target_id: str,
    node_label: str | None = None,
    edge_types: Sequence[str] | None = None,
) -> AlgorithmResult:
    """Shortest causal path between two entities, following causal edges."""
    edge_types = edge_types or list(CAUSAL_CHAIN_RELATIONSHIPS)
    snapshot = _snapshot_for(store, graph, None, None)
    # The causal chain mixes labels, so build a graph over the traversable
    # projection and then restrict the edge set to the causal vocabulary.
    digraph = _build_digraph(snapshot, None, edge_types)
    if source_id not in digraph or target_id not in digraph:
        return AlgorithmResult(
            algorithm="shortest_dependency_path",
            graph=graph,
            justification=(
                f"Either {source_id} or {target_id} is absent from the "
                f"causal projection, so no path can exist."
            ),
        )
    try:
        path = nx.shortest_path(digraph, source_id, target_id)
    except nx.NetworkXNoPath:
        return AlgorithmResult(
            algorithm="shortest_dependency_path",
            graph=graph,
            justification=f"No causal path connects {source_id} to {target_id}.",
        )
    edges = [
        {
            "type": digraph.edges[path[i], path[i + 1]]["type"],
            "source": path[i],
            "target": path[i + 1],
        }
        for i in range(len(path) - 1)
    ]
    return AlgorithmResult(
        algorithm="shortest_dependency_path",
        graph=graph,
        path=path,
        details={"edges": edges, "hop_count": len(path) - 1},
        justification=(
            f"BFS shortest path over {len(edge_types)} causal relationship types "
            f"yields {' -> '.join(path)} ({len(path) - 1} hops)."
        ),
    )


def blast_radius(
    store: GraphStore,
    graph: str,
    origin_ids: Sequence[str],
    max_hops: int = 5,
    edge_types: Sequence[str] | None = None,
) -> AlgorithmResult:
    """Everything downstream that a change to ``origin_ids`` can reach.

    This is the counterfactual core: run it on the shadow graph and the
    ``max_hops`` distance distribution *is* the predicted blast radius.
    """
    edge_types = edge_types or list(TRAVERSABLE_RELATIONSHIPS)
    snapshot = _snapshot_for(store, graph, None, None)
    digraph = _build_digraph(snapshot, None, edge_types)
    # Propagation runs *against* the call direction: if A calls B and B breaks,
    # A is impacted. Reversing here keeps the reasoning readable.
    reversed_graph = digraph.reverse(copy=True)

    reached: dict[str, int] = {}
    paths: dict[str, list[str]] = {}
    queue: deque[tuple[str, int, list[str]]] = deque(
        (origin, 0, [origin]) for origin in origin_ids
    )
    seen_depth: dict[str, int] = {}
    while queue:
        node, depth, path = queue.popleft()
        if depth > 0:
            if node not in reached or depth < reached[node]:
                reached[node] = depth
                paths[node] = path
        if depth >= max_hops:
            continue
        if seen_depth.get(node, 1 << 30) <= depth:
            continue
        seen_depth[node] = depth
        for neighbour in reversed_graph.successors(node):
            if neighbour in path:
                continue
            queue.append((neighbour, depth + 1, path + [neighbour]))

    impacted = {
        node_id: float(depth)
        for node_id, depth in sorted(reached.items(), key=lambda kv: kv[1])
    }
    tiers = {
        n["id"]: n.get("tier")
        for n in snapshot.nodes
        if n["id"] in impacted
    }
    tier1 = [nid for nid, tier in tiers.items() if tier == 1]
    longest = max(paths.values(), key=len) if paths else []
    return AlgorithmResult(
        algorithm="blast_radius",
        graph=graph,
        scores=impacted,
        path=longest,
        details={
            "origins": list(origin_ids),
            "max_hops": max_hops,
            "impacted_count": len(impacted),
            "tier1_impacted": tier1,
            "hop_distribution": {
                str(depth): sum(1 for d in reached.values() if d == depth)
                for depth in range(1, max_hops + 1)
            },
            "impact_paths": {k: v for k, v in paths.items()},
        },
        justification=(
            f"Reverse-propagated from {list(origin_ids)} up to {max_hops} hops "
            f"across {len(edge_types)} relationship types: {len(impacted)} nodes "
            f"impacted, {len(tier1)} of them Tier-1. Deepest chain: "
            f"{' -> '.join(longest) if longest else 'none'}."
        ),
    )


def detect_cycles(
    store: GraphStore,
    graph: str,
    node_label: str | None = "ASTModule",
    edge_types: Sequence[str] | None = None,
    limit: int = 20,
) -> AlgorithmResult:
    """Find dependency cycles - the classic cause of deploy-time deadlock."""
    edge_types = edge_types or ["IMPORTS", "CALLS"]
    snapshot = _snapshot_for(store, graph, node_label, edge_types)
    digraph = _build_digraph(snapshot, node_label, edge_types)
    cycles: list[list[str]] = []
    for cycle in nx.simple_cycles(digraph, length_bound=8):
        cycles.append(list(cycle) + [cycle[0]])
        if len(cycles) >= limit:
            break
    return AlgorithmResult(
        algorithm="cycle_detection",
        graph=graph,
        cycles=cycles,
        details={
            "cycle_count": len(cycles),
            "node_count": digraph.number_of_nodes(),
            "edge_types": list(edge_types),
        },
        justification=(
            f"Found {len(cycles)} dependency cycle(s) in the "
            f"{node_label or 'full'} projection."
            if cycles
            else f"No dependency cycles detected in the {node_label or 'full'} projection."
        ),
    )


def node_degree(
    store: GraphStore,
    graph: str,
    node_label: str | None = None,
    edge_types: Sequence[str] | None = None,
) -> AlgorithmResult:
    """In/out degree table - cheap fan-in signal for risk scoring."""
    edge_types = edge_types or list(TRAVERSABLE_RELATIONSHIPS)
    snapshot = _snapshot_for(store, graph, node_label, None)
    digraph = _build_digraph(snapshot, node_label, edge_types)
    scores = {
        node: float(digraph.in_degree(node)) for node in digraph.nodes
    }
    return AlgorithmResult(
        algorithm="node_degree",
        graph=graph,
        scores=scores,
        details={
            "out_degree": {
                node: digraph.out_degree(node) for node in digraph.nodes
            }
        },
        justification=(
            f"Computed fan-in over {digraph.number_of_nodes()} nodes; highest "
            f"fan-in is {max(scores, key=scores.get) if scores else 'n/a'}."
        ),
    )


ALGORITHMS = {
    "pagerank_criticality": pagerank_criticality,
    "betweenness_centrality": betweenness_centrality,
    "shortest_path": shortest_dependency_path,
    "blast_radius": blast_radius,
    "cycle_detection": detect_cycles,
    "node_degree": node_degree,
}


def run_algorithm(name: str, store: GraphStore, graph: str, **kwargs) -> AlgorithmResult:
    """Dispatch an algorithm by name - the entry point used by the MCP tool.

    The registry key is echoed back as ``result.algorithm`` so a caller can
    correlate a request with its response. Without this the two diverge whenever
    a registry key and the implementation's own name differ (``shortest_path``
    versus ``shortest_dependency_path``), and a client cannot tell which
    algorithm produced a given payload.
    """
    if name not in ALGORITHMS:
        raise ValueError(
            f"unknown algorithm {name!r}; expected one of {sorted(ALGORITHMS)}"
        )
    result = ALGORITHMS[name](store, graph, **kwargs)
    result.algorithm = name
    return result
