"""FastAPI swarm coordinator.

Responsibilities:

* serve the frontend with graph snapshots (nodes + edges, coloured by track)
* run the three scenarios, either synchronously or as a Server-Sent Events
  stream so the 3D UI can light up nodes as the swarm touches them
* expose the topology algorithms and the memory contents for the audit panel

The service is deliberately stateless between requests: all state lives in
FalkorDB, so restarting the API never loses a session's reasoning trace.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..actions.diff_parser import build_mutation_spec
from ..actions.expertise import ExpertiseRanker
from ..actions.shadow_sim import MutationSpec, ShadowSimulator
from ..agents.swarm import SwarmOrchestrator
from ..config import get_settings
from ..graph.algorithms import run_algorithm
from ..graph.investigate import (
    multi_hop_investigate,
    trace_decision_provenance,
    triage_incident,
)
from ..graph.ontology import Track, validate_schema
from ..graph.store import open_store
from ..memory.memory import MemoryStore
from ..seed import seed_all
from ..seed_data import PR_402_DIFF, PR_812_DIFF

settings = get_settings()
store = open_store(settings)
memory = MemoryStore(store, settings.memory_graph, settings.prod_graph)
orchestrator = SwarmOrchestrator(settings, store)

app = FastAPI(
    title="Knot Swarm Coordinator",
    version="1.0.0",
    description=(
        "Temporal-predictive autonomous engineering swarm over FalkorDB. "
        "Past (decision provenance + episodic memory), Present (multi-hop "
        "triage + verified action), Future (counterfactual shadow-graph "
        "simulation)."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#: Colour per track, so the 3D view is consistent with the docs.
TRACK_COLORS = {
    Track.PRESENT.value: "#38bdf8",
    Track.MEMORY.value: "#a78bfa",
    Track.BRAIN.value: "#34d399",
}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class FutureSimRequest(BaseModel):
    pr_id: str = Field(default="PR-812")
    diff_text: str = ""
    modified_modules: list[str] = Field(default_factory=list)
    breaking_schema_changes: bool = False
    simulated_latency_delta_ms: float = 0.0
    max_hops: int = Field(default=5, ge=1, le=8)
    block_on_risk: bool = True


class IncidentRequest(BaseModel):
    alert_id: str = Field(default="ALT-904")


class ArchaeologyRequest(BaseModel):
    query: str = Field(
        default="Why does PaymentService bypass Redis on retry?"
    )


class AlgorithmRequest(BaseModel):
    algorithm: str = "betweenness_centrality"
    source_id: str = ""
    target_id: str = ""
    subgraph_label: str = "Service"
    edge_types: list[str] | None = None


# ---------------------------------------------------------------------------
# Graph views
# ---------------------------------------------------------------------------


def _track_of(label: str) -> str:
    from ..graph.ontology import NODE_BY_LABEL

    spec = NODE_BY_LABEL.get(label)
    return spec.track.value if spec else Track.PRESENT.value


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "backend": store.name,
        "graphs": store.list_graphs(),
        "schema_problems": validate_schema(),
    }


@app.get("/api/graph")
def graph_view(limit: int = Query(default=2000, le=8000)) -> dict:
    """Full graph snapshot in the shape the 3D force view expects."""
    snapshot = store.snapshot(settings.prod_graph, limit=limit)
    nodes = []
    for node in snapshot.nodes:
        track = _track_of(node.get("label", ""))
        nodes.append(
            {
                "id": node["id"],
                "label": node.get("label"),
                "name": node.get("name")
                or node.get("title")
                or node.get("file_path")
                or node.get("code")
                or node.get("key")
                or node["id"],
                "track": track,
                "color": TRACK_COLORS.get(track, "#94a3b8"),
                "tier": node.get("tier"),
                "severity": node.get("severity"),
                "status": node.get("status"),
                "properties": {
                    k: v
                    for k, v in node.items()
                    if k not in {"id", "label", "name", "tier", "severity", "status"}
                },
            }
        )
    edges = [
        {
            "source": edge["source"],
            "target": edge["target"],
            "type": edge["type"],
            "properties": edge.get("properties") or {},
            "traversable": True,
        }
        for edge in snapshot.edges
    ]
    prod_nodes, prod_edges = store.counts(settings.prod_graph)
    return {
        "graph": settings.prod_graph,
        "nodes": nodes,
        "edges": edges,
        "node_count": prod_nodes,
        "edge_count": prod_edges,
        "tenants": {
            name: dict(zip(("nodes", "edges"), store.counts(name)))
            for name in store.list_graphs()
        },
    }


@app.get("/api/graph/node/{node_id}")
def graph_node(node_id: str) -> dict:
    from ..graph.investigate import _find_any_node

    node = _find_any_node(store, settings.prod_graph, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"no node {node_id}")
    snapshot = store.snapshot(settings.prod_graph, limit=8000)
    neighbours = [
        {"source": e["source"], "target": e["target"], "type": e["type"]}
        for e in snapshot.edges
        if e["source"] == node_id or e["target"] == node_id
    ]
    return {"node": node, "edges": neighbours}


@app.get("/api/investigate")
def investigate(
    start_node_id: str,
    max_hops: int = Query(default=4, ge=1, le=8),
    direction: str = "out",
    relationship_filter: str = "",
) -> dict:
    rels = [r for r in relationship_filter.split(",") if r] or None
    result = multi_hop_investigate(
        store,
        settings.prod_graph,
        start_node_id,
        max_hops=max_hops,
        relationship_filter=rels,
        direction=direction,
    )
    return result.to_dict()


@app.get("/api/provenance/{entity_id}")
def provenance(entity_id: str) -> dict:
    result = trace_decision_provenance(store, settings.prod_graph, entity_id)
    ranker = ExpertiseRanker(store, settings.prod_graph)
    return {
        **result.to_dict(),
        "experts": [e.to_dict() for e in ranker.rank(entity_id, top_k=5)],
    }


@app.get("/api/triage/{alert_id}")
def triage(alert_id: str) -> dict:
    result = triage_incident(
        store,
        settings.prod_graph,
        alert_id,
        window_hours=settings.recent_change_window_hours,
    )
    episodes = memory.retrieve_similar_episodes(result.signature, top_k=3)
    playbook = memory.retrieve_playbook(result.signature)
    return {
        **result.to_dict(),
        "episodes": [e.to_dict() for e in episodes],
        "playbook_match": playbook.to_dict() if playbook else None,
    }


@app.post("/api/algorithm")
def algorithm(request: AlgorithmRequest) -> dict:
    kwargs: dict[str, Any] = {}
    if request.algorithm == "shortest_path":
        kwargs.update(
            {"source_id": request.source_id, "target_id": request.target_id}
        )
    elif request.algorithm == "blast_radius":
        kwargs.update({"origin_ids": [request.source_id]})
    else:
        kwargs["node_label"] = request.subgraph_label or None
    if request.edge_types:
        kwargs["edge_types"] = request.edge_types
    try:
        return run_algorithm(
            request.algorithm, store, settings.prod_graph, **kwargs
        ).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Memory / audit views
# ---------------------------------------------------------------------------


@app.get("/api/memory")
def memory_view() -> dict:
    return {
        "graph": settings.memory_graph,
        "episodes": memory.store.find_nodes(
            settings.memory_graph, "EpisodicIncident", limit=100
        ),
        "playbooks": memory.store.find_nodes(
            settings.memory_graph, "ProceduralPlaybook", limit=100
        ),
        "rules": memory.store.find_nodes(
            settings.memory_graph, "SemanticRule", limit=100
        ),
        "sessions": memory.store.find_nodes(
            settings.memory_graph, "AgentSession", limit=100
        ),
    }


@app.get("/api/memory/session/{session_id}")
def session_trace(session_id: str) -> dict:
    steps = memory.session_trace(session_id)
    return {"session_id": session_id, "steps": steps, "step_count": len(steps)}


# ---------------------------------------------------------------------------
# Scenario endpoints
# ---------------------------------------------------------------------------


def _mutation_for(request: FutureSimRequest) -> MutationSpec:
    if request.diff_text:
        modules = store.find_nodes(settings.prod_graph, "ASTModule", limit=500)
        fields = store.find_nodes(settings.prod_graph, "SchemaField", limit=500)
        mutation, _ = build_mutation_spec(
            request.diff_text,
            request.pr_id,
            modules,
            fields,
            declare_breaking=request.breaking_schema_changes,
            latency_delta_ms=request.simulated_latency_delta_ms,
        )
        return mutation
    return MutationSpec(
        modified_module_ids=list(request.modified_modules),
        breaking_schema_change=request.breaking_schema_changes,
        latency_delta_ms=request.simulated_latency_delta_ms,
    )


@app.post("/api/scenario/future_simulation")
def scenario_future_simulation(request: FutureSimRequest) -> dict:
    session_id = f"SESS-{uuid.uuid4().hex[:8]}"
    mutation = _mutation_for(request)
    return orchestrator.run_future_simulation(
        request.pr_id,
        mutation,
        session_id,
        max_hops=request.max_hops,
        block_on_risk=request.block_on_risk,
    )


@app.post("/api/scenario/live_incident")
def scenario_live_incident(request: IncidentRequest) -> dict:
    session_id = f"SESS-{uuid.uuid4().hex[:8]}"
    return orchestrator.run_live_incident(request.alert_id, session_id)


@app.post("/api/scenario/archaeology")
def scenario_archaeology(request: ArchaeologyRequest) -> dict:
    session_id = f"SESS-{uuid.uuid4().hex[:8]}"
    return orchestrator.run_archaeology(request.query, session_id)


# ---------------------------------------------------------------------------
# Streaming variants (SSE)
# ---------------------------------------------------------------------------


def _sse_stream(runner) -> StreamingResponse:
    """Run ``runner(emit)`` on a worker thread and stream its events.

    A thread plus a queue is used rather than an async task because the swarm is
    synchronous and CPU/graph bound; running it on the event loop would block
    every other client for the duration of a simulation.
    """

    def generate():
        events: queue.Queue = queue.Queue()
        sentinel = object()

        def emit(event: dict) -> None:
            events.put(event)

        def work() -> None:
            try:
                result = runner(emit)
                events.put({"kind": "result", "result": result})
            except Exception as exc:  # noqa: BLE001 - surfaced to the client
                events.put({"kind": "error", "error": str(exc)})
            finally:
                events.put(sentinel)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

        while True:
            item = events.get()
            if item is sentinel:
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"
        yield "data: {\"kind\": \"done\"}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/stream/future_simulation")
def stream_future_simulation(request: FutureSimRequest) -> StreamingResponse:
    session_id = f"SESS-{uuid.uuid4().hex[:8]}"
    mutation = _mutation_for(request)
    return _sse_stream(
        lambda emit: orchestrator.run_future_simulation(
            request.pr_id,
            mutation,
            session_id,
            max_hops=request.max_hops,
            block_on_risk=request.block_on_risk,
            on_event=emit,
        )
    )


@app.post("/api/stream/live_incident")
def stream_live_incident(request: IncidentRequest) -> StreamingResponse:
    session_id = f"SESS-{uuid.uuid4().hex[:8]}"
    return _sse_stream(
        lambda emit: orchestrator.run_live_incident(
            request.alert_id, session_id, on_event=emit
        )
    )


@app.post("/api/stream/archaeology")
def stream_archaeology(request: ArchaeologyRequest) -> StreamingResponse:
    session_id = f"SESS-{uuid.uuid4().hex[:8]}"
    return _sse_stream(
        lambda emit: orchestrator.run_archaeology(
            request.query, session_id, on_event=emit
        )
    )


# ---------------------------------------------------------------------------
# Demo fixtures and admin
# ---------------------------------------------------------------------------


@app.get("/api/demo/fixtures")
def demo_fixtures() -> dict:
    """The canned PR diffs the UI buttons use, so the demo is reproducible."""
    return {
        "PR-812": {
            "title": "feat(auth): slim down AuthPayload",
            "diff": PR_812_DIFF,
            "breaking_schema_changes": True,
            "simulated_latency_delta_ms": 180.0,
        },
        "PR-402": {
            "title": "perf(payment): bypass redis on retry",
            "diff": PR_402_DIFF,
            "breaking_schema_changes": False,
            "simulated_latency_delta_ms": 40.0,
        },
    }


@app.post("/api/admin/seed")
def admin_seed(reset: bool = True) -> dict:
    result = seed_all(store, settings, reset=reset)
    return {"seeded": result, "backend": store.name}


@app.get("/api/admin/shadow-graphs")
def shadow_graphs() -> dict:
    """List ephemeral shadow tenants. Should normally be empty."""
    shadows = [
        name
        for name in store.list_graphs()
        if name.startswith(settings.shadow_prefix)
    ]
    return {
        "shadow_prefix": settings.shadow_prefix,
        "active_shadow_graphs": shadows,
        "count": len(shadows),
    }


@app.post("/api/admin/drop-shadows")
def drop_shadows() -> dict:
    shadows = [
        name
        for name in store.list_graphs()
        if name.startswith(settings.shadow_prefix)
    ]
    for name in shadows:
        store.drop_graph(name)
    return {"dropped": shadows}
