"""Seed the FalkorDB tenants with the demo enterprise.

Idempotent: every write is a MERGE, so running it twice produces the same graph
rather than duplicating nodes. ``--reset`` drops the tenants first.
"""

from __future__ import annotations

import argparse
import sys

from . import seed_data as sd
from .config import Settings, get_settings
from .graph import open_store
from .graph.ontology import NODE_BY_LABEL
from .graph.store import GraphStore
from .memory.memory import MemoryStore

#: Labels indexed in the production tenant. These are the entities the swarm
#: looks up by id during a traversal or an investigation.
PROD_INDEXED_LABELS: tuple[str, ...] = (
    "Service",
    "ASTModule",
    "APISchema",
    "SchemaField",
    "InfraResource",
    "Alert",
    "Person",
    "Team",
    "PullRequest",
    "Commit",
    "ADR",
    "DiscussionThread",
    "Ticket",
)

#: Labels indexed in the memory tenant. Memory recall matches on these.
MEMORY_INDEXED_LABELS: tuple[str, ...] = (
    "EpisodicIncident",
    "ProceduralPlaybook",
    "SemanticRule",
    "AgentSession",
    "ReasoningStep",
)


def seed_production(store: GraphStore, graph: str) -> dict[str, int]:
    """Write the Track 01 + Track 03 company brain."""
    counts: dict[str, int] = {}

    def bump(key: str, n: int) -> None:
        counts[key] = counts.get(key, 0) + n

    bump("Service", store.merge_nodes(graph, "Service", sd.SERVICES))
    bump("ASTModule", store.merge_nodes(graph, "ASTModule", sd.MODULES))
    bump("APISchema", store.merge_nodes(graph, "APISchema", sd.SCHEMAS))
    bump(
        "SchemaField",
        store.merge_nodes(
            graph,
            "SchemaField",
            [
                {
                    "id": f"{schema}.{name}",
                    "name": name,
                    "type": field_type,
                    "nullable": nullable,
                    "breaking": breaking,
                    "owner_module": owner,
                    "tenant_id": "acme",
                }
                for schema, name, field_type, nullable, breaking, owner in sd.SCHEMA_FIELDS
            ],
        ),
    )
    bump("InfraResource", store.merge_nodes(graph, "InfraResource", sd.INFRA))
    bump("Alert", store.merge_nodes(graph, "Alert", sd.ALERTS))
    bump("Team", store.merge_nodes(graph, "Team", sd.TEAMS))
    bump("Person", store.merge_nodes(graph, "Person", sd.PEOPLE))
    bump("PullRequest", store.merge_nodes(graph, "PullRequest", sd.PRS))
    bump("Commit", store.merge_nodes(graph, "Commit", sd.COMMITS))
    bump("Ticket", store.merge_nodes(graph, "Ticket", sd.TICKETS))
    bump("DiscussionThread", store.merge_nodes(graph, "DiscussionThread", sd.THREADS))
    bump("ADR", store.merge_nodes(graph, "ADR", sd.ADRS))
    bump(
        "SemanticRule",
        store.merge_nodes(graph, "SemanticRule", sd.SEMANTIC_RULES),
    )

    # ---- Track 01 edges ----------------------------------------------------
    bump(
        "CALLS",
        store.merge_edges(
            graph,
            "CALLS",
            [
                {
                    "source": src,
                    "target": dst,
                    "properties": {
                        "latency_p99": latency,
                        "weight": weight,
                        "protocol": protocol,
                    },
                }
                for src, dst, latency, weight, protocol in sd.CALLS
            ],
        ),
    )
    bump(
        "IMPLEMENTED_BY",
        store.merge_edges(
            graph,
            "IMPLEMENTED_BY",
            [{"source": s, "target": m} for s, m in sd.IMPLEMENTED_BY],
        ),
    )
    bump(
        "IMPORTS",
        store.merge_edges(
            graph,
            "IMPORTS",
            [
                {"source": a, "target": b, "properties": {"imported_symbol": symbol}}
                for a, b, symbol in sd.IMPORTS
            ],
        ),
    )
    bump(
        "EXPOSES",
        store.merge_edges(
            graph, "EXPOSES", [{"source": s, "target": a} for s, a in sd.EXPOSES]
        ),
    )
    bump(
        "HAS_FIELD",
        store.merge_edges(
            graph,
            "HAS_FIELD",
            [
                {"source": schema, "target": f"{schema}.{name}"}
                for schema, name, *_rest in sd.SCHEMA_FIELDS
            ],
        ),
    )
    bump(
        "DEPLOYED_ON",
        store.merge_edges(
            graph,
            "DEPLOYED_ON",
            [{"source": s, "target": i} for s, i in sd.DEPLOYED_ON],
        ),
    )
    bump(
        "TRIGGERED_ON",
        store.merge_edges(
            graph,
            "TRIGGERED_ON",
            [{"source": a, "target": s} for a, s in sd.TRIGGERED_ON],
        ),
    )
    bump(
        "MODIFIES",
        store.merge_edges(
            graph,
            "MODIFIES",
            [
                {
                    "source": pr,
                    "target": module,
                    "properties": {
                        "lines_changed": lines,
                        "breaking_risk": risk,
                    },
                }
                for pr, module, lines, risk in sd.MODIFIES
            ],
        ),
    )

    # ---- Track 03 edges ----------------------------------------------------
    bump(
        "MEMBER_OF",
        store.merge_edges(
            graph, "MEMBER_OF", [{"source": p, "target": t} for p, t in sd.MEMBER_OF]
        ),
    )
    bump(
        "OWNS",
        store.merge_edges(
            graph, "OWNS", [{"source": t, "target": s} for t, s in sd.OWNS]
        ),
    )
    bump(
        "AUTHORED",
        store.merge_edges(
            graph,
            "AUTHORED",
            [{"source": p, "target": pr} for p, pr in sd.AUTHORED_PERSON_PR],
        )
        + store.merge_edges(
            graph,
            "AUTHORED",
            [{"source": p, "target": a} for p, a in sd.AUTHORED_PERSON_ADR],
            target_label="ADR",
        ),
    )
    bump(
        "REVIEWED",
        store.merge_edges(
            graph,
            "REVIEWED",
            [
                {
                    "source": p,
                    "target": pr,
                    "properties": {"sentiment": sentiment, "approved": approved},
                }
                for p, pr, sentiment, approved in sd.REVIEWED
            ],
        ),
    )
    bump(
        "COMMITTED",
        store.merge_edges(
            graph, "COMMITTED", [{"source": p, "target": c} for p, c in sd.COMMITTED]
        ),
    )
    bump(
        "INCLUDED_IN",
        store.merge_edges(
            graph,
            "INCLUDED_IN",
            [{"source": c, "target": pr} for c, pr in sd.INCLUDED_IN],
        ),
    )
    bump(
        "IMPLEMENTS",
        store.merge_edges(
            graph,
            "IMPLEMENTS",
            [{"source": pr, "target": t} for pr, t in sd.IMPLEMENTS],
        ),
    )
    bump(
        "DISCUSSED_IN",
        store.merge_edges(
            graph,
            "DISCUSSED_IN",
            [{"source": t, "target": th} for t, th in sd.DISCUSSED_IN],
        ),
    )
    bump(
        "RESULTED_IN_DECISION",
        store.merge_edges(
            graph,
            "RESULTED_IN_DECISION",
            [{"source": th, "target": a} for th, a in sd.RESULTED_IN_DECISION],
        ),
    )
    bump(
        "GOVERNS",
        store.merge_edges(
            graph, "GOVERNS", [{"source": a, "target": s} for a, s in sd.GOVERNS]
        ),
    )
    bump(
        "CONSTRAINS",
        store.merge_edges(
            graph, "CONSTRAINS", [{"source": a, "target": s} for a, s in sd.CONSTRAINS]
        ),
    )
    return counts


def seed_memory(store: GraphStore, memory_graph: str, prod_graph: str) -> dict[str, int]:
    """Write the Track 02 episodic/procedural memory tenant.

    Signatures are *computed by traversing the freshly-seeded production graph*
    with :func:`incident_signature` rather than being written by hand. That is
    what makes episodic recall work: a live alert and a seeded episode produce
    comparable fingerprints because they are produced by the same code path.
    """
    from .graph.investigate import incident_signature

    memory = MemoryStore(store, memory_graph, prod_graph)
    counts: dict[str, int] = {}

    signatures: dict[str, str] = {}
    for episode in sd.EPISODES:
        signature = incident_signature(
            store,
            prod_graph,
            service_ids=[episode["symptom"]],
            max_hops=3,
        )
        signatures[episode["id"]] = signature
        memory.store_episode(
            incident_id=episode["id"],
            summary=episode["summary"],
            symptom_signature=signature,
            affected_service_ids=episode["affected"],
            severity=episode["severity"],
            mttr_seconds=episode["mttr_seconds"],
            extra={"occurred_at": episode["occurred_at"]},
        )
    counts["EpisodicIncident"] = len(sd.EPISODES)

    for playbook in sd.PLAYBOOKS:
        # A playbook's trigger signature is the signature of the incident it was
        # learned from - that is the whole point of procedural memory.
        signature = signatures.get(playbook["derived_from"], "")
        memory.store_playbook(
            playbook_id=playbook["id"],
            name=playbook["name"],
            trigger_signature=signature,
            action_steps=playbook["steps"],
            success_rate=playbook["success_rate"],
            uses=playbook["uses"],
            derived_from_incident=playbook["derived_from"],
        )
    counts["ProceduralPlaybook"] = len(sd.PLAYBOOKS)

    # Semantic memory: the invariants that hold regardless of which incident is
    # being triaged. These are the third tier - episodic recalls what happened,
    # procedural recalls what to do, semantic recalls what must stay true.
    for rule in sd.SEMANTIC_RULES:
        memory.store_rule(
            rule_id=rule["id"],
            statement=rule["invariant_statement"],
            domain=rule["domain"],
            enforcement_level=rule["enforcement_level"],
            source=rule.get("source", "seeded architecture review"),
        )
    counts["SemanticRule"] = len(sd.SEMANTIC_RULES)
    return counts


def ensure_indexes(store: GraphStore, settings: Settings) -> dict[str, int]:
    """Index the lookup keys the swarm actually filters on.

    Every traversal, node fetch and memory recall matches on a node's key
    property (``Service.id``, ``Alert.id``, ...). Without an index FalkorDB
    scans the label, which is invisible at demo scale but dominates once the
    graph is real. Safe to call repeatedly: a duplicate index is not an error.
    """
    created = 0
    for graph, labels in (
        (settings.prod_graph, PROD_INDEXED_LABELS),
        (settings.memory_graph, MEMORY_INDEXED_LABELS),
    ):
        for label in labels:
            spec = NODE_BY_LABEL.get(label)
            if spec is None:
                continue
            if store.create_index(graph, label, spec.key):
                created += 1
    return {"indexes_created": created}


def seed_all(
    store: GraphStore, settings: Settings, reset: bool = False
) -> dict[str, dict[str, int]]:
    if reset:
        store.drop_graph(settings.prod_graph)
        store.drop_graph(settings.memory_graph)
    production = seed_production(store, settings.prod_graph)
    memory = seed_memory(store, settings.memory_graph, settings.prod_graph)
    # Indexes are created after the data so the seed does not pay for
    # maintaining them on every insert.
    indexes = ensure_indexes(store, settings)
    return {"production": production, "memory": memory, **indexes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Knot tenants.")
    parser.add_argument("--reset", action="store_true", help="drop tenants first")
    parser.add_argument(
        "--memory-backend",
        action="store_true",
        help="use the in-process backend instead of FalkorDB",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.memory_backend:
        object.__setattr__(settings, "force_memory_backend", True)

    store = open_store(settings)
    print(f"[seed] backend = {store.name}")
    result = seed_all(store, settings, reset=args.reset)

    for tenant, counts in result.items():
        if not isinstance(counts, dict):
            print(f"[seed] {tenant}: {counts}")
            continue
        print(f"[seed] {tenant}:")
        for label, count in sorted(counts.items()):
            print(f"         {label:<20} {count}")
    for tenant in (settings.prod_graph, settings.memory_graph):
        nodes, edges = store.counts(tenant)
        print(f"[seed] {tenant}: {nodes} nodes / {edges} edges")
    return 0


if __name__ == "__main__":
    sys.exit(main())
