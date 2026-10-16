"""The unified ChronosGraph ontology.

One property graph carries all three hackathon tracks:

* Track 01 - live code, infrastructure and telemetry (``Service``, ``ASTModule`` ...)
* Track 02 - agent memory (``EpisodicIncident``, ``ProceduralPlaybook`` ...)
* Track 03 - the company brain (``Person``, ``PullRequest``, ``ADR`` ...)

Every node carries ``tenant_id`` plus bitemporal fields (``valid_from`` /
``valid_to``) so the same graph can answer "what did we believe then?" and
"what happens if we change it now?" without a second datastore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Track(str, Enum):
    """Which hackathon track a schema element primarily serves."""

    PRESENT = "track_01_present"
    MEMORY = "track_02_memory"
    BRAIN = "track_03_company_brain"


@dataclass(frozen=True)
class NodeSpec:
    label: str
    key: str
    track: Track
    properties: tuple[str, ...] = ()

    @property
    def qualified_key(self) -> str:
        return f"{self.label}.{self.key}"


@dataclass(frozen=True)
class EdgeSpec:
    type: str
    source: str
    target: str
    track: Track
    properties: tuple[str, ...] = ()
    #: Traversable edges are the ones multi-hop reasoning is allowed to walk.
    #: Governance edges (``GOVERNS``, ``OWNS``) are intentionally excluded so a
    #: blast-radius walk cannot accidentally hop from a service into an ADR and
    #: then back out into an unrelated subsystem.
    traversable: bool = True


NODES: tuple[NodeSpec, ...] = (
    # ---- Track 01: code, infra, telemetry -----------------------------------
    NodeSpec(
        "Service",
        "id",
        Track.PRESENT,
        ("name", "tier", "sla_ms", "criticality_score", "status", "language", "repo"),
    ),
    NodeSpec(
        "ASTModule",
        "id",
        Track.PRESENT,
        ("file_path", "language", "ast_hash", "complexity", "loc"),
    ),
    NodeSpec(
        "APISchema",
        "id",
        Track.PRESENT,
        ("endpoint", "version", "payload_hash", "fields"),
    ),
    NodeSpec(
        "SchemaField",
        "id",
        Track.PRESENT,
        ("name", "type", "nullable", "breaking", "owner_module"),
    ),
    NodeSpec(
        "InfraResource",
        "id",
        Track.PRESENT,
        ("kind", "region", "config_json"),
    ),
    NodeSpec(
        "Alert",
        "id",
        Track.PRESENT,
        ("severity", "metric", "value", "threshold", "timestamp", "status", "summary"),
    ),
    # ---- Track 02: agent memory ---------------------------------------------
    NodeSpec(
        "EpisodicIncident",
        "id",
        Track.MEMORY,
        ("summary", "occurred_at", "root_cause_hash", "mttr_seconds", "severity"),
    ),
    NodeSpec(
        "ProceduralPlaybook",
        "id",
        Track.MEMORY,
        ("name", "trigger_signature", "action_steps_json", "success_rate", "uses"),
    ),
    NodeSpec(
        "SemanticRule",
        "id",
        Track.MEMORY,
        ("invariant_statement", "enforcement_level", "domain"),
    ),
    NodeSpec("AgentSession", "id", Track.MEMORY, ("active_agent", "status", "updated_at")),
    NodeSpec(
        "ReasoningStep",
        "id",
        Track.MEMORY,
        ("step_index", "agent_name", "tool_called", "cypher_executed", "observation"),
    ),
    # ---- Track 03: company brain --------------------------------------------
    NodeSpec(
        "Person",
        "id",
        Track.BRAIN,
        ("handle", "name", "role", "timezone", "on_call"),
    ),
    NodeSpec("Team", "id", Track.BRAIN, ("name", "slack_channel")),
    NodeSpec(
        "PullRequest",
        "id",
        Track.BRAIN,
        ("number", "title", "state", "diff_summary", "created_at", "repo"),
    ),
    NodeSpec(
        "Commit",
        "id",
        Track.BRAIN,
        ("sha", "message", "timestamp", "additions", "deletions"),
    ),
    NodeSpec(
        "ADR",
        "id",
        Track.BRAIN,
        (
            "code",
            "title",
            "status",
            "rationale",
            "created_at",
            # Machine-checkable constraint, e.g.
            # {"service": "SVC-AUTH", "max_latency_p99": 200,
            #  "forbids": ["synchronous_retry"]}. Kept as JSON so the shadow
            # simulator can evaluate an ADR instead of only quoting it.
            "constraint_json",
        ),
    ),
    NodeSpec(
        "DiscussionThread",
        "id",
        Track.BRAIN,
        ("platform", "channel", "summary", "source_url", "timestamp"),
    ),
    NodeSpec("Ticket", "id", Track.BRAIN, ("key", "title", "priority", "status")),
)

EDGES: tuple[EdgeSpec, ...] = (
    # ---- Track 01 -----------------------------------------------------------
    EdgeSpec("CALLS", "Service", "Service", Track.PRESENT, ("latency_p99", "weight", "protocol")),
    EdgeSpec("EXPOSES", "Service", "APISchema", Track.PRESENT),
    EdgeSpec("HAS_FIELD", "APISchema", "SchemaField", Track.PRESENT),
    EdgeSpec("IMPLEMENTED_BY", "Service", "ASTModule", Track.PRESENT),
    EdgeSpec("IMPORTS", "ASTModule", "ASTModule", Track.PRESENT, ("imported_symbol",)),
    EdgeSpec("DEPLOYED_ON", "Service", "InfraResource", Track.PRESENT),
    EdgeSpec("TRIGGERED_ON", "Alert", "Service", Track.PRESENT),
    EdgeSpec("MODIFIES", "PullRequest", "ASTModule", Track.PRESENT, ("lines_changed", "breaking_risk")),
    # ---- Track 02 -----------------------------------------------------------
    EdgeSpec("AFFECTED", "EpisodicIncident", "Service", Track.MEMORY),
    EdgeSpec("RESOLVED_BY", "EpisodicIncident", "ProceduralPlaybook", Track.MEMORY, ("success",)),
    EdgeSpec("PRODUCED_STEP", "AgentSession", "ReasoningStep", Track.MEMORY),
    # ``*`` marks a polymorphic target: a reasoning step can inspect *any*
    # node, so the caller must name the label explicitly when writing the edge.
    EdgeSpec("INSPECTED_NODE", "ReasoningStep", "*", Track.MEMORY, traversable=False),
    EdgeSpec("HANDED_OFF_TO", "ReasoningStep", "ReasoningStep", Track.MEMORY, ("context_summary",)),
    # ---- Track 03 -----------------------------------------------------------
    EdgeSpec("MEMBER_OF", "Person", "Team", Track.BRAIN, traversable=False),
    EdgeSpec("OWNS", "Team", "Service", Track.BRAIN, traversable=False),
    EdgeSpec("AUTHORED", "Person", "PullRequest", Track.BRAIN, traversable=False),
    EdgeSpec("AUTHORED", "Person", "ADR", Track.BRAIN, traversable=False),
    EdgeSpec("REVIEWED", "Person", "PullRequest", Track.BRAIN, ("sentiment", "approved"), traversable=False),
    EdgeSpec("COMMITTED", "Person", "Commit", Track.BRAIN, traversable=False),
    EdgeSpec("INCLUDED_IN", "Commit", "PullRequest", Track.BRAIN, traversable=False),
    EdgeSpec("IMPLEMENTS", "PullRequest", "Ticket", Track.BRAIN, traversable=False),
    EdgeSpec("DISCUSSED_IN", "Ticket", "DiscussionThread", Track.BRAIN, traversable=False),
    EdgeSpec("RESULTED_IN_DECISION", "DiscussionThread", "ADR", Track.BRAIN, traversable=False),
    EdgeSpec("GOVERNS", "ADR", "Service", Track.BRAIN, traversable=False),
    EdgeSpec("CONSTRAINS", "ADR", "APISchema", Track.BRAIN, traversable=False),
    EdgeSpec("VIOLATES", "PullRequest", "ADR", Track.BRAIN, ("severity", "evidence"), traversable=False),
)

NODE_BY_LABEL: dict[str, NodeSpec] = {n.label: n for n in NODES}

#: The primary shape of each edge type. Built with ``setdefault`` so the *first*
#: declaration wins: a plain comprehension would let the last one silently
#: redefine the type, which is how AUTHORED briefly started resolving to ADR.
EDGE_BY_TYPE: dict[str, EdgeSpec] = {}
for _spec in EDGES:
    EDGE_BY_TYPE.setdefault(_spec.type, _spec)

#: Every declared target label per edge type. A relationship such as AUTHORED
#: genuinely points at more than one label (a person authors a PR *and* an ADR),
#: which a single EdgeSpec cannot express, so the store accepts an explicit
#: override and validates it against this map.
EDGE_TARGETS: dict[str, frozenset[str]] = {}
for _spec in EDGES:
    EDGE_TARGETS[_spec.type] = EDGE_TARGETS.get(_spec.type, frozenset()) | {
        _spec.target
    }

#: Edge types the blast-radius / root-cause walkers are allowed to traverse.
TRAVERSABLE_RELATIONSHIPS: tuple[str, ...] = tuple(
    e.type for e in EDGES if e.traversable
)

#: Canonical ordered walk used to produce explainable hop chains.
CAUSAL_CHAIN_RELATIONSHIPS: tuple[str, ...] = (
    "TRIGGERED_ON",
    "CALLS",
    "IMPLEMENTED_BY",
    "IMPORTS",
    "MODIFIES",
    "HAS_FIELD",
    "EXPOSES",
)


def validate_schema() -> list[str]:
    """Return a list of schema inconsistencies (empty means healthy)."""
    problems: list[str] = []
    labels = set(NODE_BY_LABEL)
    for edge in EDGES:
        if edge.source not in labels:
            problems.append(f"edge {edge.type}: unknown source label {edge.source}")
        # ``*`` is the declared polymorphic target, not a typo.
        if edge.target != "*" and edge.target not in labels:
            problems.append(f"edge {edge.type}: unknown target label {edge.target}")
    for node in NODES:
        if node.key not in node.properties and node.properties:
            # ``id`` is the universal key and is not repeated in properties.
            if node.key != "id":
                problems.append(f"node {node.label}: key {node.key} missing from properties")
    return problems


@dataclass
class GraphSnapshot:
    """A serialisable projection of part of the graph."""

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)

    def node_ids(self) -> list[str]:
        return [n["id"] for n in self.nodes]

    def to_dict(self) -> dict:
        return {"nodes": self.nodes, "edges": self.edges}
