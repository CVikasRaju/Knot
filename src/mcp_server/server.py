"""ChronosGraph MCP server.

Every graph capability the swarm uses is registered here as a Model Context
Protocol tool, so an external MCP client (Claude Desktop, Cursor, another
LangGraph swarm) can chain the same primitives.

Two design decisions worth stating, because they are what make this a *graph*
MCP server rather than a chat wrapper:

1. Tools return structure, not prose. Each result carries the node ids, the hop
   chain, and the Cypher that produced it. An LLM client can therefore cite its
   evidence, and a human can replay it.
2. The mutating tool (``execute_verified_remediation``) requires a
   ``proof_path_ids`` array and refuses to act when the ids are not backed by a
   certificate. Exposing a "block this merge" tool without that guard would be
   handing an LLM the production keys.

Run it with ``python -m src.mcp_server.server`` (stdio) or
``python -m src.mcp_server.server --transport sse --port 8765``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from fastmcp import FastMCP

from ..actions.diff_parser import build_mutation_spec
from ..actions.expertise import ExpertiseRanker
from ..actions.remediation import ActionRefused, RemediationExecutor
from ..actions.shadow_sim import MutationSpec, ShadowSimulator
from ..config import Settings, get_settings
from ..graph.algorithms import ALGORITHMS, run_algorithm
from ..graph.investigate import (
    multi_hop_investigate,
    trace_decision_provenance,
    triage_incident,
)
from ..graph.ontology import EDGES, NODES, validate_schema
from ..graph.store import GraphStore, open_store
from ..memory.memory import MemoryStore
from ..memory.proof import ProofCertificate

mcp: FastMCP = FastMCP(
    name="chronosgraph",
    instructions=(
        "ChronosGraph exposes a temporal enterprise knowledge graph on FalkorDB. "
        "Use graph_multi_hop_investigate to trace causality, "
        "run_graph_topology_algorithm to find single points of failure, "
        "fork_and_simulate_shadow_graph to predict the blast radius of an "
        "unmerged pull request, memory_read_write_coordinator to recall how a "
        "similar incident was fixed before, and "
        "trace_company_decision_provenance to find out why code exists and who "
        "owns it. Mutating tools require proof_path_ids from a prior traversal."
    ),
)

_state: dict[str, Any] = {}


def _settings() -> Settings:
    if "settings" not in _state:
        _state["settings"] = get_settings()
    return _state["settings"]


def _store() -> GraphStore:
    if "store" not in _state:
        _state["store"] = open_store(_settings())
    return _state["store"]


def _memory() -> MemoryStore:
    if "memory" not in _state:
        _state["memory"] = MemoryStore(_store(), _settings().memory_graph, _settings().prod_graph)
    return _state["memory"]


def _executor() -> RemediationExecutor:
    if "executor" not in _state:
        settings = _settings()
        _state["executor"] = RemediationExecutor(dry_run=settings.actions_dry_run)
    return _state["executor"]


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


# ---------------------------------------------------------------------------
# 1. Multi-hop investigation (Track 01)
# ---------------------------------------------------------------------------


@mcp.tool
def graph_multi_hop_investigate(
    start_node_id: str,
    max_hops: int = 4,
    relationship_filter: list[str] | None = None,
    direction: str = "out",
    tenant_graph: str = "",
) -> str:
    """Trace bounded multi-hop causal paths from any entity in the graph.

    Args:
        start_node_id: Entity to start from (e.g. "ALT-904", "SVC-AUTH",
            "PR-812", "MOD-AUTH-TOKEN").
        max_hops: Traversal depth, 1-8.
        relationship_filter: Edge types to follow. Defaults to every traversable
            relationship in the ontology.
        direction: "out", "in" (reverse dependencies), or "both".
        tenant_graph: Graph tenant name. Defaults to the production brain.

    Returns:
        JSON with nodes, edges, ranked hop paths, and an explainable trace.
    """
    settings = _settings()
    graph = tenant_graph or settings.prod_graph
    max_hops = max(1, min(int(max_hops), 8))
    if direction not in {"out", "in", "both"}:
        direction = "out"

    result = multi_hop_investigate(
        _store(),
        graph,
        start_node_id,
        max_hops=max_hops,
        relationship_filter=relationship_filter,
        direction=direction,
    )
    return _json(result.to_dict())


# ---------------------------------------------------------------------------
# 2. Graph algorithms (Track 01)
# ---------------------------------------------------------------------------


@mcp.tool
def run_graph_topology_algorithm(
    algorithm: str,
    source_id: str = "",
    target_id: str = "",
    subgraph_label: str = "Service",
    edge_types: list[str] | None = None,
    tenant_graph: str = "",
) -> str:
    """Run a structural graph algorithm and return scores with justification.

    Args:
        algorithm: One of "pagerank_criticality", "betweenness_centrality",
            "shortest_path", "blast_radius", "cycle_detection", "node_degree".
        source_id: Start node for path/blast algorithms.
        target_id: Target node for shortest_path.
        subgraph_label: Restrict the projection to one node label.
        edge_types: Relationship types to include in the projection.
        tenant_graph: Graph tenant name.

    Returns:
        JSON with ranked scores or path sequences plus a justification string.
    """
    settings = _settings()
    graph = tenant_graph or settings.prod_graph
    if algorithm not in ALGORITHMS:
        return _json(
            {
                "error": f"unknown algorithm {algorithm!r}",
                "available": sorted(ALGORITHMS),
            }
        )
    kwargs: dict[str, Any] = {}
    if algorithm == "shortest_path":
        if not source_id or not target_id:
            return _json({"error": "shortest_path requires source_id and target_id"})
        kwargs.update({"source_id": source_id, "target_id": target_id})
        if edge_types:
            kwargs["edge_types"] = edge_types
    elif algorithm == "blast_radius":
        if not source_id:
            return _json({"error": "blast_radius requires source_id"})
        kwargs.update({"origin_ids": [source_id], "max_hops": settings.max_hops})
        if edge_types:
            kwargs["edge_types"] = edge_types
    else:
        kwargs["node_label"] = subgraph_label or None
        if edge_types:
            kwargs["edge_types"] = edge_types

    result = run_algorithm(algorithm, _store(), graph, **kwargs)
    return _json(result.to_dict())


# ---------------------------------------------------------------------------
# 3. Shadow-graph simulation (Track 01 + 03)
# ---------------------------------------------------------------------------


@mcp.tool
def fork_and_simulate_shadow_graph(
    pr_number: str,
    modified_modules: list[str] | None = None,
    diff_text: str = "",
    breaking_schema_changes: bool = False,
    simulated_latency_delta_ms: float = 0.0,
    max_hops: int = 5,
    keep_shadow: bool = False,
) -> str:
    """Fork production into an isolated tenant and predict a PR's blast radius.

    The fork is a real FalkorDB graph copy, so the prediction is a traversal that
    can be replayed. The shadow tenant is dropped again unless ``keep_shadow``.

    Args:
        pr_number: Pull request identifier, e.g. "PR-812".
        modified_modules: ASTModule ids the PR touches.
        diff_text: Unified diff; parsed with tree-sitter to derive the delta.
        breaking_schema_changes: Assert that the PR breaks a payload contract.
        simulated_latency_delta_ms: Latency the PR is expected to add.
        max_hops: Propagation depth for the blast radius.
        keep_shadow: Keep the shadow tenant for inspection instead of dropping it.

    Returns:
        JSON verdict (SAFE/AT_RISK/BLOCKED), recommended action, impacted
        services, ADR violations, cascading path trace and proof certificates.
    """
    settings = _settings()
    store = _store()

    mutation: MutationSpec
    if diff_text:
        modules = store.find_nodes(settings.prod_graph, "ASTModule", limit=500)
        fields = store.find_nodes(settings.prod_graph, "SchemaField", limit=500)
        mutation, _parsed = build_mutation_spec(
            diff_text,
            pr_number,
            modules,
            fields,
            declare_breaking=breaking_schema_changes,
            latency_delta_ms=simulated_latency_delta_ms,
        )
    else:
        mutation = MutationSpec(
            modified_module_ids=list(modified_modules or []),
            breaking_schema_change=breaking_schema_changes,
            latency_delta_ms=simulated_latency_delta_ms,
        )

    simulator = ShadowSimulator(
        store,
        settings.prod_graph,
        settings.shadow_prefix,
        settings.criticality_alert_threshold,
        keep_shadow=keep_shadow,
    )
    result = simulator.simulate(pr_number, mutation, max_hops=max_hops)
    return _json(result.to_dict())


# ---------------------------------------------------------------------------
# 4. Memory coordinator (Track 02)
# ---------------------------------------------------------------------------


@mcp.tool
def memory_read_write_coordinator(
    operation: str,
    session_id: str,
    agent_id: str,
    symptom_signature: str = "",
    payload: dict | None = None,
) -> str:
    """Read or persist episodic, semantic and procedural agent memory.

    Args:
        operation: One of "retrieve_similar_episode", "retrieve_playbook",
            "store_episode", "store_procedural_playbook", "handoff_agent_state".
        session_id: Agent session id.
        agent_id: Name of the calling agent.
        symptom_signature: Structural signature to match against.
        payload: Operation-specific body.

    Returns:
        JSON with the matched memories or the persisted node.
    """
    memory = _memory()
    payload = payload or {}

    if operation == "retrieve_similar_episode":
        episodes = memory.retrieve_similar_episodes(symptom_signature, top_k=5)
        return _json({"episodes": [e.to_dict() for e in episodes]})

    if operation == "retrieve_playbook":
        match = memory.retrieve_playbook(symptom_signature)
        return _json({"playbook_match": match.to_dict() if match else None})

    if operation == "store_episode":
        stored = memory.store_episode(
            incident_id=payload.get("incident_id") or f"INC-{session_id[-6:]}",
            summary=payload.get("summary", ""),
            symptom_signature=symptom_signature,
            affected_service_ids=payload.get("affected_service_ids", []),
            severity=payload.get("severity", "SEV2"),
            mttr_seconds=int(payload.get("mttr_seconds", 0)),
            extra={"agent_id": agent_id, "session_id": session_id},
        )
        return _json({"stored_episode": stored})

    if operation == "store_procedural_playbook":
        stored = memory.store_playbook(
            playbook_id=payload.get("playbook_id") or f"PB-{session_id[-6:]}",
            name=payload.get("name", "learned playbook"),
            trigger_signature=symptom_signature,
            action_steps=payload.get("action_steps", []),
            success_rate=float(payload.get("success_rate", 1.0)),
            derived_from_incident=payload.get("derived_from_incident"),
        )
        return _json({"stored_playbook": stored})

    if operation == "handoff_agent_state":
        step = memory.record_step(
            session_id=session_id,
            step_index=int(payload.get("step_index", 1)),
            agent_name=agent_id,
            tool_called=payload.get("tool_called", "handoff"),
            cypher_executed=payload.get("cypher", ""),
            observation=payload.get("observation", "handoff"),
            inspected_node_ids=payload.get("inspected_node_ids", []),
            handoff_to=payload.get("handoff_to"),
            handoff_context=payload.get("context_summary", ""),
        )
        return _json({"recorded_step": step})

    return _json(
        {
            "error": f"unknown operation {operation!r}",
            "available": [
                "retrieve_similar_episode",
                "retrieve_playbook",
                "store_episode",
                "store_procedural_playbook",
                "handoff_agent_state",
            ],
        }
    )


# ---------------------------------------------------------------------------
# 5. Decision provenance (Track 03)
# ---------------------------------------------------------------------------


@mcp.tool
def trace_company_decision_provenance(
    entity_id: str,
    include_expert_ranking: bool = True,
    tenant_graph: str = "",
) -> str:
    """Walk backwards from code to the decisions and people that shaped it.

    Args:
        entity_id: Service, ASTModule, APISchema or ADR id.
        include_expert_ranking: Include a weighted subject-matter-expert list.
        tenant_graph: Graph tenant name.

    Returns:
        JSON with a chronological decision timeline, governing ADRs, and a
        ranked expert list with per-signal explanations.
    """
    settings = _settings()
    graph = tenant_graph or settings.prod_graph
    provenance = trace_decision_provenance(_store(), graph, entity_id)

    experts: list[dict] = []
    if include_expert_ranking:
        ranker = ExpertiseRanker(_store(), graph)
        experts = [e.to_dict() for e in ranker.rank(entity_id, top_k=5)]

    return _json(
        {
            **provenance.to_dict(),
            "experts": experts,
        }
    )


# ---------------------------------------------------------------------------
# 6. Incident triage (Track 01 + 02)
# ---------------------------------------------------------------------------


@mcp.tool
def triage_live_incident(
    alert_id: str,
    window_hours: int = 2,
    max_hops: int = 4,
    tenant_graph: str = "",
) -> str:
    """Root-cause a firing alert and recall a matching procedural playbook.

    Args:
        alert_id: Alert node id, e.g. "ALT-904".
        window_hours: Only consider PRs opened within this many hours.
        max_hops: Upstream traversal depth.
        tenant_graph: Graph tenant name.

    Returns:
        JSON with ranked suspect causes, the structural signature, a blast
        radius, and any recalled episode or playbook.
    """
    settings = _settings()
    graph = tenant_graph or settings.prod_graph
    result = triage_incident(
        _store(), graph, alert_id, window_hours=window_hours, max_hops=max_hops
    )
    memory = _memory()
    episodes = memory.retrieve_similar_episodes(result.signature, top_k=3)
    playbook = memory.retrieve_playbook(result.signature)
    return _json(
        {
            **result.to_dict(),
            "episodes": [e.to_dict() for e in episodes],
            "playbook_match": playbook.to_dict() if playbook else None,
        }
    )


# ---------------------------------------------------------------------------
# 7. Verified remediation (Track 01 action tool)
# ---------------------------------------------------------------------------


@mcp.tool
def execute_verified_remediation(
    action_type: str,
    target_id: str,
    proof_path_ids: list[str],
    remediation_note: str,
    claim: str = "",
    confidence: float = 0.0,
    certificate_id: str = "",
    graph: str = "",
    expert_handle: str = "",
    team: str = "",
) -> str:
    """Execute a remediation, but only with a certificate that proves it is safe.

    This tool refuses to act unless ``proof_path_ids`` is non-empty and the
    accompanying claim clears the configured confidence threshold. There is no
    override: an agent that cannot show its evidence cannot mutate production.

    Args:
        action_type: "create_revert_pr", "block_ci_merge", "apply_hotfix_patch",
            "sync_stale_adr_doc", "page_domain_expert", "quarantine_endpoint".
        target_id: What the action applies to.
        proof_path_ids: Node ids from a prior traversal that justify the action.
        remediation_note: Operator-facing explanation.
        claim: The claim the proof supports.
        confidence: Confidence in the claim, 0.0-1.0.
        certificate_id: Id of a prior certificate, for traceability.
        graph: Graph the proof came from.
        expert_handle: Target handle for page_domain_expert.
        team: Target team for page_domain_expert.

    Returns:
        JSON action record, or an error explaining the refusal.
    """
    settings = _settings()
    proof = ProofCertificate(
        claim=claim or f"{action_type} on {target_id}",
        verdict="PROVEN" if confidence >= settings.confidence_threshold else "PARTIAL",
        confidence=confidence,
        graph=graph or settings.prod_graph,
        node_ids=list(proof_path_ids),
        cypher=_store().last_cypher(),
        certificate_id=certificate_id or f"PROOF-{target_id}",
    )

    executor = _executor()
    kwargs: dict[str, Any] = {
        "target_id": target_id,
        "proof": proof,
        "proof_path_ids": proof_path_ids,
        "remediation_note": remediation_note,
    }
    try:
        if action_type == "create_revert_pr":
            result = executor.create_revert_pr(**kwargs)
        elif action_type == "block_ci_merge":
            result = executor.block_ci_merge(**kwargs)
        elif action_type == "apply_hotfix_patch":
            result = executor.apply_hotfix_patch(**kwargs)
        elif action_type == "sync_stale_adr_doc":
            result = executor.sync_stale_adr_doc(
                **kwargs, stale_doc_ids=proof_path_ids
            )
        elif action_type == "page_domain_expert":
            result = executor.page_domain_expert(
                **kwargs, expert_handle=expert_handle, team=team
            )
        elif action_type == "quarantine_endpoint":
            result = executor.quarantine_endpoint(**kwargs)
        else:
            return _json({"error": f"unknown action_type {action_type!r}"})
    except ActionRefused as exc:
        return _json({"refused": True, "reason": str(exc)})
    return _json(result.to_dict())


# ---------------------------------------------------------------------------
# 8. Schema introspection - lets an MCP client discover the ontology
# ---------------------------------------------------------------------------


@mcp.tool
def describe_graph_schema() -> str:
    """Return the ChronosGraph ontology: node labels, edges and track mapping.

    Useful for an MCP client that needs to know which relationship types exist
    before issuing a filtered traversal.
    """
    return _json(
        {
            "nodes": [
                {
                    "label": n.label,
                    "key": n.key,
                    "track": n.track.value,
                    "properties": list(n.properties),
                }
                for n in NODES
            ],
            "edges": [
                {
                    "type": e.type,
                    "from": e.source,
                    "to": e.target,
                    "track": e.track.value,
                    "properties": list(e.properties),
                    "traversable": e.traversable,
                }
                for e in EDGES
            ],
            "schema_problems": validate_schema(),
        }
    )


# ---------------------------------------------------------------------------
# 9. Tenant status
# ---------------------------------------------------------------------------


@mcp.tool
def graph_tenant_status() -> str:
    """Report the backend, the tenants present, and their node/edge counts."""
    settings = _settings()
    store = _store()
    graphs = store.list_graphs()
    tenants = {}
    for name in graphs:
        nodes, edges = store.counts(name)
        tenants[name] = {"nodes": nodes, "edges": edges}
    return _json(
        {
            "backend": store.name,
            "production_graph": settings.prod_graph,
            "memory_graph": settings.memory_graph,
            "shadow_prefix": settings.shadow_prefix,
            "tenants": tenants,
        }
    )


def build_server() -> FastMCP:
    return mcp


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ChronosGraph MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "http"],
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    settings = _settings()
    store = _store()
    print(
        f"[chronosgraph-mcp] backend={store.name} "
        f"prod={settings.prod_graph} memory={settings.memory_graph}",
        file=sys.stderr,
    )

    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
