"""In-process MCP client - the swarm's execution path for graph tools.

Track 01 asks for an agent that calls graph traversals and graph algorithms *as
tools over the Model Context Protocol*, not one that imports those functions and
happens to also ship an MCP server. Those are different claims, and only the
first one is testable.

So the swarm does not import :mod:`src.graph` or :mod:`src.actions` to do its
work. It builds a :class:`GraphToolClient` and calls the same MCP tools an
external client (Claude Desktop, Cursor, another swarm) would call. There is one
execution path, and it is the protocol one.

The client is synchronous because the agents are synchronous reasoners. Each call
opens a client session, invokes the tool, and returns the parsed JSON payload.
Every tenant name and threshold is passed explicitly rather than read from
module state, so two sessions over different tenants cannot interfere.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any, Sequence

from fastmcp import Client

from ..config import Settings
from ..graph.store import GraphStore

#: Tools the swarm is allowed to reach. Kept explicit so a typo fails loudly
#: here rather than as an opaque protocol error at runtime.
TOOLS: tuple[str, ...] = (
    "graph_multi_hop_investigate",
    "graph_node_catalog",
    "resolve_dependents",
    "run_graph_topology_algorithm",
    "fork_and_simulate_shadow_graph",
    "memory_read_write_coordinator",
    "trace_company_decision_provenance",
    "triage_live_incident",
    "execute_verified_remediation",
    "describe_graph_schema",
    "graph_tenant_status",
)


class MCPToolError(RuntimeError):
    """Raised when an MCP tool call fails or returns an unusable payload."""


def _run_sync(coro):
    """Run a coroutine from synchronous code, even inside a running loop.

    The API streams scenarios from a worker thread and the CLI calls from the
    main thread, so normally there is no loop and ``asyncio.run`` is correct.
    If a caller ever invokes the swarm from inside a loop, that would raise, so
    the coroutine is handed to a fresh thread instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class GraphToolClient:
    """Synchronous facade over the Knot MCP server."""

    def __init__(self, settings: Settings, store: GraphStore) -> None:
        self.settings = settings
        self.store = store
        #: Every (tool, arguments) pair this client has invoked, in order. This
        #: is the audit trail that makes "the agent acted through MCP" a
        #: verifiable statement rather than an architectural claim.
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Imported here rather than at module scope: the server imports this
        # package's siblings, and a top-level import would be circular.
        from .server import bind, mcp

        bind(settings, store)
        self._server = mcp

    # -- protocol plumbing ---------------------------------------------------

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict:
        """Invoke one MCP tool and return its JSON payload as a dict."""
        if tool not in TOOLS:
            raise MCPToolError(f"unknown tool {tool!r}")
        payload = arguments or {}
        self.calls.append((tool, payload))

        async def _invoke() -> dict:
            async with Client(self._server) as client:
                result = await client.call_tool(tool, payload)
            data = result.data
            if isinstance(data, str):
                return json.loads(data)
            if isinstance(data, dict):
                return data
            raise MCPToolError(f"{tool} returned {type(data).__name__}, expected JSON")

        try:
            parsed = _run_sync(_invoke())
        except MCPToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalised for the trace
            raise MCPToolError(f"{tool} failed: {exc}") from exc
        if isinstance(parsed, dict) and "error" in parsed and "refused" not in parsed:
            raise MCPToolError(f"{tool}: {parsed['error']}")
        return parsed

    def last_cypher(self) -> str:
        """The last Cypher the store executed, for proof certificates.

        This reads the shared connection rather than a tool result: the tools
        already return the Cypher they ran, but several call sites need it for
        a certificate assembled from more than one call.
        """
        return self.store.last_cypher()

    # -- Track 01: traversal and algorithms ----------------------------------

    def graph_multi_hop_investigate(
        self,
        start_node_id: str,
        max_hops: int = 4,
        relationship_filter: Sequence[str] | None = None,
        direction: str = "out",
        tenant_graph: str = "",
    ) -> dict:
        return self.call(
            "graph_multi_hop_investigate",
            {
                "start_node_id": start_node_id,
                "max_hops": max_hops,
                "relationship_filter": list(relationship_filter or []),
                "direction": direction,
                "tenant_graph": tenant_graph or self.settings.prod_graph,
            },
        )

    def graph_node_catalog(
        self,
        labels: Sequence[str] | None = None,
        limit: int = 8000,
        tenant_graph: str = "",
    ) -> dict:
        return self.call(
            "graph_node_catalog",
            {
                "labels": list(labels or []),
                "limit": limit,
                "tenant_graph": tenant_graph or self.settings.prod_graph,
            },
        )

    def resolve_dependents(
        self,
        node_ids: Sequence[str],
        relationship_type: str = "IMPLEMENTED_BY",
        tenant_graph: str = "",
    ) -> dict:
        return self.call(
            "resolve_dependents",
            {
                "node_ids": list(node_ids),
                "relationship_type": relationship_type,
                "tenant_graph": tenant_graph or self.settings.prod_graph,
            },
        )

    def run_graph_topology_algorithm(
        self,
        algorithm: str,
        source_id: str = "",
        target_id: str = "",
        subgraph_label: str = "Service",
        edge_types: Sequence[str] | None = None,
        tenant_graph: str = "",
    ) -> dict:
        return self.call(
            "run_graph_topology_algorithm",
            {
                "algorithm": algorithm,
                "source_id": source_id,
                "target_id": target_id,
                "subgraph_label": subgraph_label,
                "edge_types": list(edge_types or []),
                "tenant_graph": tenant_graph or self.settings.prod_graph,
            },
        )

    # -- Track 01 + 03: counterfactual simulation ----------------------------

    def fork_and_simulate_shadow_graph(
        self,
        pr_number: str,
        modified_modules: Sequence[str] | None = None,
        diff_text: str = "",
        breaking_schema_changes: bool = False,
        simulated_latency_delta_ms: float = 0.0,
        removed_schema_field_ids: Sequence[str] | None = None,
        added_schema_field_ids: Sequence[str] | None = None,
        added_call_edges: Sequence[Sequence[str]] | None = None,
        removed_call_edges: Sequence[Sequence[str]] | None = None,
        added_import_edges: Sequence[Sequence[str]] | None = None,
        max_hops: int = 5,
        keep_shadow: bool = False,
    ) -> dict:
        return self.call(
            "fork_and_simulate_shadow_graph",
            {
                "pr_number": pr_number,
                "modified_modules": list(modified_modules or []),
                "diff_text": diff_text,
                "breaking_schema_changes": breaking_schema_changes,
                "simulated_latency_delta_ms": simulated_latency_delta_ms,
                "removed_schema_field_ids": list(removed_schema_field_ids or []),
                "added_schema_field_ids": list(added_schema_field_ids or []),
                "added_call_edges": [list(e) for e in (added_call_edges or [])],
                "removed_call_edges": [list(e) for e in (removed_call_edges or [])],
                "added_import_edges": [list(e) for e in (added_import_edges or [])],
                "max_hops": max_hops,
                "keep_shadow": keep_shadow,
            },
        )

    # -- Track 02: memory ----------------------------------------------------

    def memory_read_write_coordinator(
        self,
        operation: str,
        session_id: str,
        agent_id: str,
        symptom_signature: str = "",
        payload: dict | None = None,
        memory_graph: str = "",
        prod_graph: str = "",
    ) -> dict:
        return self.call(
            "memory_read_write_coordinator",
            {
                "operation": operation,
                "session_id": session_id,
                "agent_id": agent_id,
                "symptom_signature": symptom_signature,
                "payload": payload or {},
                "memory_graph": memory_graph or self.settings.memory_graph,
                "prod_graph": prod_graph or self.settings.prod_graph,
            },
        )

    # -- Track 03: provenance and expertise ----------------------------------

    def trace_company_decision_provenance(
        self, entity_id: str, include_expert_ranking: bool = True, tenant_graph: str = ""
    ) -> dict:
        return self.call(
            "trace_company_decision_provenance",
            {
                "entity_id": entity_id,
                "include_expert_ranking": include_expert_ranking,
                "tenant_graph": tenant_graph or self.settings.prod_graph,
            },
        )

    # -- Track 01 + 02: triage ----------------------------------------------

    def triage_live_incident(
        self,
        alert_id: str,
        window_hours: int = 2,
        max_hops: int = 4,
        tenant_graph: str = "",
        memory_graph: str = "",
    ) -> dict:
        return self.call(
            "triage_live_incident",
            {
                "alert_id": alert_id,
                "window_hours": window_hours,
                "max_hops": max_hops,
                "tenant_graph": tenant_graph or self.settings.prod_graph,
                "memory_graph": memory_graph or self.settings.memory_graph,
            },
        )

    # -- Track 01: gated action ---------------------------------------------

    def execute_verified_remediation(
        self,
        action_type: str,
        target_id: str,
        proof_path_ids: Sequence[str],
        remediation_note: str,
        claim: str = "",
        confidence: float = 0.0,
        certificate_id: str = "",
        graph: str = "",
        expert_handle: str = "",
        team: str = "",
        stale_doc_ids: Sequence[str] | None = None,
        patch_summary: str = "",
    ) -> dict:
        return self.call(
            "execute_verified_remediation",
            {
                "action_type": action_type,
                "target_id": target_id,
                "proof_path_ids": list(proof_path_ids),
                "remediation_note": remediation_note,
                "claim": claim,
                "confidence": confidence,
                "certificate_id": certificate_id,
                "graph": graph or self.settings.prod_graph,
                "expert_handle": expert_handle,
                "team": team,
                "stale_doc_ids": list(stale_doc_ids or []),
                "patch_summary": patch_summary,
                "dry_run": self.settings.actions_dry_run,
            },
        )
