"""Track 01 conformance: the swarm must reach the graph *through MCP*.

The README claims the agents call graph traversals and topology algorithms as
MCP tools. That claim is about the execution path, so it is testable: if an
agent imports ``src.graph.algorithms`` and calls it directly, the claim is
false no matter how good the MCP server looks on its own.

These tests pin the two halves of that contract:

1. the swarm module does not import the graph or action layers at all; and
2. a live run records the MCP tool calls that produced its decisions, and the
   server reports those same tools to any MCP client.

A change that quietly reintroduces a direct call fails here rather than passing
silently while the submission's central claim rots.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from fastmcp import Client

from src.agents import SwarmOrchestrator
from src.agents.swarm import SwarmContext
from src.actions.diff_parser import build_mutation_spec
from src.mcp_server.client import TOOLS, GraphToolClient
from src.mcp_server.server import bind, mcp
from src.seed_data import PR_812_DIFF


def _mutation(store, settings, pr_id="PR-812"):
    modules = store.find_nodes(settings.prod_graph, "ASTModule", limit=500)
    fields = store.find_nodes(settings.prod_graph, "SchemaField", limit=500)
    mutation, _ = build_mutation_spec(
        PR_812_DIFF, pr_id, modules, fields, latency_delta_ms=180
    )
    return mutation


SWARM_SOURCE = Path(__file__).resolve().parents[1] / "src" / "agents" / "swarm.py"

#: Modules the swarm must not reach into directly. The agents' only handle on
#: the graph is the MCP client.
FORBIDDEN_IMPORTS = (
    "src.graph",
    "src.actions.shadow_sim",
    "src.actions.expertise",
    "src.memory.memory",
)


def _imported_modules(source: str) -> set[str]:
    """Every module a source file imports at *runtime*, normalised to absolute.

    Imports nested under ``if TYPE_CHECKING:`` are excluded: they exist only for
    annotations, and ``from __future__ import annotations`` means they never run.
    A name that is only ever used in a type hint cannot bypass anything.

    Relative imports are resolved against this file's own package: the swarm
    lives at ``src/agents/swarm.py``, so ``from ..graph.algorithms import x``
    (level 2) becomes ``src.graph.algorithms``.
    """
    tree = ast.parse(source)

    type_checking_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            guard = (
                test.id if isinstance(test, ast.Name)
                else test.attr if isinstance(test, ast.Attribute)
                else ""
            )
            if guard == "TYPE_CHECKING":
                for child in node.body:
                    for sub in ast.walk(child):
                        if hasattr(sub, "lineno"):
                            type_checking_lines.add(sub.lineno)

    found: set[str] = set()
    for node in ast.walk(tree):
        if getattr(node, "lineno", -1) in type_checking_lines:
            continue
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                # Drop the leaf package (src.agents) and climb `level - 1`.
                parts = ["src", "agents"]
                parts = parts[: len(parts) - (node.level - 1)]
                found.add(".".join(parts + ([base] if base else [])))
            else:
                found.add(base)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
    return found


def test_swarm_does_not_import_the_graph_layer_directly():
    """No agent module may bypass MCP by importing the graph layer."""
    imported = _imported_modules(SWARM_SOURCE.read_text())
    offending = sorted(
        name
        for name in imported
        for forbidden in FORBIDDEN_IMPORTS
        if name == forbidden or name.startswith(forbidden + ".")
    )
    assert not offending, (
        "src/agents/swarm.py imports the graph/action layer directly, which "
        f"bypasses MCP: {offending}"
    )


def test_swarm_imports_the_mcp_client():
    """The swarm's graph access must come from the MCP client module."""
    imported = _imported_modules(SWARM_SOURCE.read_text())
    assert "src.mcp_server.client" in imported, (
        "swarm.py does not import GraphToolClient, so it cannot be calling "
        "graph operations over MCP"
    )


def test_swarm_context_exposes_no_direct_store_handle():
    """A session context must not carry the graph store.

    Import discipline alone is not enough: an agent could be handed the store
    through its context and call it without importing anything. Keeping the
    store off ``SwarmContext`` removes that route entirely.
    """
    source = SWARM_SOURCE.read_text()
    context = source.split("class SwarmContext", 1)[1].split("\nclass ", 1)[0]
    assert "self.store = store" not in context, (
        "SwarmContext keeps a store handle, which lets an agent bypass MCP"
    )
    assert "self.tools = " in context


def test_mcp_server_exposes_the_tools_the_swarm_calls():
    """Every tool the client is allowed to call must exist on the server."""
    async def _names() -> set[str]:
        async with Client(mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    exposed = asyncio.run(_names())
    missing = sorted(set(TOOLS) - exposed)
    assert not missing, f"MCP server does not expose: {missing}"


def test_agent_decisions_carry_mcp_tool_calls(seeded, shadow_graphs_clean):
    """A live run must record the MCP tools that produced each decision.

    Two independent checks: the tools named in the reasoning trace must be real
    registered MCP tools (not free-text labels), and the client must actually
    have invoked them over the protocol during the run.
    """
    store, settings = seeded
    orchestrator = SwarmOrchestrator(settings, store)
    result = orchestrator.run_future_simulation(
        "PR-812",
        _mutation(store, settings),
        "SESS-mcp-routing",
    )

    recorded = {
        tool
        for step in result["steps"]
        for tool in step["tool"].replace(" + ", "|").split("|")
        if tool
    }
    assert recorded, "no tools were recorded in the reasoning trace"
    unknown = sorted(recorded - set(TOOLS))
    assert not unknown, (
        f"trace names tools that are not registered MCP tools: {unknown}"
    )

    # The client's own call log is the ground truth: these tools really were
    # invoked, in this process, over the MCP protocol.
    invoked = [tool for tool, _ in orchestrator.tools.calls]
    assert invoked, "the tool client recorded no MCP calls"
    assert set(invoked) <= set(TOOLS)
    assert "fork_and_simulate_shadow_graph" in invoked
    # Track 01 is about acting on connected data: the run must traverse, not
    # only simulate.
    assert any(
        tool in invoked
        for tool in ("graph_multi_hop_investigate", "resolve_dependents")
    ), f"no traversal tool was invoked: {invoked}"


def test_tool_client_records_every_call(seeded):
    """The call log is complete, which is what makes the audit trail trustworthy."""
    store, settings = seeded
    client = GraphToolClient(settings, store)
    assert client.calls == []

    client.call("graph_tenant_status", {})
    client.run_graph_topology_algorithm("node_degree")
    assert [tool for tool, _ in client.calls] == [
        "graph_tenant_status",
        "run_graph_topology_algorithm",
    ]


def test_mcp_client_returns_real_graph_results(seeded):
    """The client is not a stub: its calls reach FalkorDB and come back with data."""
    store, settings = seeded
    client = GraphToolClient(settings, store)

    status = client.call("graph_tenant_status", {})
    assert status["backend"] == "falkordb"
    assert status["production_graph"] == settings.prod_graph

    centrality = client.run_graph_topology_algorithm("betweenness_centrality")
    assert centrality["scores"], "betweenness centrality returned no scores"

    catalog = client.graph_node_catalog()
    assert catalog["count"] > 0
    assert any(node["id"] == "SVC-AUTH" for node in catalog["nodes"])


def test_swarm_context_binds_the_session_tenant(seeded):
    """A session's tool client must target that session's tenants.

    Tests run against per-test tenant names, so a client that fell back to the
    ambient settings would read the demo graph and pass by accident. This pins
    the binding.
    """
    store, settings = seeded
    ctx = SwarmContext(settings, store, "SESS-tenant-binding")
    assert ctx.tools.settings.prod_graph == settings.prod_graph
    assert ctx.tools.settings.memory_graph == settings.memory_graph

    bind(settings, store)
    status = ctx.tools.call("graph_tenant_status", {})
    assert status["production_graph"] == settings.prod_graph
