"""The multi-agent swarm.

Coordination model: agents do **not** pass conversation transcripts to each
other. Each agent reads the graph, writes a :class:`ReasoningStep` node with
``INSPECTED_NODE`` edges to everything it looked at, and hands off by linking
``HANDED_OFF_TO`` to the next agent's step. The next agent therefore starts from
a precise set of node ids rather than a lossy summary - which is the difference
between real graph memory and a long prompt.

Tool access model: agents reach the graph **through the MCP server**, via
:class:`~src.mcp_server.client.GraphToolClient`. They do not import
``src.graph`` or ``src.actions`` to do their work. That matters for the claim
being made: "the agent calls graph algorithms as MCP tools" is a statement about
the execution path, and it is only true if the execution path actually goes
through the protocol. ``tests/test_mcp_routing.py`` asserts exactly that.

The swarm is intentionally *not* an LLM loop. Each agent is a deterministic
reasoner over graph algorithms, and the optional LLM layer only narrates. That
keeps the demo reproducible and makes every decision auditable; an LLM that
"decides" whether to block a merge is not something a reviewer can verify.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

from ..actions.remediation import ActionResult
from ..mcp_server.client import GraphToolClient
from ..memory.proof import (
    ProofCertificate,
    ReasoningTrace,
    certificate_from_paths,
)

if TYPE_CHECKING:  # annotation-only; importing these at runtime would bypass MCP
    from ..actions.shadow_sim import MutationSpec
    from ..config import Settings
    from ..graph.store import GraphStore


@dataclass
class AgentStepResult:
    """What one agent produced, plus the graph pointers it leaves behind."""

    agent: str
    action: str
    observation: str
    inspected_node_ids: list[str] = field(default_factory=list)
    tool: str = ""
    cypher: str = ""
    payload: dict = field(default_factory=dict)
    certificate: ProofCertificate | None = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "action": self.action,
            "observation": self.observation,
            "inspected_node_ids": self.inspected_node_ids,
            "tool": self.tool,
            "cypher": self.cypher,
            "payload": self.payload,
            "certificate": self.certificate.to_dict() if self.certificate else None,
        }


class SwarmContext:
    """Shared state for one investigation session."""

    def __init__(
        self,
        settings: Settings,
        store: GraphStore,
        session_id: str,
        client: GraphToolClient | None = None,
    ) -> None:
        self.settings = settings
        self.session_id = session_id
        # The single tool surface every agent uses. Bound to this session's
        # settings, so tenants cannot bleed between concurrent runs.
        #
        # The store handle is deliberately *not* kept on the context. Agents
        # hold only the client, so there is no attribute through which one could
        # reach the graph layer and quietly skip the protocol.
        self.tools = client or GraphToolClient(settings, store)
        self.trace = ReasoningTrace(session_id=session_id)
        self.step_counter = 0
        self.shared: dict[str, Any] = {}
        self.actions: list[ActionResult] = []
        self.tools.memory_read_write_coordinator(
            operation="session_open",
            session_id=session_id,
            agent_id="orchestrator",
        )

    def memory(
        self,
        operation: str,
        *,
        agent_id: str,
        symptom_signature: str = "",
        payload: dict | None = None,
    ) -> dict:
        """Call the Track 02 memory tool. All memory access goes through here."""
        return self.tools.memory_read_write_coordinator(
            operation=operation,
            session_id=self.session_id,
            agent_id=agent_id,
            symptom_signature=symptom_signature,
            payload=payload or {},
        )

    def record(self, result: AgentStepResult, handoff_to: str | None = None) -> dict:
        """Persist a step to the memory graph and append it to the trace."""
        self.step_counter += 1
        step = self.trace.add(
            agent=result.agent,
            action=result.action,
            observation=result.observation,
            tool=result.tool,
            cypher=result.cypher,
            inspected=result.inspected_node_ids,
            certificates=[result.certificate] if result.certificate else [],
        )
        self.memory(
            "handoff_agent_state",
            agent_id=result.agent,
            payload={
                "step_index": self.step_counter,
                "tool_called": result.tool,
                "cypher": result.cypher,
                "observation": result.observation,
                "inspected_node_ids": result.inspected_node_ids,
                "handoff_to": handoff_to,
                "context_summary": result.observation[:500],
            },
        )
        return step

    def close(self, status: str = "completed") -> None:
        self.memory("session_close", agent_id="orchestrator", payload={"status": status})


# ---------------------------------------------------------------------------
# Archaeologist - Track 03 specialist
# ---------------------------------------------------------------------------


class ArchaeologistAgent:
    """Answers "why does this exist?" and finds who actually knows the system."""

    name = "archaeologist"

    def __init__(self, context: SwarmContext) -> None:
        self.ctx = context

    def investigate(self, entity_id: str | None) -> AgentStepResult:
        if not entity_id:
            # Resolution failed. Say so plainly rather than investigating an
            # arbitrary entity, so the caller can see the query did not land.
            payload = {
                "entity_id": None,
                "narrative": (
                    "No entity in the graph matched that query. Name a service, "
                    "module, endpoint or decision record to trace."
                ),
                "timeline": [],
            }
            self.ctx.shared["provenance"] = payload
            self.ctx.shared["experts"] = []
            return AgentStepResult(
                agent=self.name,
                action="provenance(unresolved)",
                observation=payload["narrative"],
                tool="trace_company_decision_provenance",
                payload=payload,
            )

        # Track 03 through the MCP tool: one call returns the decision timeline
        # and the ranked experts, so the agent sees the same payload an external
        # MCP client would.
        result = self.ctx.tools.trace_company_decision_provenance(entity_id)
        provenance = {k: v for k, v in result.items() if k != "experts"}
        experts = result.get("experts", [])

        inspected = [entity_id] + [
            event["node_id"] for event in provenance.get("timeline", [])
        ]

        certificate = None
        timeline = provenance.get("timeline", [])
        if timeline:
            # A provenance walk is assembled from several independent hops
            # rather than one traversal, so the evidence is passed as explicit
            # node ids. Confidence scales with how much authored history exists.
            certificate = certificate_from_paths(
                claim=(
                    f"{entity_id} has {len(timeline)} recorded decision "
                    f"event(s) across PRs, discussions and ADRs."
                ),
                graph=self.ctx.settings.prod_graph,
                paths=[],
                node_ids=[event["node_id"] for event in timeline],
                cypher=self.ctx.tools.last_cypher(),
                confidence=min(0.55 + 0.08 * len(timeline), 0.95),
                extra_evidence=[
                    f"{event['event']}: {event['title']}" for event in timeline[:8]
                ],
            )

        observation = provenance.get("narrative", "")
        if experts:
            observation += (
                f" Best contact: {experts[0].get('handle')} "
                f"(score {experts[0].get('ownership_score', 0):.2f})."
            )

        self.ctx.shared["provenance"] = provenance
        self.ctx.shared["experts"] = experts

        return AgentStepResult(
            agent=self.name,
            action=f"trace_decision_provenance({entity_id})",
            observation=observation,
            inspected_node_ids=inspected,
            tool="trace_company_decision_provenance",
            cypher=self.ctx.tools.last_cypher(),
            payload={"provenance": provenance, "experts": experts},
            certificate=certificate,
        )


# ---------------------------------------------------------------------------
# Simulator - Track 01/02 specialist
# ---------------------------------------------------------------------------


class SimulatorAgent:
    """Runs graph algorithms and counterfactual shadow simulations."""

    name = "simulator"

    def __init__(self, context: SwarmContext) -> None:
        self.ctx = context

    def analyse_topology(self) -> AgentStepResult:
        settings = self.ctx.settings
        # Three separate MCP algorithm calls - the same ones an external client
        # would issue. PageRank finds the most load-bearing modules, betweenness
        # finds the single points of failure, and cycle detection finds import
        # loops that a change could close.
        centrality = self.ctx.tools.run_graph_topology_algorithm(
            "betweenness_centrality", subgraph_label="Service"
        )
        pagerank = self.ctx.tools.run_graph_topology_algorithm(
            "pagerank_criticality", subgraph_label="Service"
        )
        cycles = self.ctx.tools.run_graph_topology_algorithm(
            "cycle_detection", subgraph_label="ASTModule"
        )

        inspected = list(centrality.get("scores", {})) + list(pagerank.get("scores", {}))
        payload = {
            "betweenness_centrality": centrality,
            "pagerank_criticality": pagerank,
            "cycle_detection": cycles,
        }
        self.ctx.shared["topology"] = payload

        observation = " ".join(
            part
            for part in (
                centrality.get("justification", ""),
                pagerank.get("justification", ""),
                cycles.get("justification", ""),
            )
            if part
        )
        # Topology analysis is not derived from a single traversal, so the
        # evidence is the ranked nodes themselves. Without them the certificate
        # would carry no node ids and could never clear the action bar.
        evidence_ids = (
            list(centrality.get("scores", {}))[:10]
            + list(pagerank.get("scores", {}))[:10]
        )
        certificate = certificate_from_paths(
            claim=(
                f"Topology analysis identified {len(centrality.get('scores', {}))} "
                f"criticality-ranked nodes and {len(cycles.get('cycles', []))} cycle(s)."
            ),
            graph=settings.prod_graph,
            paths=[],
            node_ids=evidence_ids,
            cypher=self.ctx.tools.last_cypher(),
            confidence=0.9 if centrality.get("scores") else 0.0,
            extra_evidence=[
                centrality.get("justification", ""),
                cycles.get("justification", ""),
            ],
        )
        return AgentStepResult(
            agent=self.name,
            action="analyse_topology",
            observation=observation,
            inspected_node_ids=inspected,
            tool="run_graph_topology_algorithm",
            cypher=self.ctx.tools.last_cypher(),
            payload=payload,
            certificate=certificate,
        )

    def simulate_pr(
        self, pr_id: str, mutation: MutationSpec, max_hops: int = 5
    ) -> AgentStepResult:
        settings = self.ctx.settings
        # The whole counterfactual runs inside the MCP tool: fork, inject,
        # measure, tear down. The agent never touches the shadow tenant itself.
        result = self.ctx.tools.fork_and_simulate_shadow_graph(
            pr_number=pr_id,
            modified_modules=mutation.modified_module_ids,
            breaking_schema_changes=mutation.breaking_schema_change,
            simulated_latency_delta_ms=mutation.latency_delta_ms,
            removed_schema_field_ids=mutation.removed_schema_field_ids,
            added_schema_field_ids=mutation.added_schema_field_ids,
            added_call_edges=mutation.added_call_edges,
            removed_call_edges=mutation.removed_call_edges,
            added_import_edges=mutation.added_import_edges,
            max_hops=max_hops,
        )
        payload = result
        self.ctx.shared["simulation"] = payload

        inspected = [pr_id] + [s["id"] for s in result.get("impacted_services", [])]
        inspected += [v["adr"]["id"] for v in result.get("adr_violations", [])]

        # The proof path is a real traversal from the PR through the modules it
        # touches and out along the call graph - requested through MCP so the
        # evidence and the simulation come from the same protocol surface.
        path_trace = self.ctx.tools.graph_multi_hop_investigate(
            pr_id,
            max_hops=max_hops,
            relationship_filter=["MODIFIES", "IMPLEMENTED_BY", "CALLS"],
            direction="out",
        )
        certificate = certificate_from_paths(
            claim=(
                f"Shadow simulation of {pr_id} predicts {result.get('verdict')} with "
                f"{len(result.get('impacted_services', []))} impacted service(s)."
            ),
            graph=settings.prod_graph,
            paths=[],
            node_ids=list(dict.fromkeys(inspected)),
            cypher=self.ctx.tools.last_cypher(),
            confidence=float(result.get("confidence", 0.0)),
            extra_evidence=[result.get("narrative", "")],
        )
        payload["proof_trace_hops"] = len(path_trace.get("hop_paths", []))
        return AgentStepResult(
            agent=self.name,
            action=f"fork_and_simulate_shadow_graph({pr_id})",
            observation=result.get("narrative", ""),
            inspected_node_ids=inspected,
            tool="fork_and_simulate_shadow_graph",
            cypher=self.ctx.tools.last_cypher(),
            payload=payload,
            certificate=certificate,
        )


# ---------------------------------------------------------------------------
# Triage - Track 01 + 02 specialist
# ---------------------------------------------------------------------------


class TriageAgent:
    """Root-causes a live alert and recalls a procedural playbook."""

    name = "triage"

    def __init__(self, context: SwarmContext) -> None:
        self.ctx = context

    def investigate_alert(self, alert_id: str) -> AgentStepResult:
        settings = self.ctx.settings
        # One MCP call does the multi-hop root-cause walk and the episodic +
        # procedural recall, and returns them together.
        triage = self.ctx.tools.triage_live_incident(
            alert_id,
            window_hours=settings.recent_change_window_hours,
            max_hops=settings.max_hops,
        )
        self.ctx.shared["triage"] = triage

        signature = triage.get("signature", "")
        episodes = triage.get("episodes", [])
        playbook_match = triage.get("playbook_match")

        # Track 02 semantic memory: the invariants that apply to this service,
        # independent of which incident is in front of us. Episodic memory says
        # what happened last time; semantic memory says what must stay true.
        symptom = triage.get("symptom_service") or {}
        domain = _service_domain(symptom.get("id", ""))
        semantic = self.ctx.memory(
            "recall_semantic_rules",
            agent_id=self.name,
            payload={"domain": domain},
        )
        rules = semantic.get("rules", [])
        self.ctx.shared["semantic_rules"] = rules
        self.ctx.shared["semantic_domain"] = domain

        inspected = [alert_id]
        if symptom:
            inspected.append(symptom["id"])
        for suspect in triage.get("suspects", []):
            inspected.append(suspect["service"]["id"])
            if suspect.get("pr"):
                inspected.append(suspect["pr"]["id"])
        inspected += [r["id"] for r in rules]

        observation = triage.get("narrative", "")
        if episodes:
            best = episodes[0]
            observation += (
                f" Episodic memory matched {best['incident'].get('id')} at "
                f"{best['similarity']:.0%} structural similarity "
                f"(MTTR {best['incident'].get('mttr_seconds')}s)."
            )
        if playbook_match:
            observation += (
                f" Procedural playbook {playbook_match['playbook'].get('id')} "
                f"({playbook_match['playbook'].get('name')}) applies with "
                f"{playbook_match['similarity']:.0%} signature overlap and a "
                f"{float(playbook_match['playbook'].get('success_rate') or 0):.0%} "
                f"historical success rate."
            )
        if rules:
            observation += (
                f" Semantic memory holds {len(rules)} standing invariant(s) for "
                f"the {domain} domain: "
                + "; ".join(r["invariant_statement"] for r in rules[:2])
                + "."
            )

        payload = {
            "triage": triage,
            "episodes": episodes,
            "playbook_match": playbook_match,
            "semantic_rules": rules,
            "semantic_domain": domain,
        }
        self.ctx.shared["episodes"] = episodes
        self.ctx.shared["playbook_match"] = playbook_match

        suspects = triage.get("suspects", [])
        certificate = certificate_from_paths(
            claim=(
                f"Alert {alert_id} traces to "
                f"{suspects[0]['service'].get('name') if suspects else 'no suspect'}"
                f" with a structural signature matching {len(episodes)} past incident(s)"
                f" and {len(rules)} standing invariant(s)."
            ),
            graph=settings.prod_graph,
            paths=[],
            node_ids=list(dict.fromkeys(inspected)),
            cypher=self.ctx.tools.last_cypher(),
            confidence=0.92 if suspects else 0.35,
            extra_evidence=[triage.get("narrative", "")],
        )
        return AgentStepResult(
            agent=self.name,
            action=f"triage_incident({alert_id})",
            observation=observation,
            inspected_node_ids=inspected,
            tool="graph_multi_hop_investigate + memory_read_write_coordinator",
            cypher=self.ctx.tools.last_cypher(),
            payload=payload,
            certificate=certificate,
        )


# ---------------------------------------------------------------------------
# Commander - Track 01 executor
# ---------------------------------------------------------------------------


class CommanderAgent:
    """Decides whether to act, and acts only on proven evidence."""

    name = "commander"

    def __init__(self, context: SwarmContext) -> None:
        self.ctx = context

    def decide_and_act(
        self,
        focus_id: str,
        candidate_certificates: Sequence[ProofCertificate],
        planned_actions: Sequence[dict],
    ) -> AgentStepResult:
        settings = self.ctx.settings
        threshold = settings.confidence_threshold

        best = max(
            (c for c in candidate_certificates if c),
            key=lambda c: c.confidence,
            default=None,
        )

        decisions: list[str] = []
        executed: list[ActionResult] = []

        if best is None or best.confidence < threshold:
            decisions.append(
                f"No certificate reached the {threshold:.2f} confidence bar"
                + (f" (best was {best.confidence:.2f})." if best else ".")
                + " Escalating to a human instead of acting."
            )
            # Escalation is itself an action, but it is always safe, so it runs
            # on a lowered bar.
            if best is not None:
                expert = None
                experts = self.ctx.shared.get("experts") or []
                if experts:
                    expert = experts[0]
                executed.append(
                    self._call_action(
                        "page_domain_expert",
                        target_id=focus_id,
                        proof=best,
                        remediation_note=(
                            "Confidence below the autonomous action threshold; "
                            "routing to the domain expert for a human decision."
                        ),
                        expert_handle=(expert or {}).get("handle", ""),
                        team=(expert or {}).get("team", ""),
                    )
                )
        else:
            decisions.append(
                f"Certificate {best.certificate_id} cleared the bar at "
                f"{best.confidence:.2f} (>= {threshold:.2f}); executing "
                f"{len(planned_actions)} planned action(s)."
            )
            for action in planned_actions:
                outcome = self._execute(action, best)
                if outcome:
                    executed.append(outcome)

        self.ctx.actions.extend(executed)

        observation = " ".join(decisions)
        if executed:
            observation += " Actions: " + "; ".join(
                f"{a.action_type}->{a.status}" for a in executed
            )
        else:
            observation += " No action taken."

        payload = {
            "decisions": decisions,
            "actions": [a.to_dict() for a in executed],
            "threshold": threshold,
            "best_certificate": best.to_dict() if best else None,
        }
        self.ctx.shared["commander"] = payload

        return AgentStepResult(
            agent=self.name,
            action="decide_and_act",
            observation=observation,
            inspected_node_ids=list(best.node_ids) if best else [],
            tool="execute_verified_remediation",
            cypher=self.ctx.tools.last_cypher(),
            payload=payload,
            certificate=best,
        )

    def _call_action(
        self,
        action_type: str,
        *,
        target_id: str,
        proof: ProofCertificate,
        remediation_note: str,
        **extra,
    ) -> ActionResult:
        """Invoke the gated remediation tool over MCP.

        The certificate travels with the call, so the tool can enforce its own
        proof bar server-side. A refusal comes back as a structured result
        rather than an exception, because a refused action is a normal outcome
        the trace should record.
        """
        result = self.ctx.tools.execute_verified_remediation(
            action_type=action_type,
            target_id=target_id,
            proof_path_ids=proof.node_ids,
            remediation_note=remediation_note,
            claim=proof.claim,
            confidence=proof.confidence,
            certificate_id=proof.certificate_id,
            graph=proof.graph,
            expert_handle=extra.get("expert_handle", ""),
            team=extra.get("team", ""),
            stale_doc_ids=extra.get("stale_doc_ids"),
            patch_summary=extra.get("patch_summary", ""),
        )
        if result.get("refused"):
            return ActionResult(
                action_type=action_type,
                target_id=target_id,
                status="REFUSED",
                summary=f"Executor refused: {result.get('reason', '')}",
                dry_run=True,
                certificate_id=proof.certificate_id,
            )
        return ActionResult(
            action_type=result.get("action_type", action_type),
            target_id=result.get("target_id", target_id),
            status=result.get("status", "UNKNOWN"),
            summary=result.get("summary", ""),
            dry_run=bool(result.get("dry_run", True)),
            certificate_id=result.get("certificate_id", proof.certificate_id),
            external_ref=result.get("external_ref", ""),
            payload=result.get("payload", {}),
        )

    def _execute(self, action: dict, proof: ProofCertificate) -> ActionResult | None:
        kind = action.get("action_type") or action.get("type")
        target = action.get("target_id") or action.get("target") or ""
        note = action.get("remediation_note") or action.get("detail") or ""
        if kind not in {
            "create_revert_pr",
            "block_ci_merge",
            "apply_hotfix_patch",
            "sync_stale_adr_doc",
            "page_domain_expert",
            "quarantine_endpoint",
        }:
            return None
        try:
            return self._call_action(
                str(kind),
                target_id=target,
                proof=proof,
                remediation_note=note,
                patch_summary=action.get("patch_summary", ""),
                stale_doc_ids=action.get("stale_doc_ids"),
                expert_handle=action.get("expert_handle", ""),
                team=action.get("team", ""),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced in the trace
            return ActionResult(
                action_type=str(kind),
                target_id=target,
                status="REFUSED",
                summary=f"Executor refused: {exc}",
                dry_run=True,
                certificate_id=proof.certificate_id,
            )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class SwarmOrchestrator:
    """Drives the agents and owns the handoff sequence.

    Every ``run_*`` method accepts an optional ``on_event`` callback. The API
    layer uses it to stream agent activity to the UI over SSE, which is what
    makes the swarm's reasoning visible while it happens rather than only after
    the fact.
    """

    def __init__(
        self,
        settings: Settings,
        store: GraphStore,
    ) -> None:
        self.settings = settings
        self.store = store
        # One tool client per orchestrator. Sessions bind their own so tenant
        # settings cannot bleed, but the surface is identical.
        self.tools = GraphToolClient(settings, store)

    @staticmethod
    def _emit(on_event, kind: str, payload: dict) -> None:
        if on_event is None:
            return
        try:
            on_event({"kind": kind, **payload})
        except Exception:  # noqa: BLE001 - a broken listener must not break a run
            pass

    def new_context(self, session_id: str) -> SwarmContext:
        return SwarmContext(
            self.settings, self.store, session_id, client=self.tools
        )

    def run_future_simulation(
        self,
        pr_id: str,
        mutation: MutationSpec,
        session_id: str,
        max_hops: int = 5,
        block_on_risk: bool = True,
        on_event=None,
    ) -> dict:
        """Full pre-crime flow: simulate, then archaeology, then decide."""
        ctx = self.new_context(session_id)
        simulator = SimulatorAgent(ctx)
        archaeologist = ArchaeologistAgent(ctx)
        commander = CommanderAgent(ctx)

        steps: list[dict] = []
        self._emit(on_event, "session_start",
                   {"session_id": session_id, "scenario": "future_simulation",
                    "pr_id": pr_id})

        self._emit(on_event, "agent_start",
                   {"agent": simulator.name, "action": f"simulate({pr_id})"})
        sim_step = simulator.simulate_pr(pr_id, mutation, max_hops=max_hops)
        steps.append(ctx.record(sim_step, handoff_to="archaeologist"))
        self._emit(on_event, "agent_finish", {
            "agent": simulator.name,
            "observation": sim_step.observation,
            "inspected_node_ids": sim_step.inspected_node_ids,
            "certificate": sim_step.certificate.to_dict() if sim_step.certificate else None,
            "payload": sim_step.payload,
        })

        simulation = ctx.shared["simulation"]
        origin = (simulation.get("mutation") or {}).get("modified_module_ids") or []
        # The archaeologist's job here is the "why": which historical decision
        # governs the code the PR touches.
        focus = "SVC-AUTH"
        if origin:
            dependents = ctx.tools.resolve_dependents(origin)
            owners = dependents.get("dependents", [])
            focus = owners[0] if owners else focus

        self._emit(on_event, "agent_start",
                   {"agent": archaeologist.name, "action": f"provenance({focus})"})
        arch_step = archaeologist.investigate(focus)
        steps.append(ctx.record(arch_step, handoff_to="commander"))
        self._emit(on_event, "agent_finish", {
            "agent": archaeologist.name,
            "observation": arch_step.observation,
            "inspected_node_ids": arch_step.inspected_node_ids,
            "certificate": arch_step.certificate.to_dict() if arch_step.certificate else None,
            "payload": arch_step.payload,
        })

        planned: list[dict] = []
        if block_on_risk and simulation["verdict"] in ("BLOCKED", "AT_RISK"):
            planned.append(
                {
                    "action_type": "block_ci_merge",
                    "target_id": pr_id,
                    "remediation_note": (
                        f"Shadow simulation predicts {simulation['verdict']}: "
                        f"{len(simulation['impacted_services'])} service(s) "
                        f"impacted, "
                        f"{len(simulation['adr_violations'])} ADR violation(s)."
                    ),
                }
            )
        for violation in simulation["adr_violations"]:
            if violation["severity"] == "critical":
                planned.append(
                    {
                        "action_type": "sync_stale_adr_doc",
                        "target_id": violation["adr"]["id"],
                        "remediation_note": (
                            f"Re-sync {violation['adr']['code']} because "
                            f"{pr_id} contradicts it: {violation['evidence']}"
                        ),
                        "stale_doc_ids": [violation["adr"]["id"]],
                    }
                )

        certificates = [
            c
            for c in (sim_step.certificate, arch_step.certificate)
            if c is not None
        ]
        self._emit(on_event, "agent_start",
                   {"agent": commander.name, "action": "decide_and_act"})
        cmd_step = commander.decide_and_act(pr_id, certificates, planned)
        steps.append(ctx.record(cmd_step))
        self._emit(on_event, "agent_finish", {
            "agent": commander.name,
            "observation": cmd_step.observation,
            "inspected_node_ids": cmd_step.inspected_node_ids,
            "certificate": cmd_step.certificate.to_dict() if cmd_step.certificate else None,
            "payload": cmd_step.payload,
        })

        ctx.close("completed")
        result = {
            "session_id": session_id,
            "scenario": "future_simulation",
            "pr_id": pr_id,
            "simulation": simulation,
            "provenance": ctx.shared.get("provenance"),
            "experts": ctx.shared.get("experts"),
            "commander": ctx.shared.get("commander"),
            "steps": steps,
            "trace": ctx.trace.to_dict(),
            "actions": [a.to_dict() for a in ctx.actions],
        }
        self._emit(on_event, "session_end",
                   {"session_id": session_id, "verdict": simulation.get("verdict")})
        return result

    def run_live_incident(
        self, alert_id: str, session_id: str, on_event=None
    ) -> dict:
        """Full incident flow: topology, triage, episodic recall, act."""
        ctx = self.new_context(session_id)
        simulator = SimulatorAgent(ctx)
        triage_agent = TriageAgent(ctx)
        commander = CommanderAgent(ctx)

        steps: list[dict] = []
        self._emit(on_event, "session_start",
                   {"session_id": session_id, "scenario": "live_incident",
                    "alert_id": alert_id})

        self._emit(on_event, "agent_start",
                   {"agent": simulator.name, "action": "analyse_topology"})
        topo_step = simulator.analyse_topology()
        steps.append(ctx.record(topo_step, handoff_to="triage"))
        self._emit(on_event, "agent_finish", {
            "agent": simulator.name,
            "observation": topo_step.observation,
            "inspected_node_ids": topo_step.inspected_node_ids,
            "certificate": topo_step.certificate.to_dict() if topo_step.certificate else None,
            "payload": topo_step.payload,
        })

        self._emit(on_event, "agent_start",
                   {"agent": triage_agent.name, "action": f"triage({alert_id})"})
        triage_step = triage_agent.investigate_alert(alert_id)
        steps.append(ctx.record(triage_step, handoff_to="commander"))
        self._emit(on_event, "agent_finish", {
            "agent": triage_agent.name,
            "observation": triage_step.observation,
            "inspected_node_ids": triage_step.inspected_node_ids,
            "certificate": triage_step.certificate.to_dict() if triage_step.certificate else None,
            "payload": triage_step.payload,
        })

        planned: list[dict] = []
        playbook_match = ctx.shared.get("playbook_match")
        triage_payload = ctx.shared.get("triage") or {}
        suspects = triage_payload.get("suspects") or []
        primary = suspects[0] if suspects else None

        if playbook_match:
            import json as _json

            steps_json = playbook_match["playbook"].get("action_steps_json") or "[]"
            try:
                playbook_actions = _json.loads(steps_json)
            except (TypeError, ValueError):
                playbook_actions = []
            for action in playbook_actions:
                planned.append(
                    {
                        "action_type": action.get("action"),
                        "target_id": action.get("target"),
                        "remediation_note": (
                            f"Recalled from playbook "
                            f"{playbook_match['playbook'].get('id')}: "
                            f"{action.get('detail')}"
                        ),
                    }
                )

        if primary and primary.get("service"):
            # Only page an owner we have not already escalated to: the playbook
            # may well name the same target, and paging the same person twice
            # for one incident is noise, not thoroughness.
            already_paged = {
                action.get("target_id") for action in planned
                if action.get("action_type") == "page_domain_expert"
            }
            target = primary["service"]["id"]
            if target not in already_paged:
                planned.append(
                    {
                        "action_type": "page_domain_expert",
                        "target_id": target,
                        "remediation_note": (
                            f"Root-cause suspect for {alert_id}: "
                            + "; ".join(primary.get("reasons", []))
                        ),
                    }
                )

        certificates = [
            c
            for c in (topo_step.certificate, triage_step.certificate)
            if c is not None
        ]
        self._emit(on_event, "agent_start",
                   {"agent": commander.name, "action": "decide_and_act"})
        cmd_step = commander.decide_and_act(alert_id, certificates, planned)
        steps.append(ctx.record(cmd_step))
        self._emit(on_event, "agent_finish", {
            "agent": commander.name,
            "observation": cmd_step.observation,
            "inspected_node_ids": cmd_step.inspected_node_ids,
            "certificate": cmd_step.certificate.to_dict() if cmd_step.certificate else None,
            "payload": cmd_step.payload,
        })

        # Close the memory loop: write the new episode and reinforce the
        # playbook that was used, so the next occurrence is cheaper. Nothing is
        # written when the alert does not exist: an incident that never happened
        # must not become precedent for the next one.
        resolved = bool(triage_payload.get("alert"))
        signature = triage_payload.get("signature") or ""
        affected = [s["service"]["id"] for s in suspects[:3]]
        incident_id: str | None = None
        reinforced = None
        if resolved:
            # Derive the episode id from the session's unique tail, and skip any
            # id the memory graph already holds. Reusing a seeded id (or an id
            # that collapses to the same string) would silently overwrite
            # history, which is the one thing an episodic store must never do.
            existing = ctx.memory(
                "list_episodes",
                agent_id="orchestrator",
                payload={"limit": 500},
            )
            existing_episode_ids = set(existing.get("episode_ids", []))
            suffix = session_id.replace("SESS-", "").replace("-", "")[-6:].upper()
            incident_id = f"INC-{suffix}"
            attempt = 0
            while incident_id in existing_episode_ids:
                attempt += 1
                incident_id = f"INC-{suffix}{attempt}"
            ctx.memory(
                "store_episode",
                agent_id="orchestrator",
                symptom_signature=signature,
                payload={
                    "incident_id": incident_id,
                    "summary": triage_payload.get("narrative", ""),
                    "affected_service_ids": affected,
                    "severity": (triage_payload.get("alert") or {}).get("severity", "SEV2"),
                    "mttr_seconds": 0,
                    "source_alert": alert_id,
                },
            )
            if playbook_match:
                succeeded = any(a.status != "REFUSED" for a in ctx.actions)
                reinforced = ctx.memory(
                    "reinforce_playbook",
                    agent_id="orchestrator",
                    payload={
                        "playbook_id": playbook_match["playbook"]["id"],
                        "succeeded": succeeded,
                    },
                ).get("reinforced_playbook")

        ctx.close("completed")
        result = {
            "session_id": session_id,
            "scenario": "live_incident",
            "alert_id": alert_id,
            "topology": ctx.shared.get("topology"),
            "triage": triage_payload,
            "episodes": ctx.shared.get("episodes"),
            "playbook_match": playbook_match,
            "semantic_rules": ctx.shared.get("semantic_rules") or [],
            "semantic_domain": ctx.shared.get("semantic_domain") or "",
            "reinforced_playbook": reinforced,
            "new_episode_id": incident_id,
            "commander": ctx.shared.get("commander"),
            "steps": steps,
            "trace": ctx.trace.to_dict(),
            "actions": [a.to_dict() for a in ctx.actions],
        }
        self._emit(on_event, "session_end",
                   {"session_id": session_id,
                    "new_episode_id": incident_id,
                    "reinforced_playbook": reinforced})
        return result

    def run_archaeology(self, query: str, session_id: str, on_event=None) -> dict:
        """Answer "why does this exist?" for an entity named in a query."""
        ctx = self.new_context(session_id)
        archaeologist = ArchaeologistAgent(ctx)

        self._emit(on_event, "session_start",
                   {"session_id": session_id, "scenario": "archaeology",
                    "query": query})

        entity_id = self._resolve_entity(query)
        self._emit(on_event, "agent_start",
                   {"agent": archaeologist.name, "action": f"provenance({entity_id})"})
        step = archaeologist.investigate(entity_id)
        ctx.record(step)
        self._emit(on_event, "agent_finish", {
            "agent": archaeologist.name,
            "observation": step.observation,
            "inspected_node_ids": step.inspected_node_ids,
            "certificate": step.certificate.to_dict() if step.certificate else None,
            "payload": step.payload,
        })
        ctx.close("completed")

        result = {
            "session_id": session_id,
            "scenario": "archaeology",
            "query": query,
            "resolved_entity_id": entity_id,
            "provenance": ctx.shared.get("provenance"),
            "experts": ctx.shared.get("experts"),
            "steps": [step.to_dict()],
            "trace": ctx.trace.to_dict(),
            "actions": [],
        }
        self._emit(on_event, "session_end",
                   {"session_id": session_id, "resolved_entity_id": entity_id})
        return result

    def _resolve_entity(self, query: str) -> str | None:
        """Resolve a natural-language query to an entity id in the graph.

        Deliberately lexical rather than embedding-based: it is deterministic,
        needs no provider, and a wrong resolution is visible in the trace
        instead of silent.

        Matching runs in two passes, most specific first:
          1. an explicit node id appears verbatim in the query;
          2. a node's display name matches after suffix normalisation, so
             ``PaymentService`` in prose finds ``PaymentAPI``.

        Returns ``None`` when nothing matches. Falling back to "some service"
        would produce a confident, sourced, and entirely irrelevant answer.
        """
        # Resolution runs through the MCP catalog tool, so the swarm's only view
        # of the graph is the same one an external client gets.
        catalog = self.tools.graph_node_catalog()
        nodes = catalog.get("nodes", [])
        lowered = query.lower()

        # Pass 1: explicit ids.
        for node in nodes:
            if node["id"].lower() in lowered:
                return node["id"]

        # Pass 2: normalised name matching. Service names in prose rarely match
        # the graph's canonical name exactly ("PaymentService" vs "PaymentAPI"),
        # so both sides are reduced to a suffix-stripped stem before comparing.
        query_stems = _stems(lowered)
        best: tuple[float, str] | None = None
        for node in nodes:
            candidates = [
                node.get("name"),
                node.get("code"),
                node.get("key"),
                node.get("file_path"),
            ]
            for candidate in candidates:
                if not candidate:
                    continue
                stem = _normalise(str(candidate))
                if not stem:
                    continue
                overlap = query_stems & _stems(stem)
                if not overlap:
                    continue
                # Score: prefer longer stems and services, so "paymentapi"
                # beats a stray file-path fragment.
                score = float(len(stem)) + (10.0 if node.get("label") == "Service" else 0.0)
                if best is None or score > best[0]:
                    best = (score, node["id"])
        if best:
            return best[1]
        return None


#: Which semantic-memory domain a service's invariants live in. Mirrors
#: ``src.seed_data.SERVICE_DOMAINS`` so a live triage resolves the same domain a
#: seeded run does. Kept local rather than imported so the swarm does not reach
#: into the seeder for runtime behaviour.
_SERVICE_DOMAINS: dict[str, str] = {
    "SVC-AUTH": "auth",
    "SVC-PAY": "payments",
    "SVC-BILL": "payments",
    "SVC-STRIPE": "payments",
    "SVC-CACHE": "architecture",
    "SVC-ORDER": "architecture",
    "SVC-USER": "architecture",
    "SVC-ANALYTICS": "architecture",
    "SVC-RISK": "reliability",
    "SVC-NOTIF": "reliability",
    "SVC-SEARCH": "reliability",
    "SVC-EMAIL": "reliability",
}


def _service_domain(service_id: str) -> str:
    """Domain whose semantic rules constrain a service.

    Falls back to the broad ``architecture`` domain, whose invariants are the
    ones that hold everywhere, rather than to an empty result that would silently
    report "no constraints apply".
    """
    return _SERVICE_DOMAINS.get(service_id, "architecture")


#: Generic suffixes that carry no discriminating meaning in a service name.
_GENERIC_SUFFIXES = (
    "service",
    "api",
    "worker",
    "gateway",
    "dispatcher",
    "ingest",
    "indexer",
    "sender",
    "webhook",
    "hook",
    "scoring",
    "client",
    "cache",
    "handler",
    "manager",
)


def _normalise(text: str) -> str:
    """Lowercase, strip non-alphanumerics, and drop a trailing generic suffix."""
    cleaned = re.sub(r"[^a-z0-9]", "", text.lower())
    for suffix in _GENERIC_SUFFIXES:
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 2:
            return cleaned[: -len(suffix)]
    return cleaned


def _stems(text: str) -> set[str]:
    """Split text into normalised word-stems for overlap comparison."""
    words = re.findall(r"[a-z0-9_/\.]+", text.lower())
    stems: set[str] = set()
    for word in words:
        stem = _normalise(word)
        if len(stem) >= 4:
            stems.add(stem)
    return stems
