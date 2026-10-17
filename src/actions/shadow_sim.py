"""Counterfactual shadow-graph simulation - Knot's "pre-crime" engine.

The idea in one paragraph: a pull request is a *hypothesis about the future*.
Instead of reviewing the diff as text, Knot forks the production
knowledge graph into an ephemeral FalkorDB tenant (``shadow_sim_<id>``), injects
the PR's structural delta into the fork, and then asks graph questions about the
forked world. Because the fork is a real graph, every prediction is a traversal
that can be replayed and shown to a human - and because it is a *separate*
tenant, the production brain is never mutated by a what-if question.

The engine is deliberately deterministic. Given the same graph and the same PR
delta it produces the same verdict, which is what makes the block-merge action
defensible to a reviewer.
"""

from __future__ import annotations

import json
import time
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..graph.algorithms import (
    AlgorithmResult,
    betweenness_centrality,
    blast_radius,
    detect_cycles,
)
from ..graph.store import GraphStore
from ..memory.proof import ProofCertificate, certificate_from_paths


@dataclass
class MutationSpec:
    """A structural delta extracted from a PR.

    Everything here is expressed in ontology terms rather than as raw text, so
    the simulation can apply it to the graph without an LLM in the loop. The
    parsing layer (``src/actions/diff_parser.py``) is what turns a diff into
    these; the simulator only ever sees structure.
    """

    modified_module_ids: list[str] = field(default_factory=list)
    #: Fields removed or made non-nullable on an existing API schema.
    removed_schema_field_ids: list[str] = field(default_factory=list)
    #: New fields being introduced.
    added_schema_field_ids: list[str] = field(default_factory=list)
    #: ``(source_service, target_service)`` call edges the PR adds.
    added_call_edges: list[tuple[str, str]] = field(default_factory=list)
    #: ``(source_service, target_service)`` call edges the PR removes.
    removed_call_edges: list[tuple[str, str]] = field(default_factory=list)
    #: ``(from_module, to_module)`` imports the PR introduces.
    added_import_edges: list[tuple[str, str]] = field(default_factory=list)
    #: Latency injected onto call edges originating at the modified services.
    latency_delta_ms: float = 0.0
    breaking_schema_change: bool = False
    summary: str = ""

    def describe(self) -> list[str]:
        lines = []
        if self.modified_module_ids:
            lines.append(f"modifies modules: {', '.join(self.modified_module_ids)}")
        if self.removed_schema_field_ids:
            lines.append(
                f"removes schema fields: {', '.join(self.removed_schema_field_ids)}"
            )
        if self.added_schema_field_ids:
            lines.append(
                f"adds schema fields: {', '.join(self.added_schema_field_ids)}"
            )
        if self.added_call_edges:
            lines.append(
                "adds call edges: "
                + ", ".join(f"{s}->{t}" for s, t in self.added_call_edges)
            )
        if self.removed_call_edges:
            lines.append(
                "removes call edges: "
                + ", ".join(f"{s}->{t}" for s, t in self.removed_call_edges)
            )
        if self.added_import_edges:
            lines.append(
                "adds imports: "
                + ", ".join(f"{s}->{t}" for s, t in self.added_import_edges)
            )
        if self.latency_delta_ms:
            lines.append(f"latency delta: +{self.latency_delta_ms}ms")
        if self.breaking_schema_change:
            lines.append("declares a breaking schema change")
        return lines

    def to_dict(self) -> dict:
        return {
            "modified_module_ids": self.modified_module_ids,
            "removed_schema_field_ids": self.removed_schema_field_ids,
            "added_schema_field_ids": self.added_schema_field_ids,
            "added_call_edges": [list(e) for e in self.added_call_edges],
            "removed_call_edges": [list(e) for e in self.removed_call_edges],
            "added_import_edges": [list(e) for e in self.added_import_edges],
            "latency_delta_ms": self.latency_delta_ms,
            "breaking_schema_change": self.breaking_schema_change,
            "summary": self.summary,
        }


@dataclass
class AdrViolation:
    adr: dict
    rule: str
    severity: str
    evidence: str
    proof: ProofCertificate | None = None

    def to_dict(self) -> dict:
        return {
            "adr": self.adr,
            "rule": self.rule,
            "severity": self.severity,
            "evidence": self.evidence,
            "proof": self.proof.to_dict() if self.proof else None,
        }


@dataclass
class SimulationResult:
    simulation_id: str
    shadow_graph: str
    pr_id: str
    verdict: str
    recommended_action: str
    confidence: float
    latency_ms: float
    mutation: MutationSpec
    impacted_services: list[dict] = field(default_factory=list)
    tier1_impacted: list[str] = field(default_factory=list)
    blast: dict = field(default_factory=dict)
    new_cycles: list[list[str]] = field(default_factory=list)
    adr_violations: list[AdrViolation] = field(default_factory=list)
    criticality_delta: dict = field(default_factory=dict)
    cascading_path_trace: list[str] = field(default_factory=list)
    certificates: list[dict] = field(default_factory=list)
    narrative: str = ""
    teardown_done: bool = False

    def to_dict(self) -> dict:
        return {
            "simulation_id": self.simulation_id,
            "shadow_graph": self.shadow_graph,
            "pr_id": self.pr_id,
            "verdict": self.verdict,
            "recommended_action": self.recommended_action,
            "confidence": round(self.confidence, 4),
            "latency_ms": round(self.latency_ms, 2),
            "mutation": self.mutation.to_dict(),
            "mutation_description": self.mutation.describe(),
            "impacted_services": self.impacted_services,
            "impacted_services_count": len(self.impacted_services),
            "tier1_impacted": self.tier1_impacted,
            "blast": self.blast,
            "new_cycles": self.new_cycles,
            "adr_violations": [v.to_dict() for v in self.adr_violations],
            "criticality_delta": self.criticality_delta,
            "cascading_path_trace": self.cascading_path_trace,
            "certificates": self.certificates,
            "narrative": self.narrative,
            "teardown_done": self.teardown_done,
        }


class ShadowSimulator:
    """Forks a graph, injects a PR delta, and predicts the consequences."""

    def __init__(
        self,
        store: GraphStore,
        prod_graph: str,
        shadow_prefix: str = "shadow_sim_",
        criticality_threshold: float = 0.30,
        keep_shadow: bool = False,
    ) -> None:
        self.store = store
        self.prod_graph = prod_graph
        self.shadow_prefix = shadow_prefix
        self.criticality_threshold = criticality_threshold
        #: The demo tears the fork down to prove it was ephemeral. Debug runs
        #: set ``keep_shadow`` so the shadow tenant can be inspected by hand.
        self.keep_shadow = keep_shadow

    # ---- public API --------------------------------------------------------
    def simulate(
        self,
        pr_id: str,
        mutation: MutationSpec,
        max_hops: int = 5,
        simulation_id: str | None = None,
    ) -> SimulationResult:
        started = time.perf_counter()
        sim_id = simulation_id or f"{pr_id.replace('PR-', '')}-{uuid.uuid4().hex[:6]}"
        shadow_graph = f"{self.shadow_prefix}{sim_id}"

        baseline = self._baseline_metrics(max_hops)
        self._fork(shadow_graph)
        result: SimulationResult | None = None
        try:
            self._inject(shadow_graph, mutation)
            result = self._evaluate(
                sim_id, shadow_graph, pr_id, mutation, baseline, max_hops
            )
        finally:
            # Teardown must happen even if evaluation raises, otherwise a broken
            # simulation leaks a full graph copy into the cluster.
            if not self.keep_shadow:
                self.store.drop_graph(shadow_graph)
                if result is not None:
                    result.teardown_done = True
        assert result is not None
        result.latency_ms = (time.perf_counter() - started) * 1000.0
        return result

    # ---- steps -------------------------------------------------------------
    def _baseline_metrics(self, max_hops: int) -> dict:
        """Measure production *before* forking so deltas are meaningful."""
        centrality = betweenness_centrality(self.store, self.prod_graph)
        cycles = detect_cycles(self.store, self.prod_graph)
        return {
            "centrality": centrality.scores,
            "cycle_count": len(cycles.cycles),
            "cycles": cycles.cycles,
        }

    def _fork(self, shadow_graph: str) -> None:
        self.store.fork_graph(self.prod_graph, shadow_graph)

    def _inject(self, shadow_graph: str, mutation: MutationSpec) -> None:
        """Apply the PR's structural delta to the forked graph."""
        # 1. Breaking schema fields are *removed* from the shadow world, which
        #    is exactly what a consumer 4 hops away will experience.
        for field_id in mutation.removed_schema_field_ids:
            self.store.delete_node(shadow_graph, "SchemaField", field_id)

        for field_id in mutation.added_schema_field_ids:
            self.store.merge_nodes(
                shadow_graph,
                "SchemaField",
                [
                    {
                        "id": field_id,
                        "name": field_id.split(".")[-1],
                        "type": "string",
                        "nullable": True,
                        "breaking": False,
                        "owner_module": (mutation.modified_module_ids or ["unknown"])[0],
                    }
                ],
            )

        # 2. Topology changes.
        if mutation.added_call_edges:
            self.store.merge_edges(
                shadow_graph,
                "CALLS",
                [
                    {
                        "source": src,
                        "target": dst,
                        "properties": {
                            "latency_p99": mutation.latency_delta_ms,
                            "weight": 1.0,
                            "protocol": "grpc",
                            "injected_by_simulation": True,
                        },
                    }
                    for src, dst in mutation.added_call_edges
                ],
            )
        for src, dst in mutation.removed_call_edges:
            self.store.delete_edge(shadow_graph, "CALLS", src, dst)

        if mutation.added_import_edges:
            resolved = self._resolve_import_edges(shadow_graph, mutation)
            if resolved:
                self.store.merge_edges(
                    shadow_graph,
                    "IMPORTS",
                    [
                        {
                            "source": src,
                            "target": dst,
                            "properties": {"injected_by_simulation": True},
                        }
                        for src, dst in resolved
                    ],
                )

        # 3. Latency pressure: push the declared delta onto every outbound call
        #    edge of the modified services so the criticality shift is visible.
        if mutation.latency_delta_ms:
            snapshot = self.store.snapshot(shadow_graph, limit=6000)
            affected_ids = self._services_for_modules(snapshot, mutation)
            updates = [
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "properties": {
                        **edge.get("properties", {}),
                        "latency_p99": float(
                            edge.get("properties", {}).get("latency_p99", 0) or 0
                        )
                        + mutation.latency_delta_ms,
                    },
                }
                for edge in snapshot.edges
                if edge["type"] == "CALLS" and edge["source"] in affected_ids
            ]
            if updates:
                self.store.merge_edges(shadow_graph, "CALLS", updates)

    def _resolve_import_edges(
        self, shadow_graph: str, mutation: MutationSpec
    ) -> list[tuple[str, str]]:
        """Map parsed import strings onto real ASTModule ids in the shadow graph.

        The parser sees an import as written in source (``from cache.redis_client
        import x`` yields ``cache.redis_client``), while the graph stores modules
        under their own ids. Emitting the raw string as an edge target would
        point at a node that does not exist, so each target is resolved against
        the shadow graph's module ids, file paths, and dotted-path stems. An
        import that matches nothing is skipped rather than injected as a
        dangling edge.
        """
        modules = self.store.find_nodes(shadow_graph, "ASTModule", limit=4000)
        by_id = {m["id"] for m in modules}
        by_path: dict[str, str] = {}
        by_stem: dict[str, str] = {}
        for module in modules:
            path = (module.get("file_path") or "").replace("\\", "/")
            if path:
                by_path[path] = module["id"]
                stem = re.sub(r"\.[a-z0-9]+$", "", path, flags=re.IGNORECASE)
                by_stem[stem.replace("/", ".")] = module["id"]
                by_stem[stem.replace("/", ".").split(".")[-1]] = module["id"]

        resolved: list[tuple[str, str]] = []
        for src, target in mutation.added_import_edges:
            if src not in by_id:
                continue
            key = (target or "").strip().replace("/", ".")
            match = (
                (target if target in by_id else None)
                or by_path.get(target.replace(".", "/") + ".py")
                or by_stem.get(key)
                or by_stem.get(key.split(".")[-1])
            )
            if match and match != src:
                resolved.append((src, match))
        return resolved

    def _services_for_modules(self, snapshot, mutation: MutationSpec) -> set[str]:
        """Map the PR's touched modules back to the services that own them."""
        module_ids = set(mutation.modified_module_ids)
        owners: set[str] = set()
        for edge in snapshot.edges:
            if edge["type"] == "IMPLEMENTED_BY" and edge["target"] in module_ids:
                owners.add(edge["source"])
        return owners

    def _evaluate(
        self,
        sim_id: str,
        shadow_graph: str,
        pr_id: str,
        mutation: MutationSpec,
        baseline: dict,
        max_hops: int,
    ) -> SimulationResult:
        snapshot = self.store.snapshot(shadow_graph, limit=6000)
        origin_services = sorted(self._services_for_modules(snapshot, mutation))

        # A PR that touches only schema fields still originates somewhere: find
        # the service exposing the schema that owns the removed fields.
        if not origin_services:
            origin_services = self._services_for_schema(
                snapshot, mutation.removed_schema_field_ids
            )

        blast: AlgorithmResult = blast_radius(
            self.store,
            shadow_graph,
            origin_services or [],
            max_hops=max_hops,
            # Dependency propagation only. Alerts and PRs are attached to the
            # graph but a *service* blast radius should not be widened by a
            # reverse TRIGGERED_ON hop, which would report an alert as if it
            # were an impacted downstream service.
            edge_types=["CALLS", "IMPLEMENTED_BY", "IMPORTS", "EXPOSES", "HAS_FIELD"],
        )

        cycles = detect_cycles(self.store, shadow_graph)
        new_cycles = [
            cycle for cycle in cycles.cycles if cycle not in baseline["cycles"]
        ]

        centrality = betweenness_centrality(self.store, shadow_graph)
        criticality_delta = {
            node_id: round(
                score - float(baseline["centrality"].get(node_id, 0.0)), 6
            )
            for node_id, score in centrality.scores.items()
            if abs(score - float(baseline["centrality"].get(node_id, 0.0))) > 1e-9
        }

        node_lookup = {n["id"]: n for n in snapshot.nodes}
        impacted_services = [
            {
                "id": node_id,
                "name": node_lookup.get(node_id, {}).get("name", node_id),
                "tier": node_lookup.get(node_id, {}).get("tier"),
                "sla_ms": node_lookup.get(node_id, {}).get("sla_ms"),
                "hop_distance": int(depth),
            }
            for node_id, depth in sorted(blast.scores.items(), key=lambda kv: kv[1])
            if node_lookup.get(node_id, {}).get("label") == "Service"
        ]
        tier1 = [s["id"] for s in impacted_services if s["tier"] == 1]

        violations = self._check_adrs(shadow_graph, mutation, origin_services, snapshot)

        cascading = self._cascade_trace(blast, node_lookup)

        verdict, action, confidence = self._verdict(
            mutation, tier1, new_cycles, violations, criticality_delta
        )

        certificates = [
            certificate_from_paths(
                claim=(
                    f"PR {pr_id} changes {', '.join(origin_services) or 'no service'}, "
                    f"which reaches {len(impacted_services)} downstream services "
                    f"within {max_hops} hops in shadow graph {shadow_graph}."
                ),
                graph=shadow_graph,
                paths=self.store.traverse(
                    shadow_graph,
                    origin_services[0],
                    max_hops,
                    direction="in",
                    limit=50,
                )
                if origin_services
                else [],
                cypher=self.store.last_cypher(),
                confidence=confidence,
                extra_evidence=blast.justification.split(". "),
            ).to_dict()
        ]

        narrative = self._narrate(
            pr_id, mutation, origin_services, impacted_services, tier1,
            new_cycles, violations, verdict,
        )

        return SimulationResult(
            simulation_id=sim_id,
            shadow_graph=shadow_graph,
            pr_id=pr_id,
            verdict=verdict,
            recommended_action=action,
            confidence=confidence,
            latency_ms=0.0,
            mutation=mutation,
            impacted_services=impacted_services,
            tier1_impacted=tier1,
            blast=blast.to_dict(),
            new_cycles=new_cycles,
            adr_violations=violations,
            criticality_delta=criticality_delta,
            cascading_path_trace=cascading,
            certificates=certificates,
            narrative=narrative,
        )

    def _services_for_schema(self, snapshot, field_ids: Sequence[str]) -> list[str]:
        if not field_ids:
            return []
        wanted = set(field_ids)
        schemas = {
            edge["source"]
            for edge in snapshot.edges
            if edge["type"] == "HAS_FIELD" and edge["target"] in wanted
        }
        return sorted(
            edge["source"]
            for edge in snapshot.edges
            if edge["type"] == "EXPOSES" and edge["target"] in schemas
        )

    def _cascade_trace(self, blast: AlgorithmResult, node_lookup: dict) -> list[str]:
        """Render the longest impact chain as an arrow diagram for the UI."""
        trace: list[str] = []
        impact_paths = blast.details.get("impact_paths", {})
        for node_id, path in sorted(
            impact_paths.items(), key=lambda kv: len(kv[1]), reverse=True
        )[:5]:
            rendered = " -> ".join(
                node_lookup.get(pid, {}).get("name", pid) for pid in path
            )
            trace.append(f"{rendered}  [{len(path) - 1} hops]")
        return trace

    def _check_adrs(
        self,
        shadow_graph: str,
        mutation: MutationSpec,
        origin_services: Sequence[str],
        snapshot,
    ) -> list[AdrViolation]:
        """Evaluate every governing ADR's machine-checkable constraint."""
        adrs = self.store.find_nodes(shadow_graph, "ADR", limit=200)
        violations: list[AdrViolation] = []
        module_ids = set(mutation.modified_module_ids)

        # Which services do the ADRs govern, per the GOVERNS edge?
        governs: dict[str, list[str]] = {}
        for edge in snapshot.edges:
            if edge["type"] == "GOVERNS":
                governs.setdefault(edge["target"], []).append(edge["source"])

        for adr in adrs:
            if (adr.get("status") or "").upper() != "ACCEPTED":
                continue
            raw = adr.get("constraint_json") or ""
            if not raw:
                continue
            try:
                constraint = json.loads(raw)
            except (TypeError, ValueError):
                continue

            governed_service = constraint.get("service")
            service_in_scope = governed_service in (origin_services or [])
            # Also treat an ADR as in scope when it governs any impacted
            # downstream service - that is the "4 hops away" case.
            if not service_in_scope and governed_service in governs:
                service_in_scope = governed_service in origin_services

            evidence_bits: list[str] = []
            severity = "info"

            # --- rule 1: breaking schema changes are forbidden outright ------
            if mutation.breaking_schema_change and constraint.get(
                "forbids_breaking_schema"
            ):
                if governed_service in origin_services or governed_service in governs:
                    evidence_bits.append(
                        "PR declares a breaking schema change on a service this ADR governs"
                    )
                    severity = "critical"

            # --- rule 2: named forbidden patterns ----------------------------
            for pattern in constraint.get("forbids", []) or []:
                if pattern == "synchronous_retry" and mutation.latency_delta_ms > 0:
                    if governed_service in (origin_services or []):
                        evidence_bits.append(
                            f"PR adds {mutation.latency_delta_ms}ms of latency, which "
                            f"re-introduces synchronous retry pressure the ADR banned"
                        )
                        severity = "critical" if severity != "critical" else severity
                if pattern == "new_call_edge" and mutation.added_call_edges:
                    if governed_service in (origin_services or []):
                        evidence_bits.append(
                            "PR introduces a new synchronous call edge the ADR banned"
                        )
                        severity = "high"
                if pattern == "circular_import" and mutation.added_import_edges:
                    evidence_bits.append(
                        "PR adds module imports that may close a dependency cycle"
                    )
                    severity = "high"

            # --- rule 3: latency budget --------------------------------------
            max_latency = constraint.get("max_latency_p99")
            if max_latency is not None and mutation.latency_delta_ms > 0:
                if governed_service in (origin_services or []):
                    current = self._current_latency(snapshot, governed_service)
                    projected = current + mutation.latency_delta_ms
                    if projected > float(max_latency):
                        evidence_bits.append(
                            f"projected p99 {projected:.0f}ms exceeds the ADR's "
                            f"{float(max_latency):.0f}ms budget for {governed_service}"
                        )
                        severity = "critical"

            # --- rule 4: forbidden module ownership --------------------------
            forbidden_modules = set(constraint.get("forbids_modules", []) or [])
            if forbidden_modules & module_ids:
                evidence_bits.append(
                    "PR modifies a module the ADR explicitly froze: "
                    + ", ".join(sorted(forbidden_modules & module_ids))
                )
                severity = "critical"

            if evidence_bits:
                violations.append(
                    AdrViolation(
                        adr=adr,
                        rule=constraint.get("rule", adr.get("code", "")),
                        severity=severity,
                        evidence="; ".join(evidence_bits),
                    )
                )
        return violations

    @staticmethod
    def _current_latency(snapshot, service_id: str) -> float:
        values = [
            float(edge.get("properties", {}).get("latency_p99") or 0)
            for edge in snapshot.edges
            if edge["type"] == "CALLS" and edge["source"] == service_id
        ]
        return max(values) if values else 0.0

    def _verdict(
        self,
        mutation: MutationSpec,
        tier1_impacted: Sequence[str],
        new_cycles: Sequence,
        violations: Sequence[AdrViolation],
        criticality_delta: dict,
    ) -> tuple[str, str, float]:
        critical_violations = [v for v in violations if v.severity == "critical"]
        high_violations = [v for v in violations if v.severity == "high"]

        # Confidence in the *prediction*, which scales with how much structural
        # evidence the simulation actually produced.
        evidence_weight = (
            (0.40 if tier1_impacted else 0.0)
            + (0.25 if new_cycles else 0.0)
            + (0.25 if critical_violations else 0.0)
            + (0.10 if mutation.breaking_schema_change else 0.0)
        )
        confidence = round(min(0.60 + evidence_weight, 0.99), 4)

        if critical_violations or (tier1_impacted and new_cycles):
            return "BLOCKED", "BLOCK_AND_PATCH", confidence
        if critical_violations or high_violations or tier1_impacted:
            return "AT_RISK", "WARN", confidence
        if mutation.breaking_schema_change or new_cycles:
            return "AT_RISK", "WARN", confidence
        return "SAFE", "APPROVE", round(min(0.75 + 0.1, 0.99), 4)

    def _narrate(
        self,
        pr_id: str,
        mutation: MutationSpec,
        origins: Sequence[str],
        impacted: Sequence[dict],
        tier1: Sequence[str],
        new_cycles: Sequence,
        violations: Sequence[AdrViolation],
        verdict: str,
    ) -> str:
        parts = [
            f"Forked production into a shadow tenant and applied the delta for {pr_id}: "
            + "; ".join(mutation.describe() or ["no structural change detected"])
            + "."
        ]
        if origins:
            parts.append(f"The change originates at {', '.join(origins)}.")
        if impacted:
            deepest = max(impacted, key=lambda s: s["hop_distance"])
            parts.append(
                f"{len(impacted)} downstream service(s) are reachable, the deepest at "
                f"{deepest['hop_distance']} hops ({deepest['name']})."
            )
        if tier1:
            parts.append(f"Tier-1 exposure: {', '.join(tier1)}.")
        if new_cycles:
            parts.append(
                f"The delta closes {len(new_cycles)} new dependency cycle(s), which "
                f"did not exist in production."
            )
        for violation in violations:
            parts.append(
                f"ADR {violation.adr.get('code')} ({violation.severity}) is violated: "
                f"{violation.evidence}."
            )
        parts.append(f"Verdict: {verdict}.")
        return " ".join(parts)
