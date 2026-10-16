"""Graph store, ontology and algorithm tests."""

from __future__ import annotations

import pytest

from src.graph.algorithms import (
    ALGORITHMS,
    betweenness_centrality,
    blast_radius,
    detect_cycles,
    pagerank_criticality,
    run_algorithm,
    shortest_dependency_path,
)
from src.graph.ontology import (
    NODE_BY_LABEL,
    Track,
    validate_schema,
)
from src.graph.store import InMemoryStore


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_merge_and_read_roundtrip(store, settings):
    store.merge_nodes(
        settings.prod_graph,
        "Service",
        [{"id": "SVC-T1", "name": "Test", "tier": 1}],
    )
    node = store.get_node(settings.prod_graph, "Service", "SVC-T1")
    assert node is not None
    assert node["name"] == "Test"
    assert node["tier"] == 1


def test_merge_is_idempotent(store, settings):
    for _ in range(3):
        store.merge_nodes(
            settings.prod_graph,
            "Service",
            [{"id": "SVC-T2", "name": "Idem", "tier": 2}],
        )
    nodes = [
        n
        for n in store.find_nodes(settings.prod_graph, "Service", limit=500)
        if n["id"] == "SVC-T2"
    ]
    assert len(nodes) == 1, "MERGE must not create duplicate nodes"


def test_tenant_isolation(store, settings):
    """Two tenants must not see each other's nodes."""
    store.merge_nodes(
        settings.prod_graph, "Service", [{"id": "SVC-ONLY-A", "tier": 1}]
    )
    store.merge_nodes(
        settings.memory_graph, "Service", [{"id": "SVC-ONLY-B", "tier": 1}]
    )
    assert store.get_node(settings.prod_graph, "Service", "SVC-ONLY-B") is None
    assert store.get_node(settings.memory_graph, "Service", "SVC-ONLY-A") is None


def test_drop_graph_removes_it_from_listing(store, settings):
    store.merge_nodes(
        settings.prod_graph, "Service", [{"id": "SVC-DROP", "tier": 1}]
    )
    assert settings.prod_graph in store.list_graphs()
    store.drop_graph(settings.prod_graph)
    assert settings.prod_graph not in store.list_graphs()


def test_delete_node_removes_it(store, settings):
    store.merge_nodes(
        settings.prod_graph, "Service", [{"id": "SVC-DEL", "tier": 1}]
    )
    store.delete_node(settings.prod_graph, "Service", "SVC-DEL")
    assert store.get_node(settings.prod_graph, "Service", "SVC-DEL") is None


def test_delete_edge_removes_it(store, settings):
    store.merge_nodes(
        settings.prod_graph,
        "Service",
        [{"id": "SVC-E1", "tier": 1}, {"id": "SVC-E2", "tier": 2}],
    )
    store.merge_edges(
        settings.prod_graph,
        "CALLS",
        [{"source": "SVC-E1", "target": "SVC-E2"}],
    )
    assert store.traverse(settings.prod_graph, "SVC-E1", 1, ["CALLS"])
    store.delete_edge(settings.prod_graph, "CALLS", "SVC-E1", "SVC-E2")
    assert store.traverse(settings.prod_graph, "SVC-E1", 1, ["CALLS"]) == []


def test_snapshot_edge_endpoints_resolve(seeded):
    """Every edge endpoint must name a node that exists in the same snapshot."""
    store, settings = seeded
    snapshot = store.snapshot(settings.prod_graph, limit=8000)
    ids = {n["id"] for n in snapshot.nodes}
    dangling = [
        edge
        for edge in snapshot.edges
        if edge["source"] not in ids or edge["target"] not in ids
    ]
    assert not dangling, f"dangling edges: {dangling[:5]}"


def test_in_memory_store_matches_contract():
    """The fallback backend must satisfy the same contract as FalkorDB."""
    store = InMemoryStore()
    store.merge_nodes("g", "Service", [{"id": "A", "tier": 1}])
    store.merge_nodes("g", "Service", [{"id": "B", "tier": 2}])
    store.merge_edges(
        "g", "CALLS", [{"source": "A", "target": "B", "properties": {"latency_p99": 10}}]
    )
    assert store.get_node("g", "Service", "A")["tier"] == 1
    assert store.counts("g") == (2, 1)
    paths = store.traverse("g", "A", 2, ["CALLS"], direction="out")
    assert paths and paths[0].nodes[-1]["id"] == "B"


# ---------------------------------------------------------------------------
# Path integrity - the bug class that silently corrupted results
# ---------------------------------------------------------------------------


def test_traversal_edge_indices_align_with_path_nodes(seeded):
    """Edge endpoints on a path must be real members of that path.

    FalkorDB's Edge.src_node/dest_node are engine-global ids, not offsets into
    path.nodes(). Treating them as offsets produced edges pointing at unrelated
    nodes, so this asserts the invariant directly.
    """
    store, settings = seeded
    paths = store.traverse(
        settings.prod_graph, "SVC-AUTH", 4, ["CALLS"], direction="in", limit=10
    )
    assert paths
    for path in paths:
        ids = {node["id"] for node in path.nodes}
        for edge in path.edges:
            assert edge["source"] in ids, f"edge source {edge['source']} not in path"
            assert edge["target"] in ids, f"edge target {edge['target']} not in path"


def test_reverse_traversal_finds_upstream_callers(seeded):
    store, settings = seeded
    paths = store.traverse(
        settings.prod_graph, "SVC-AUTH", 3, ["CALLS"], direction="in", limit=20
    )
    found = {node["id"] for path in paths for node in path.nodes}
    assert "SVC-ORDER" in found, "OrderGateway calls AuthService and must be found"


# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------


def test_every_registered_algorithm_is_runnable(seeded):
    store, settings = seeded
    for name in ALGORITHMS:
        kwargs = {}
        if name == "shortest_path":
            kwargs = {"source_id": "SVC-AUTH", "target_id": "SVC-PAY"}
        elif name == "blast_radius":
            kwargs = {"origin_ids": ["SVC-AUTH"]}
        result = run_algorithm(name, store, settings.prod_graph, **kwargs)
        assert result.algorithm == name
        assert result.justification, f"{name} produced no justification"


def test_unknown_algorithm_raises(seeded):
    store, settings = seeded
    with pytest.raises(ValueError):
        run_algorithm("not_an_algorithm", store, settings.prod_graph)


def test_pagerank_scores_are_positive_and_ranked(seeded):
    store, settings = seeded
    result = pagerank_criticality(store, settings.prod_graph)
    assert result.scores
    assert all(score > 0 for score in result.scores.values())
    ordered = list(result.scores.values())
    assert ordered == sorted(ordered, reverse=True), "scores must be ranked"


def test_betweenness_is_computed_natively(seeded):
    """FalkorDB's algo.Betweenness returns empty, so this is our own implementation."""
    store, settings = seeded
    result = betweenness_centrality(store, settings.prod_graph)
    assert result.scores, "betweenness must produce scores"
    assert any(score > 0 for score in result.scores.values())


def test_blast_radius_is_reverse_and_bounded(seeded):
    store, settings = seeded
    result = blast_radius(
        store,
        settings.prod_graph,
        ["SVC-AUTH"],
        max_hops=3,
        edge_types=["CALLS"],
    )
    assert "SVC-AUTH" not in result.scores, "origin must not be its own casualty"
    assert result.scores, "AuthService has callers and must have a blast radius"


def test_blast_radius_respects_edge_filter(seeded):
    store, settings = seeded
    calls_only = blast_radius(
        store, settings.prod_graph, ["SVC-AUTH"], max_hops=3, edge_types=["CALLS"]
    )
    everything = blast_radius(store, settings.prod_graph, ["SVC-AUTH"], max_hops=3)
    assert set(calls_only.scores) <= set(everything.scores)


def test_shortest_path_between_known_services(seeded):
    store, settings = seeded
    result = shortest_dependency_path(
        store, settings.prod_graph, "SVC-ORDER", "SVC-AUTH", edge_types=["CALLS"]
    )
    assert result.path, "OrderGateway calls AuthService; a path must exist"
    assert result.path[0] == "SVC-ORDER"
    assert result.path[-1] == "SVC-AUTH"


def test_shortest_path_returns_empty_when_unreachable(seeded):
    store, settings = seeded
    result = shortest_dependency_path(
        store,
        settings.prod_graph,
        "SVC-SEARCH",
        "SVC-AUTH",
        edge_types=["CALLS"],
    )
    assert result.path == []


def test_cycle_detection_finds_the_seeded_service_cycle(seeded):
    """The seeded estate deliberately contains OrderGateway <-> BillingWorker."""
    store, settings = seeded
    result = detect_cycles(
        store, settings.prod_graph, node_label="Service", edge_types=["CALLS"]
    )
    assert result.cycles, "the seeded graph contains a deliberate CALLS cycle"
    members = {node for cycle in result.cycles for node in cycle}
    assert "SVC-ORDER" in members and "SVC-BILL" in members


def test_cycle_detection_is_clean_when_no_cycle_exists(store, settings):
    store.merge_nodes(
        settings.prod_graph,
        "ASTModule",
        [
            {"id": "M-A", "file_path": "a.py"},
            {"id": "M-B", "file_path": "b.py"},
        ],
    )
    store.merge_edges(
        settings.prod_graph,
        "IMPORTS",
        [{"source": "M-A", "target": "M-B"}],
    )
    result = detect_cycles(store, settings.prod_graph, node_label="ASTModule")
    assert result.cycles == []


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------


def test_schema_validates():
    assert validate_schema() == []


def test_tracks_are_assigned_across_all_three_dimensions():
    tracks = {spec.track for spec in NODE_BY_LABEL.values()}
    assert Track.PRESENT in tracks
    assert Track.MEMORY in tracks
    assert Track.BRAIN in tracks


def test_every_node_spec_declares_a_distinct_key():
    """``key`` is the merge identity and is listed separately from ``properties``.

    Every label must declare one, and it must not be repeated in ``properties``,
    which holds only the *non-identity* fields.
    """
    for spec in NODE_BY_LABEL.values():
        assert spec.key, f"{spec.label} declares no merge key"
        assert spec.key not in spec.properties, (
            f"{spec.label}: key {spec.key!r} must not be repeated in properties"
        )


def test_node_keys_are_unique_across_labels():
    keys = [spec.qualified_key for spec in NODE_BY_LABEL.values()]
    assert len(keys) == len(set(keys))
