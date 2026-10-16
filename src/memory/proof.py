"""Explainability artefacts: graph proof certificates and reasoning traces.

Track 01 rewards *visible* multi-hop explainability. Every mutating action the
swarm takes must therefore be accompanied by a certificate that names the exact
nodes, edges and Cypher statement that justified it. This module is the single
place those certificates are constructed, so no agent can quietly act without
one.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from ..graph.store import GraphStore, PathRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProofCertificate:
    """A verifiable justification for one agent decision.

    ``verdict`` is deliberately three-valued. ``INSUFFICIENT_EVIDENCE`` is a
    first-class outcome: the commander agent refuses to act on it, which is what
    stops the swarm from confidently reverting production code on a hunch.
    """

    claim: str
    verdict: str
    confidence: float
    graph: str
    node_ids: list[str] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    cypher: str = ""
    hop_count: int = 0
    evidence: list[str] = field(default_factory=list)
    certificate_id: str = field(default_factory=lambda: f"PROOF-{uuid.uuid4().hex[:10]}")
    created_at: str = field(default_factory=_now)

    @property
    def is_actionable(self) -> bool:
        return self.verdict == "PROVEN"

    def digest(self) -> str:
        payload = f"{self.claim}|{self.verdict}|{','.join(sorted(self.node_ids))}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "certificate_id": self.certificate_id,
            "claim": self.claim,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 4),
            "graph": self.graph,
            "node_ids": self.node_ids,
            "edges": self.edges,
            "cypher": self.cypher,
            "hop_count": self.hop_count,
            "evidence": self.evidence,
            "digest": self.digest(),
            "created_at": self.created_at,
            "is_actionable": self.is_actionable,
        }


def certificate_from_paths(
    claim: str,
    graph: str,
    paths: Sequence[PathRecord],
    cypher: str = "",
    confidence: float | None = None,
    extra_evidence: Sequence[str] = (),
    node_ids: Sequence[str] = (),
    edges: Sequence[dict] = (),
) -> ProofCertificate:
    """Build a certificate from the paths that were actually traversed.

    ``node_ids`` / ``edges`` may be supplied directly for evidence that did not
    come from a single traversal - for example a provenance timeline assembled
    from several independent walks. Path-derived evidence is merged in, not
    replaced, so a caller can combine both sources.
    """
    collected_node_ids: list[str] = list(node_ids)
    collected_edges: list[dict] = list(edges)
    for path in paths:
        for node in path.nodes:
            if node["id"] not in collected_node_ids:
                collected_node_ids.append(node["id"])
        for edge in path.edges:
            if edge not in collected_edges:
                collected_edges.append(edge)

    hop_count = max((p.hop_count for p in paths), default=0)
    evidence = [f"{p.hop_count}-hop: {' '.join(p.describe())}" for p in paths[:6]]
    evidence.extend(extra_evidence)

    if confidence is None:
        # Confidence grows with evidence depth but saturates; a single 1-hop
        # match is weak, a 4-hop chain is strong, and more than ~6 hops adds
        # almost nothing because the search has already fanned out.
        depth_factor = min(hop_count / 4.0, 1.0) if hop_count else 0.0
        breadth_factor = min(len(paths) / 5.0, 1.0)
        confidence = round(0.35 * depth_factor + 0.45 * breadth_factor + 0.20, 4)
        confidence = min(confidence, 0.99)

    if not collected_node_ids:
        verdict = "INSUFFICIENT_EVIDENCE"
        confidence = 0.0
    elif confidence >= 0.85:
        verdict = "PROVEN"
    elif confidence >= 0.5:
        verdict = "PARTIAL"
    else:
        verdict = "INSUFFICIENT_EVIDENCE"

    return ProofCertificate(
        claim=claim,
        verdict=verdict,
        confidence=confidence,
        graph=graph,
        node_ids=collected_node_ids,
        edges=collected_edges,
        cypher=cypher,
        hop_count=hop_count,
        evidence=evidence,
    )


@dataclass
class ReasoningTrace:
    """Ordered record of what each agent did, for the UI audit panel."""

    session_id: str
    steps: list[dict] = field(default_factory=list)

    def add(
        self,
        agent: str,
        action: str,
        observation: str,
        tool: str = "",
        cypher: str = "",
        inspected: Sequence[str] = (),
        certificates: Sequence[ProofCertificate] = (),
    ) -> dict:
        step = {
            "index": len(self.steps),
            "agent": agent,
            "action": action,
            "tool": tool,
            "observation": observation,
            "cypher": cypher,
            "inspected_node_ids": list(inspected),
            "certificates": [c.to_dict() for c in certificates],
            "at": _now(),
        }
        self.steps.append(step)
        return step

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "steps": self.steps}


def graph_proof_summary(
    store: GraphStore, graph: str, certificate: ProofCertificate
) -> dict:
    """Re-read the certificate's node ids from the graph to prove they exist.

    A certificate that cannot be replayed against the live graph is worthless,
    so the API surfaces this verification result next to every action.
    """
    verified: list[dict] = []
    for node_id in certificate.node_ids:
        found = None
        for label in (
            "Service",
            "ASTModule",
            "APISchema",
            "SchemaField",
            "InfraResource",
            "Alert",
            "PullRequest",
            "Commit",
            "ADR",
            "DiscussionThread",
            "Ticket",
            "Person",
            "Team",
        ):
            found = store.get_node(graph, label, node_id)
            if found:
                break
        verified.append({"id": node_id, "exists": found is not None, "node": found})
    return {
        "certificate_id": certificate.certificate_id,
        "graph": graph,
        "verified_nodes": verified,
        "all_nodes_exist": all(v["exists"] for v in verified) if verified else False,
    }
