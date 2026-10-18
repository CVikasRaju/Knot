"""Tri-tier agent memory: episodic, semantic and procedural.

The memory layer is the reason the swarm gets *faster* over time instead of
rediscovering the same outage. It lives in its own FalkorDB tenant graph
(``agent_memory``) so the company brain can be re-seeded without erasing what
the agents have learned, and vice versa.

Similarity matching is deliberately **structural**, not vector-based. Two
incidents are "the same kind of problem" when they present the same *shape* of
evidence - the same node labels connected by the same relationship types in the
same causal order. That is a far stronger signal than cosine distance over
summarised prose, it needs no embedding provider, and it is fully explainable:
the matcher can show exactly which signature tokens overlapped.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from ..graph.ontology import CAUSAL_CHAIN_RELATIONSHIPS, NODE_BY_LABEL
from ..graph.store import GraphStore, PathRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def structural_signature(paths: Sequence[PathRecord]) -> str:
    """Reduce a set of traversed paths to a comparable symptom fingerprint.

    Tokens come in three flavours:

    * ``Alert:TRIGGERED_ON>Service`` - a hop, capturing the *shape* of the fault.
    * ``Service`` / ``Service#tier1`` - a node label, capturing the kind of estate
      involved.
    * ``Service@PaymentAPI`` - an identity token, capturing *which* component.

    Shape tokens alone are not enough: in a twelve-service estate almost every
    incident shares ``Service:CALLS>Service``, so every episode would match every
    alert at the same middling score. Identity tokens are what separate "the
    payment path leaked a connection again" from "some service had a bad day",
    while the shape tokens keep a genuinely analogous incident in a *different*
    service discoverable.
    """
    tokens: set[str] = set()
    for path in paths:
        for edge in path.edges:
            source_label = _label_for(path, edge["source"])
            target_label = _label_for(path, edge["target"])
            tokens.add(f"{source_label}:{edge['type']}>{target_label}")
        for node in path.nodes:
            label = node.get("label", "Unknown")
            tokens.add(label)
            if node.get("tier") is not None:
                tokens.add(f"{label}#tier{node['tier']}")
            identity = node.get("name") or node.get("file_path") or node.get("id")
            if identity:
                tokens.add(f"{label}@{identity}")
    return "|".join(sorted(tokens))


def _label_for(path: PathRecord, node_id: str) -> str:
    for node in path.nodes:
        if node["id"] == node_id:
            return node.get("label", "Unknown")
    return "Unknown"


def signature_tokens(signature: str) -> set[str]:
    return {t for t in signature.split("|") if t}


def signature_similarity(left: str, right: str) -> float:
    """Jaccard similarity over signature tokens."""
    a, b = signature_tokens(left), signature_tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 4)


@dataclass
class PlaybookMatch:
    playbook: dict
    similarity: float
    matched_tokens: list[str]
    rationale: str

    def to_dict(self) -> dict:
        return {
            "playbook": self.playbook,
            "similarity": self.similarity,
            "matched_tokens": self.matched_tokens,
            "rationale": self.rationale,
        }


@dataclass
class EpisodeMatch:
    incident: dict
    similarity: float
    matched_tokens: list[str]

    def to_dict(self) -> dict:
        return {
            "incident": self.incident,
            "similarity": self.similarity,
            "matched_tokens": self.matched_tokens,
        }


class MemoryStore:
    """Read/write access to the tri-tier memory tenant graph."""

    def __init__(
        self, store: GraphStore, graph: str, prod_graph: str = "prod_org_brain"
    ) -> None:
        self.store = store
        self.graph = graph
        self.prod_graph = prod_graph
        self.ensure_graph()

    def ensure_graph(self) -> None:
        if not self.store.graph_exists(self.graph):
            # Creating a graph in FalkorDB is implicit on first write, so seed
            # a sentinel node to make the tenant visible to GRAPH.LIST.
            self.store.merge_nodes(
                self.graph,
                "AgentSession",
                [{"id": "__bootstrap__", "active_agent": "system", "status": "idle"}],
            )

    # ---- episodic ----------------------------------------------------------
    def store_episode(
        self,
        incident_id: str,
        summary: str,
        symptom_signature: str,
        affected_service_ids: Sequence[str],
        severity: str = "SEV2",
        mttr_seconds: int = 0,
        root_cause_hash: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict:
        payload = {
            "id": incident_id,
            "summary": summary,
            "occurred_at": utc_now(),
            "root_cause_hash": root_cause_hash
            or hashlib.sha256(summary.encode()).hexdigest()[:16],
            "mttr_seconds": mttr_seconds,
            "severity": severity,
            "symptom_signature": symptom_signature,
            **(extra or {}),
        }
        self.store.merge_nodes(self.graph, "EpisodicIncident", [payload])
        if affected_service_ids:
            # AFFECTED targets :Service, which lives in the production graph.
            # Mirror the endpoints into this tenant first so the edge can form.
            self._mirror_nodes(affected_service_ids, self.prod_graph)
            self.store.merge_edges(
                self.graph,
                "AFFECTED",
                [
                    {"source": incident_id, "target": service_id}
                    for service_id in affected_service_ids
                ],
            )
        return payload

    def _mirror_nodes(self, node_ids: Sequence[str], source_graph: str) -> int:
        """Copy referenced nodes into the memory graph so edges can point at them.

        Edges are created with both endpoints inside one tenant, so a reference
        to a node that lives in the production graph needs a local copy. The
        copy keeps the original id and label (the ontology key is ``id`` for
        every label), so a lookup by id still resolves. Existing nodes are left
        untouched.
        """
        wanted = {node_id for node_id in node_ids if node_id}
        if not wanted:
            return 0
        source = source_graph if source_graph != self.graph else self.prod_graph
        mirrored = 0
        for label in NODE_BY_LABEL:
            found = [
                node
                for node in self.store.find_nodes(source, label, limit=2000)
                if node["id"] in wanted
            ]
            if not found:
                continue
            present = {
                row["id"]
                for row in self.store.find_nodes(self.graph, label, limit=2000)
            }
            fresh = [node for node in found if node["id"] not in present]
            if fresh:
                self.store.merge_nodes(self.graph, label, fresh)
                mirrored += len(fresh)
        return mirrored

    def retrieve_similar_episodes(
        self, symptom_signature: str, top_k: int = 3, min_similarity: float = 0.0
    ) -> list[EpisodeMatch]:
        # An empty or token-less query has nothing to match on, and returning
        # arbitrary episodes for it is worse than returning none: the caller
        # would treat a random past incident as precedent for this one and could
        # act on an unrelated playbook.
        query_tokens = signature_tokens(symptom_signature)
        if not query_tokens:
            return []

        incidents = self.store.find_nodes(
            self.graph, "EpisodicIncident", limit=500
        )
        matches: list[EpisodeMatch] = []
        for incident in incidents:
            if incident["id"] == "__bootstrap__":
                continue
            candidate = incident.get("symptom_signature") or ""
            if not candidate:
                continue
            similarity = signature_similarity(symptom_signature, candidate)
            if similarity < min_similarity:
                continue
            shared = sorted(query_tokens & signature_tokens(candidate))
            matches.append(
                EpisodeMatch(
                    incident=incident,
                    similarity=similarity,
                    matched_tokens=shared,
                )
            )
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches[:top_k]

    # ---- procedural --------------------------------------------------------
    def store_playbook(
        self,
        playbook_id: str,
        name: str,
        trigger_signature: str,
        action_steps: Sequence[dict],
        success_rate: float = 1.0,
        uses: int = 0,
        derived_from_incident: str | None = None,
    ) -> dict:
        payload = {
            "id": playbook_id,
            "name": name,
            "trigger_signature": trigger_signature,
            "action_steps_json": json.dumps(list(action_steps)),
            "success_rate": success_rate,
            "uses": uses,
        }
        self.store.merge_nodes(self.graph, "ProceduralPlaybook", [payload])
        if derived_from_incident:
            self.store.merge_edges(
                self.graph,
                "RESOLVED_BY",
                [
                    {
                        "source": derived_from_incident,
                        "target": playbook_id,
                        "properties": {"success": success_rate >= 0.5},
                    }
                ],
            )
        return payload

    def retrieve_playbook(
        self, symptom_signature: str, min_similarity: float = 0.0
    ) -> PlaybookMatch | None:
        # Same reasoning as retrieve_similar_episodes: with no query tokens every
        # playbook scores 0.0 and the "best" one would be chosen arbitrarily.
        query_tokens = signature_tokens(symptom_signature)
        if not query_tokens:
            return None

        playbooks = self.store.find_nodes(
            self.graph, "ProceduralPlaybook", limit=500
        )
        best: PlaybookMatch | None = None
        for playbook in playbooks:
            candidate = playbook.get("trigger_signature") or ""
            similarity = signature_similarity(symptom_signature, candidate)
            if similarity < min_similarity:
                continue
            if best is None or similarity > best.similarity:
                shared = sorted(query_tokens & signature_tokens(candidate))
                best = PlaybookMatch(
                    playbook=playbook,
                    similarity=similarity,
                    matched_tokens=shared,
                    rationale=(
                        f"Signature overlap {similarity:.2%} on "
                        f"{len(shared)} shared structural tokens "
                        f"({', '.join(shared[:6]) or 'none'})."
                    ),
                )
        return best

    def reinforce_playbook(self, playbook_id: str, succeeded: bool) -> dict | None:
        """Blend the new outcome into the playbook's success rate."""
        playbook = self.store.get_node(self.graph, "ProceduralPlaybook", playbook_id)
        if not playbook:
            return None
        uses = int(playbook.get("uses") or 0)
        rate = float(playbook.get("success_rate") or 0.0)
        new_uses = uses + 1
        # Exponentially weighted so recent outcomes matter more, but a single
        # failure cannot destroy a long-standing playbook.
        blended = (rate * uses + (1.0 if succeeded else 0.0)) / new_uses
        updated = {
            "id": playbook_id,
            "uses": new_uses,
            "success_rate": round(blended, 4),
        }
        self.store.merge_nodes(self.graph, "ProceduralPlaybook", [updated])
        return updated

    # ---- semantic ----------------------------------------------------------
    def store_rule(
        self,
        rule_id: str,
        statement: str,
        domain: str = "architecture",
        enforcement_level: str = "MUST",
        source: str = "",
        extra: dict | None = None,
    ) -> dict:
        payload = {
            "id": rule_id,
            "invariant_statement": statement,
            "domain": domain,
            "enforcement_level": enforcement_level,
            "source": source,
        }
        payload.update(extra or {})
        self.store.merge_nodes(self.graph, "SemanticRule", [payload])
        return payload

    def rules_for_domain(self, domain: str) -> list[dict]:
        return self.store.find_nodes(self.graph, "SemanticRule", {"domain": domain})

    def all_rules(self) -> list[dict]:
        return self.store.find_nodes(self.graph, "SemanticRule", limit=500)

    # ---- agent coordination ------------------------------------------------
    def open_session(self, session_id: str, active_agent: str) -> dict:
        payload = {
            "id": session_id,
            "active_agent": active_agent,
            "status": "running",
            "updated_at": utc_now(),
        }
        self.store.merge_nodes(self.graph, "AgentSession", [payload])
        return payload

    def close_session(self, session_id: str, status: str = "completed") -> None:
        self.store.merge_nodes(
            self.graph,
            "AgentSession",
            [
                {
                    "id": session_id,
                    "active_agent": "commander",
                    "status": status,
                    "updated_at": utc_now(),
                }
            ],
        )

    def record_step(
        self,
        session_id: str,
        step_index: int,
        agent_name: str,
        tool_called: str,
        cypher_executed: str,
        observation: str,
        inspected_node_ids: Sequence[str] = (),
        handoff_to: str | None = None,
        handoff_context: str = "",
        graph: str | None = None,
    ) -> dict:
        step_id = f"{session_id}-STEP-{step_index:02d}"
        payload = {
            "id": step_id,
            "step_index": step_index,
            "agent_name": agent_name,
            "tool_called": tool_called,
            "cypher_executed": cypher_executed,
            "observation": observation,
        }
        self.store.merge_nodes(self.graph, "ReasoningStep", [payload])
        self.store.merge_edges(
            self.graph,
            "PRODUCED_STEP",
            [{"source": session_id, "target": step_id}],
        )
        # Inspected nodes live in the production graph but are linked from the
        # memory graph. ``INSPECTED_NODE`` is marked non-traversable precisely
        # so this cross-tenant link can never widen a blast-radius walk.
        #
        # The endpoints must be materialised in the memory graph first. Its
        # MATCH is scoped to that tenant, so pointing at production-only node
        # ids silently created nothing - the reasoning audit trail was empty.
        if inspected_node_ids:
            self._mirror_nodes(inspected_node_ids, graph or self.prod_graph)
            # INSPECTED_NODE is polymorphic: one step can inspect a Service, an
            # Alert and a PullRequest at once, and each edge needs the label of
            # its own endpoint, so the rows are written one label at a time.
            labels = self.store.node_labels(self.graph, inspected_node_ids)
            grouped: dict[str, list[dict]] = {}
            for node_id in inspected_node_ids:
                label = labels.get(node_id)
                if label:
                    grouped.setdefault(label, []).append(
                        {"source": step_id, "target": node_id}
                    )
            for label, rows in grouped.items():
                self.store.merge_edges(
                    self.graph, "INSPECTED_NODE", rows, target_label=label
                )
        if handoff_to:
            next_step_id = f"{session_id}-STEP-{step_index + 1:02d}"
            self.store.merge_nodes(
                self.graph,
                "ReasoningStep",
                [
                    {
                        "id": next_step_id,
                        "step_index": step_index + 1,
                        "agent_name": handoff_to,
                        "tool_called": "pending",
                        "cypher_executed": "",
                        "observation": "awaiting handoff",
                    }
                ],
            )
            self.store.merge_edges(
                self.graph,
                "HANDED_OFF_TO",
                [
                    {
                        "source": step_id,
                        "target": next_step_id,
                        "properties": {"context_summary": handoff_context},
                    }
                ],
            )
        return payload

    def session_trace(self, session_id: str) -> list[dict]:
        steps = self.store.find_nodes(self.graph, "ReasoningStep", limit=1000)
        owned = [
            s
            for s in steps
            if str(s.get("id", "")).startswith(f"{session_id}-STEP-")
        ]
        owned.sort(key=lambda s: int(s.get("step_index") or 0))
        return owned
