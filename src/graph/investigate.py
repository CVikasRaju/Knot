"""Investigation primitives shared by the swarm.

Three questions, three traversals:

* ``multi_hop_investigate`` - "what is connected to this, and how?"
* ``trace_decision_provenance`` - "why does this exist?" (walk backwards in time)
* ``triage_incident`` - "what changed recently that could explain this alert?"

They live together because they share the same hop-rendering and scoring code,
and because the archaeologist and triage agents genuinely need all three.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .algorithms import blast_radius, shortest_dependency_path
from .ontology import CAUSAL_CHAIN_RELATIONSHIPS, TRAVERSABLE_RELATIONSHIPS
from .store import GraphStore, PathRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class InvestigationResult:
    start_node_id: str
    graph: str
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    hop_paths: list[dict] = field(default_factory=list)
    explainable_trace: list[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "start_node_id": self.start_node_id,
            "graph": self.graph,
            "nodes": self.nodes,
            "edges": self.edges,
            "hop_paths": self.hop_paths,
            "explainable_trace": self.explainable_trace,
            "truncated": self.truncated,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
        }


def _find_any_node(store: GraphStore, graph: str, node_id: str) -> dict | None:
    """Locate a node without knowing its label.

    FalkorDB has no cheap "match any label by id" that also returns the label in
    every build, so the ontology's label list is probed in order. The list is
    short and this keeps the API honest: an unknown id returns ``None`` instead
    of raising.
    """
    from .ontology import NODES

    for spec in NODES:
        found = store.get_node(graph, spec.label, node_id)
        if found:
            return found
    return None


def multi_hop_investigate(
    store: GraphStore,
    graph: str,
    start_node_id: str,
    max_hops: int = 4,
    relationship_filter: Sequence[str] | None = None,
    direction: str = "out",
    limit: int = 200,
) -> InvestigationResult:
    """Bounded traversal from any entity, rendered for a human."""
    rels = list(relationship_filter) if relationship_filter else list(
        TRAVERSABLE_RELATIONSHIPS
    )
    start = _find_any_node(store, graph, start_node_id)
    if start is None:
        return InvestigationResult(
            start_node_id=start_node_id,
            graph=graph,
            explainable_trace=[f"No node with id {start_node_id} exists in {graph}."],
        )

    paths = store.traverse(
        graph,
        start_node_id,
        max_hops=max_hops,
        relationship_types=rels,
        direction=direction,
        limit=limit,
    )
    truncated = len(paths) >= limit

    nodes: dict[str, dict] = {start["id"]: start}
    edges: list[dict] = []
    seen_edges: set[tuple] = set()
    hop_paths: list[dict] = []

    for path in paths:
        for node in path.nodes:
            nodes.setdefault(node["id"], node)
        for edge in path.edges:
            key = (edge["source"], edge["target"], edge["type"])
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(edge)
        hop_paths.append(path.to_dict())

    # Rank paths so the most informative chains come first: deeper paths and
    # paths that cross more distinct node labels carry more explanatory value.
    def informativeness(path: dict) -> tuple:
        labels = {n.get("label") for n in path["nodes"]}
        return (len(labels), path["hop_count"])

    hop_paths.sort(key=informativeness, reverse=True)

    trace = [
        f"Traversed {len(paths)} path(s) up to {max_hops} hops from "
        f"{start.get('name') or start_node_id} ({start.get('label')}) in "
        f"{graph}, following {len(rels)} relationship types "
        f"({'->'.join(rels[:6])}{'...' if len(rels) > 6 else ''})."
    ]
    for path in hop_paths[:8]:
        trace.append(f"  {path['hop_count']}-hop: {path['chain']}")
    if truncated:
        trace.append(
            f"  Traversal hit the {limit}-path budget and was truncated; "
            f"results are a lower bound."
        )

    return InvestigationResult(
        start_node_id=start_node_id,
        graph=graph,
        nodes=list(nodes.values()),
        edges=edges,
        hop_paths=hop_paths,
        explainable_trace=trace,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Decision provenance (Track 03)
# ---------------------------------------------------------------------------


@dataclass
class ProvenanceResult:
    entity_id: str
    entity: dict | None
    timeline: list[dict] = field(default_factory=list)
    governing_adrs: list[dict] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    narrative: str = ""

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "entity": self.entity,
            "timeline": self.timeline,
            "governing_adrs": self.governing_adrs,
            "decisions": self.decisions,
            "narrative": self.narrative,
        }


def trace_decision_provenance(
    store: GraphStore, graph: str, entity_id: str
) -> ProvenanceResult:
    """Walk backwards from code to the humans and documents that shaped it.

    The walk is: entity -> implementing modules -> PRs that modified them ->
    the person who authored each PR, the ticket it implements, the discussion
    it came from, and the ADR that discussion produced.
    """
    entity = _find_any_node(store, graph, entity_id)
    if entity is None:
        return ProvenanceResult(
            entity_id=entity_id,
            entity=None,
            narrative=f"No entity {entity_id} exists in {graph}.",
        )

    snapshot = store.snapshot(graph, limit=8000)
    nodes_by_id = {n["id"]: n for n in snapshot.nodes}
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for edge in snapshot.edges:
        outgoing.setdefault(edge["source"], []).append(edge)
        incoming.setdefault(edge["target"], []).append(edge)

    timeline: list[dict] = []
    seen_events: set[tuple[str, str]] = set()

    def add(event_type: str, node_id: str, detail: str, ts: str | None = None) -> None:
        # The same decision is frequently reachable by more than one route (an
        # ADR can both govern the service directly and be produced by the
        # discussion behind a PR). A timeline that lists it twice reads as two
        # separate decisions, so identical (event, node) pairs collapse.
        key = (event_type, node_id)
        if key in seen_events:
            return
        seen_events.add(key)
        node = nodes_by_id.get(node_id, {"id": node_id})
        timeline.append(
            {
                "event": event_type,
                "node_id": node_id,
                "label": node.get("label"),
                "title": node.get("title")
                or node.get("summary")
                or node.get("message")
                or node.get("name")
                or node_id,
                "timestamp": ts or node.get("created_at") or node.get("timestamp"),
                "detail": detail,
                "source_url": node.get("source_url"),
            }
        )

    # 1. The entity itself.
    add("entity", entity_id, f"{entity.get('label')} under investigation")

    # 2. Modules implementing the entity (if it is a service).
    module_ids: list[str] = []
    if entity.get("label") == "Service":
        module_ids = [
            e["target"] for e in outgoing.get(entity_id, []) if e["type"] == "IMPLEMENTED_BY"
        ]
    elif entity.get("label") == "ASTModule":
        module_ids = [entity_id]
    for module_id in module_ids:
        add("module", module_id, "implements the entity")

    # 3. PRs that modified those modules, newest first. A PR touching two
    #    modules must only appear once, so the timeline and the count reflect
    #    distinct changes rather than module-PR pairs.
    pr_ids: list[str] = []
    for module_id in module_ids:
        for edge in incoming.get(module_id, []):
            if edge["type"] == "MODIFIES":
                pr_id = edge["source"]
                if pr_id in pr_ids:
                    continue
                pr_ids.append(pr_id)
                add(
                    "pull_request",
                    pr_id,
                    f"modified {module_id} "
                    f"({edge.get('properties', {}).get('lines_changed', '?')} lines, "
                    f"breaking risk "
                    f"{edge.get('properties', {}).get('breaking_risk', '?')})",
                )

    # 4. Authors, tickets, discussions, ADRs.
    for pr_id in pr_ids:
        for edge in incoming.get(pr_id, []):
            if edge["type"] == "AUTHORED":
                add("author", edge["source"], f"authored {pr_id}")
        for edge in outgoing.get(pr_id, []):
            if edge["type"] == "IMPLEMENTS":
                ticket_id = edge["target"]
                add("ticket", ticket_id, f"implemented by {pr_id}")
                for ticket_edge in outgoing.get(ticket_id, []):
                    if ticket_edge["type"] == "DISCUSSED_IN":
                        thread_id = ticket_edge["target"]
                        add(
                            "discussion",
                            thread_id,
                            f"discussion behind {ticket_id}",
                        )
                        for thread_edge in outgoing.get(thread_id, []):
                            if thread_edge["type"] == "RESULTED_IN_DECISION":
                                add(
                                    "adr",
                                    thread_edge["target"],
                                    f"decision produced by {thread_id}",
                                )

    # 5. ADRs that govern the entity directly.
    governing: list[dict] = []
    for edge in incoming.get(entity_id, []):
        if edge["type"] == "GOVERNS":
            adr = nodes_by_id.get(edge["source"])
            if adr:
                governing.append(adr)
                add("adr", edge["source"], "governs this entity")

    # Sort the timeline oldest-first so it reads as a story, with undated
    # events last rather than first.
    timeline.sort(key=lambda item: (item["timestamp"] is None, item["timestamp"] or ""))

    decisions = [
        f"{adr.get('code')}: {adr.get('rationale')}"
        for adr in governing
        if adr.get("rationale")
    ]
    # ADRs reached through the discussion chain (ticket -> Slack -> decision) are
    # decisions that shaped this code just as much as a direct GOVERNS edge, and
    # they are frequently the *more* interesting find because the link is
    # historical rather than declared.
    chain_adr_ids = [
        event["node_id"]
        for event in timeline
        if event["event"] == "adr" and event["node_id"] not in {a["id"] for a in governing}
    ]
    for adr_id in chain_adr_ids:
        adr = nodes_by_id.get(adr_id)
        if adr and adr.get("rationale"):
            decisions.append(
                f"{adr.get('code')} (reached via discussion): {adr.get('rationale')}"
            )
        if adr:
            governing.append(adr)

    entity_name = entity.get("name") or entity.get("file_path") or entity_id
    narrative_parts = [f"Provenance for {entity_name} ({entity.get('label')})."]
    if pr_ids:
        narrative_parts.append(
            f"It was shaped by {len(pr_ids)} pull request(s): {', '.join(sorted(set(pr_ids)))}."
        )
    if governing:
        narrative_parts.append(
            "Governing decisions: "
            + "; ".join(
                f"{adr.get('code')} - {adr.get('title')}" for adr in governing
            )
            + "."
        )
    if not pr_ids and not governing:
        narrative_parts.append(
            "No authored history or governing decision was found, which itself is "
            "a finding: this is undocumented surface area."
        )

    return ProvenanceResult(
        entity_id=entity_id,
        entity=entity,
        timeline=timeline,
        governing_adrs=governing,
        decisions=decisions,
        narrative=" ".join(narrative_parts),
    )


# ---------------------------------------------------------------------------
# Incident triage (Track 01 + 02)
# ---------------------------------------------------------------------------


@dataclass
class SuspectCause:
    service: dict
    pr: dict | None
    author: dict | None
    hop_distance: int
    causal_chain: list[str]
    discussion: dict | None = None
    ticket: dict | None = None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "pr": self.pr,
            "author": self.author,
            "hop_distance": self.hop_distance,
            "causal_chain": self.causal_chain,
            "discussion": self.discussion,
            "ticket": self.ticket,
            "score": round(self.score, 4),
            "reasons": self.reasons,
        }


@dataclass
class TriageResult:
    alert_id: str
    alert: dict | None
    symptom_service: dict | None
    suspects: list[SuspectCause] = field(default_factory=list)
    signature: str = ""
    blast: dict = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)
    narrative: str = ""

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "alert": self.alert,
            "symptom_service": self.symptom_service,
            "suspects": [s.to_dict() for s in self.suspects],
            "signature": self.signature,
            "blast": self.blast,
            "trace": self.trace,
            "narrative": self.narrative,
        }


def incident_signature(
    store: GraphStore,
    graph: str,
    alert_id: str | None = None,
    service_ids: Sequence[str] = (),
    max_hops: int = 3,
) -> str:
    """Canonical structural signature for an incident.

    Both the seeder and live triage call this, which is what guarantees that a
    seeded episode and a live alert are compared on identical terms. If the two
    sides built their signatures differently, episodic recall would silently
    degrade to zero similarity - which is exactly the failure mode this shared
    helper exists to prevent.

    The anchor is the alert (if there is one) plus each affected service walked
    inbound along ``CALLS``. That captures the symptom shape: what fired, what it
    fired on, what calls into it, and what code implements it.
    """
    from ..memory.memory import structural_signature

    signature_rels = ["TRIGGERED_ON", "CALLS", "IMPLEMENTED_BY", "IMPORTS"]
    paths: list[PathRecord] = []
    if alert_id:
        paths += store.traverse(
            graph,
            alert_id,
            max_hops,
            relationship_types=signature_rels,
            direction="out",
            limit=150,
        )
    for service_id in service_ids:
        if not service_id:
            continue
        paths += store.traverse(
            graph,
            service_id,
            max_hops,
            relationship_types=["CALLS"],
            direction="in",
            limit=80,
        )
        paths += store.traverse(
            graph,
            service_id,
            max_hops,
            relationship_types=["IMPLEMENTED_BY", "IMPORTS"],
            direction="out",
            limit=60,
        )
    return structural_signature(paths)


def triage_incident(
    store: GraphStore,
    graph: str,
    alert_id: str,
    window_hours: int = 2,
    max_hops: int = 4,
) -> TriageResult:
    """Find recently-changed services upstream of an alerting service.

    This is the multi-hop root-cause walk: alert -> symptom service -> upstream
    callers -> the modules they own -> the PRs that touched those modules inside
    the change window -> the humans and discussions behind them.
    """
    from ..memory.memory import structural_signature

    alert = store.get_node(graph, "Alert", alert_id)
    if alert is None:
        return TriageResult(
            alert_id=alert_id,
            alert=None,
            symptom_service=None,
            narrative=f"No alert {alert_id} exists in {graph}.",
        )

    snapshot = store.snapshot(graph, limit=8000)
    nodes_by_id = {n["id"]: n for n in snapshot.nodes}
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for edge in snapshot.edges:
        outgoing.setdefault(edge["source"], []).append(edge)
        incoming.setdefault(edge["target"], []).append(edge)

    symptom_id = next(
        (
            e["target"]
            for e in outgoing.get(alert_id, [])
            if e["type"] == "TRIGGERED_ON"
        ),
        None,
    )
    symptom = nodes_by_id.get(symptom_id) if symptom_id else None

    trace = [
        f"Alert {alert_id} ({alert.get('severity')}) fired on "
        f"{symptom.get('name') if symptom else 'an unknown service'}: "
        f"{alert.get('summary')}"
    ]

    # Upstream callers: who calls the symptom service. A change in a caller
    # frequently surfaces as an error *here*.
    upstream_paths = (
        store.traverse(
            graph,
            symptom_id,
            max_hops=max_hops,
            relationship_types=["CALLS"],
            direction="in",
            limit=200,
        )
        if symptom_id
        else []
    )
    trace.append(
        f"Walked {len(upstream_paths)} inbound CALLS path(s) up to {max_hops} "
        f"hops to find services that could be driving this."
    )

    hop_distance: dict[str, int] = {}
    chain_for: dict[str, list[str]] = {}
    for path in upstream_paths:
        for depth, node in enumerate(path.nodes):
            # Depth 0 is the symptom service itself, and the final node of a
            # cyclic path (A -> B -> C -> A) is *also* the symptom service. Both
            # must be skipped, otherwise the symptom is reported as a cause of
            # itself several hops upstream.
            if depth == 0 or node["id"] == symptom_id:
                continue
            if node["id"] not in hop_distance or depth < hop_distance[node["id"]]:
                hop_distance[node["id"]] = depth
                chain_for[node["id"]] = [
                    n.get("name") or n["id"] for n in path.nodes[: depth + 1]
                ]

    cutoff = _now() - timedelta(hours=window_hours)
    suspects: list[SuspectCause] = []

    for service_id, distance in hop_distance.items():
        service = nodes_by_id.get(service_id)
        if not service or service.get("label") != "Service":
            continue
        module_ids = [
            e["target"]
            for e in outgoing.get(service_id, [])
            if e["type"] == "IMPLEMENTED_BY"
        ]
        for module_id in module_ids:
            for edge in incoming.get(module_id, []):
                if edge["type"] != "MODIFIES":
                    continue
                pr_id = edge["source"]
                pr = nodes_by_id.get(pr_id)
                if not pr:
                    continue
                pr_ts = _parse_ts(pr.get("created_at"))
                within_window = bool(pr_ts and pr_ts >= cutoff)

                author = None
                for author_edge in incoming.get(pr_id, []):
                    if author_edge["type"] == "AUTHORED":
                        author = nodes_by_id.get(author_edge["source"])
                        break

                ticket = None
                discussion = None
                for impl_edge in outgoing.get(pr_id, []):
                    if impl_edge["type"] == "IMPLEMENTS":
                        ticket = nodes_by_id.get(impl_edge["target"])
                        if ticket:
                            for d_edge in outgoing.get(ticket["id"], []):
                                if d_edge["type"] == "DISCUSSED_IN":
                                    discussion = nodes_by_id.get(d_edge["target"])
                                    break
                    if ticket and discussion:
                        break

                reasons: list[str] = []
                score = 0.0
                # Distance: closer changes are likelier causes.
                score += max(0.0, 0.30 - 0.05 * distance)
                reasons.append(f"{distance} hop(s) upstream of the symptom service")
                if within_window:
                    score += 0.35
                    reasons.append(
                        f"PR opened within the last {window_hours}h "
                        f"({pr.get('created_at')})"
                    )
                risk = float(
                    edge.get("properties", {}).get("breaking_risk", 0) or 0
                )
                if risk:
                    score += 0.25 * min(risk, 1.0)
                    reasons.append(f"declared breaking risk {risk:.2f}")
                if service.get("tier") == 1:
                    score += 0.10
                    reasons.append("service is Tier-1")

                suspects.append(
                    SuspectCause(
                        service=service,
                        pr=pr,
                        author=author,
                        hop_distance=distance,
                        causal_chain=chain_for.get(service_id, []),
                        discussion=discussion,
                        ticket=ticket,
                        score=score,
                        reasons=reasons,
                    )
                )

    suspects.sort(key=lambda s: s.score, reverse=True)

    # The signature is built from the paths actually traversed, which is what
    # lets episodic recall be structural rather than keyword-based.
    signature = incident_signature(
        store, graph, alert_id=alert_id, service_ids=[symptom_id] if symptom_id else []
    )

    blast = blast_radius(
        store,
        graph,
        [symptom_id] if symptom_id else [],
        max_hops=max_hops,
    )

    if suspects:
        top = suspects[0]
        narrative = (
            f"Alert {alert_id} on {symptom.get('name') if symptom else 'unknown'} "
            f"most likely originates at "
            f"{top.service.get('name')} ({top.hop_distance} hops upstream), "
            f"via {top.pr.get('id') if top.pr else 'an unknown change'}"
            + (
                f" authored by {top.author.get('handle')}"
                if top.author
                else ""
            )
            + ". "
            + " ".join(top.reasons)
            + "."
        )
    else:
        narrative = (
            f"Alert {alert_id} on {symptom.get('name') if symptom else 'unknown'} "
            f"has no recently-changed upstream service. The cause is more likely "
            f"an infrastructure or traffic condition than a code change."
        )

    return TriageResult(
        alert_id=alert_id,
        alert=alert,
        symptom_service=symptom,
        suspects=suspects,
        signature=signature,
        blast=blast.to_dict(),
        trace=trace,
        narrative=narrative,
    )


def dependency_path(
    store: GraphStore, graph: str, source_id: str, target_id: str
):
    """Convenience wrapper around the shortest causal path algorithm."""
    return shortest_dependency_path(
        store, graph, source_id, target_id, edge_types=list(CAUSAL_CHAIN_RELATIONSHIPS)
    )
