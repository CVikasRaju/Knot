"""The multi-agent swarm.

Coordination model: agents do **not** pass conversation transcripts to each
other. Each agent reads the graph, writes a :class:`ReasoningStep` node with
``INSPECTED_NODE`` edges to everything it looked at, and hands off by linking
``HANDED_OFF_TO`` to the next agent's step. The next agent therefore starts from
a precise set of node ids rather than a lossy summary - which is the difference
between real graph memory and a long prompt.

The swarm is intentionally *not* an LLM loop. Each agent is a deterministic
reasoner over graph algorithms, and the optional LLM layer only narrates. That
keeps the demo reproducible and makes every decision auditable; an LLM that
"decides" whether to block a merge is not something a reviewer can verify.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..actions.expertise import ExpertiseRanker
from ..actions.remediation import ActionResult, RemediationExecutor
from ..actions.shadow_sim import MutationSpec, ShadowSimulator, SimulationResult
from ..config import Settings
from ..graph.algorithms import (
    AlgorithmResult,
    betweenness_centrality,
    detect_cycles,
    pagerank_criticality,
)
from ..graph.investigate import (
    InvestigationResult,
    ProvenanceResult,
    TriageResult,
    multi_hop_investigate,
    trace_decision_provenance,
    triage_incident,
)
from ..graph.store import GraphStore
from ..memory.memory import MemoryStore, structural_signature
from ..memory.proof import (
    ProofCertificate,
    ReasoningTrace,
    certificate_from_paths,
)


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
        memory: MemoryStore | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.session_id = session_id
        self.memory = memory or MemoryStore(store, settings.memory_graph, settings.prod_graph)
        self.trace = ReasoningTrace(session_id=session_id)
        self.step_counter = 0
        self.shared: dict[str, Any] = {}
        self.actions: list[ActionResult] = []
        self.memory.open_session(session_id, active_agent="orchestrator")

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
        self.memory.record_step(
            session_id=self.session_id,
            step_index=self.step_counter,
            agent_name=result.agent,
            tool_called=result.tool,
            cypher_executed=result.cypher,
            observation=result.observation,
            inspected_node_ids=result.inspected_node_ids,
            handoff_to=handoff_to,
            handoff_context=result.observation[:500],
        )
        return step

    def close(self, status: str = "completed") -> None:
        self.memory.close_session(self.session_id, status=status)


# ---------------------------------------------------------------------------
# Archaeologist - Track 03 specialist
# ---------------------------------------------------------------------------


class ArchaeologistAgent:
    """Answers "why does this exist?" and finds who actually knows the system."""

    name = "archaeologist"

    def __init__(self, context: SwarmContext) -> None:
        self.ctx = context
        self.ranker = ExpertiseRanker(context.store, context.settings.prod_graph)

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

        provenance: ProvenanceResult = trace_decision_provenance(
            self.ctx.store, self.ctx.settings.prod_graph, entity_id
        )
        inspected = [entity_id] + [
            event["node_id"] for event in provenance.timeline
        ]
        experts = self.ranker.rank(entity_id, top_k=5)

        certificate = None
        if provenance.timeline:
            # A provenance walk is assembled from several independent hops
            # rather than one traversal, so the evidence is passed as explicit
            # node ids. Confidence scales with how much authored history exists.
            certificate = certificate_from_paths(
                claim=(
                    f"{entity_id} has {len(provenance.timeline)} recorded decision "
                    f"event(s) across PRs, discussions and ADRs."
                ),
                graph=self.ctx.settings.prod_graph,
                paths=[],
                node_ids=[event["node_id"] for event in provenance.timeline],
                cypher=self.ctx.store.last_cypher(),
                confidence=min(0.55 + 0.08 * len(provenance.timeline), 0.95),
                extra_evidence=[
                    f"{event['event']}: {event['title']}"
                    for event in provenance.timeline[:8]
                ],
            )

        observation = provenance.narrative
        if experts:
            observation += (
                f" Best contact: {experts[0].person.get('handle')} "
                f"(score {experts[0].score:.2f})."
            )

        self.ctx.shared["provenance"] = provenance.to_dict()
        self.ctx.shared["experts"] = [e.to_dict() for e in experts]

        return AgentStepResult(
            agent=self.name,
            action=f"trace_decision_provenance({entity_id})",
            observation=observation,
            inspected_node_ids=inspected,
            tool="trace_company_decision_provenance",
            cypher=self.ctx.store.last_cypher(),
            payload={
                "provenance": provenance.to_dict(),
                "experts": [e.to_dict() for e in experts],
            },
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
        centrality: AlgorithmResult = betweenness_centrality(
            self.ctx.store, settings.prod_graph
        )
        pagerank: AlgorithmResult = pagerank_criticality(
            self.ctx.store, settings.prod_graph
        )
        cycles: AlgorithmResult = detect_cycles(
            self.ctx.store, settings.prod_graph
        )

        inspected = list(centrality.scores) + list(pagerank.scores)
        payload = {
            "betweenness_centrality": centrality.to_dict(),
            "pagerank_criticality": pagerank.to_dict(),
            "cycle_detection": cycles.to_dict(),
        }
        self.ctx.shared["topology"] = payload

        observation = (
            f"{centrality.justification} {pagerank.justification} "
            f"{cycles.justification}"
        )
        # Topology analysis is not derived from a single traversal, so the
        # evidence is the ranked nodes themselves. Without them the certificate
        # would carry no node ids and could never clear the action bar.
        evidence_ids = list(centrality.scores)[:10] + list(pagerank.scores)[:10]
        certificate = certificate_from_paths(
            claim=(
                f"Topology analysis identified {len(centrality.scores)} "
                f"criticality-ranked nodes and {len(cycles.cycles)} cycle(s)."
            ),
            graph=settings.prod_graph,
            paths=[],
            node_ids=evidence_ids,
            cypher=self.ctx.store.last_cypher(),
            confidence=0.9 if centrality.scores else 0.0,
            extra_evidence=[centrality.justification, cycles.justification],
        )
        return AgentStepResult(
            agent=self.name,
            action="analyse_topology",
            observation=observation,
            inspected_node_ids=inspected,
            tool="run_graph_topology_algorithm",
            cypher=self.ctx.store.last_cypher(),
            payload=payload,
            certificate=certificate,
        )

    def simulate_pr(
        self, pr_id: str, mutation: MutationSpec, max_hops: int = 5
    ) -> AgentStepResult:
        settings = self.ctx.settings
        simulator = ShadowSimulator(
            self.ctx.store,
            settings.prod_graph,
            settings.shadow_prefix,
            settings.criticality_alert_threshold,
        )
        result: SimulationResult = simulator.simulate(
            pr_id, mutation, max_hops=max_hops
        )
        payload = result.to_dict()
        self.ctx.shared["simulation"] = payload

        inspected = [pr_id] + [s["id"] for s in result.impacted_services]
        inspected += [v.adr["id"] for v in result.adr_violations]

        certificate = certificate_from_paths(
            claim=(
                f"Shadow simulation of {pr_id} predicts {result.verdict} with "
                f"{len(result.impacted_services)} impacted service(s)."
            ),
            graph=settings.prod_graph,
            paths=self.ctx.store.traverse(
                settings.prod_graph,
                pr_id,
                max_hops,
                relationship_types=["MODIFIES", "IMPLEMENTED_BY", "CALLS"],
                direction="out",
                limit=50,
            ),
            confidence=result.confidence,
            extra_evidence=[result.narrative],
        )
        return AgentStepResult(
            agent=self.name,
            action=f"fork_and_simulate_shadow_graph({pr_id})",
            observation=result.narrative,
            inspected_node_ids=inspected,
            tool="fork_and_simulate_shadow_graph",
            cypher=self.ctx.store.last_cypher(),
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
        triage: TriageResult = triage_incident(
            self.ctx.store,
            settings.prod_graph,
            alert_id,
            window_hours=settings.recent_change_window_hours,
        )
        self.ctx.shared["triage"] = triage.to_dict()

        # Episodic recall: match the structural signature against past incidents
        # and the procedural playbook derived from them.
        episodes = self.ctx.memory.retrieve_similar_episodes(triage.signature, top_k=3)
        playbook_match = self.ctx.memory.retrieve_playbook(triage.signature)

        inspected = [alert_id]
        if triage.symptom_service:
            inspected.append(triage.symptom_service["id"])
        for suspect in triage.suspects:
            inspected.append(suspect.service["id"])
            if suspect.pr:
                inspected.append(suspect.pr["id"])

        observation = triage.narrative
        if episodes:
            best = episodes[0]
            observation += (
                f" Episodic memory matched {best.incident.get('id')} at "
                f"{best.similarity:.0%} structural similarity "
                f"(MTTR {best.incident.get('mttr_seconds')}s)."
            )
        if playbook_match:
            observation += (
                f" Procedural playbook {playbook_match.playbook.get('id')} "
                f"({playbook_match.playbook.get('name')}) applies with "
                f"{playbook_match.similarity:.0%} signature overlap and a "
                f"{float(playbook_match.playbook.get('success_rate') or 0):.0%} "
                f"historical success rate."
            )

        payload = {
            "triage": triage.to_dict(),
            "episodes": [e.to_dict() for e in episodes],
            "playbook_match": playbook_match.to_dict() if playbook_match else None,
        }
        self.ctx.shared["episodes"] = [e.to_dict() for e in episodes]
        self.ctx.shared["playbook_match"] = (
            playbook_match.to_dict() if playbook_match else None
        )

        certificate = certificate_from_paths(
            claim=(
                f"Alert {alert_id} traces to "
                f"{triage.suspects[0].service.get('name') if triage.suspects else 'no suspect'}"
                f" with a structural signature matching {len(episodes)} past incident(s)."
            ),
            graph=settings.prod_graph,
            paths=self.ctx.store.traverse(
                settings.prod_graph,
                alert_id,
                max_hops=settings.max_hops,
                direction="out",
                limit=50,
            ),
            confidence=0.92 if triage.suspects else 0.35,
            extra_evidence=[triage.narrative],
        )
        return AgentStepResult(
            agent=self.name,
            action=f"triage_incident({alert_id})",
            observation=observation,
            inspected_node_ids=inspected,
            tool="graph_multi_hop_investigate + memory_read_write_coordinator",
            cypher=self.ctx.store.last_cypher(),
            payload=payload,
            certificate=certificate,
        )


# ---------------------------------------------------------------------------
# Commander - Track 01 executor
# ---------------------------------------------------------------------------


class CommanderAgent:
    """Decides whether to act, and acts only on proven evidence."""

    name = "commander"

    def __init__(self, context: SwarmContext, executor: RemediationExecutor) -> None:
        self.ctx = context
        self.executor = executor

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
                result = self.executor.page_domain_expert(
                    target_id=focus_id,
                    proof=best,
                    proof_path_ids=best.node_ids,
                    remediation_note=(
                        "Confidence below the autonomous action threshold; "
                        "routing to the domain expert for a human decision."
                    ),
                    expert_handle=(expert or {}).get("handle", ""),
                    team=(expert or {}).get("team", ""),
                )
                executed.append(result)
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
            cypher=self.ctx.store.last_cypher(),
            payload=payload,
            certificate=best,
        )

    def _execute(self, action: dict, proof: ProofCertificate) -> ActionResult | None:
        kind = action.get("action_type") or action.get("type")
        target = action.get("target_id") or action.get("target") or ""
        note = action.get("remediation_note") or action.get("detail") or ""
        kwargs = {
            "target_id": target,
            "proof": proof,
            "proof_path_ids": proof.node_ids,
            "remediation_note": note,
        }
        try:
            if kind == "create_revert_pr":
                return self.executor.create_revert_pr(**kwargs)
            if kind == "block_ci_merge":
                return self.executor.block_ci_merge(**kwargs)
            if kind == "apply_hotfix_patch":
                return self.executor.apply_hotfix_patch(
                    **kwargs, patch_summary=action.get("patch_summary", "")
                )
            if kind == "sync_stale_adr_doc":
                return self.executor.sync_stale_adr_doc(
                    **kwargs, stale_doc_ids=action.get("stale_doc_ids", [])
                )
            if kind == "page_domain_expert":
                return self.executor.page_domain_expert(
                    **kwargs,
                    expert_handle=action.get("expert_handle", ""),
                    team=action.get("team", ""),
                )
            if kind == "quarantine_endpoint":
                return self.executor.quarantine_endpoint(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced in the trace
            return ActionResult(
                action_type=str(kind),
                target_id=target,
                status="REFUSED",
                summary=f"Executor refused: {exc}",
                dry_run=True,
                certificate_id=proof.certificate_id,
            )
        return None


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
        executor: RemediationExecutor | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.executor = executor or RemediationExecutor(
            dry_run=settings.actions_dry_run
        )

    @staticmethod
    def _emit(on_event, kind: str, payload: dict) -> None:
        if on_event is None:
            return
        try:
            on_event({"kind": kind, **payload})
        except Exception:  # noqa: BLE001 - a broken listener must not break a run
            pass

    def new_context(self, session_id: str) -> SwarmContext:
        return SwarmContext(self.settings, self.store, session_id)

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
        commander = CommanderAgent(ctx, self.executor)

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
            owners = [
                edge["source"]
                for edge in self.store.snapshot(
                    self.settings.prod_graph, limit=8000
                ).edges
                if edge["type"] == "IMPLEMENTED_BY" and edge["target"] in origin
            ]
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
        commander = CommanderAgent(ctx, self.executor)

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
            existing_episode_ids = {
                row["id"]
                for row in ctx.memory.store.find_nodes(
                    self.settings.memory_graph, "EpisodicIncident", limit=500
                )
            }
            suffix = session_id.replace("SESS-", "").replace("-", "")[-6:].upper()
            incident_id = f"INC-{suffix}"
            attempt = 0
            while incident_id in existing_episode_ids:
                attempt += 1
                incident_id = f"INC-{suffix}{attempt}"
            ctx.memory.store_episode(
                incident_id=incident_id,
                summary=triage_payload.get("narrative", ""),
                symptom_signature=signature,
                affected_service_ids=affected,
                severity=(triage_payload.get("alert") or {}).get("severity", "SEV2"),
                mttr_seconds=0,
                extra={"source_alert": alert_id, "session_id": session_id},
            )
            if playbook_match:
                succeeded = any(a.status != "REFUSED" for a in ctx.actions)
                reinforced = ctx.memory.reinforce_playbook(
                    playbook_match["playbook"]["id"], succeeded
                )

        ctx.close("completed")
        result = {
            "session_id": session_id,
            "scenario": "live_incident",
            "alert_id": alert_id,
            "topology": ctx.shared.get("topology"),
            "triage": triage_payload,
            "episodes": ctx.shared.get("episodes"),
            "playbook_match": playbook_match,
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
        snapshot = self.store.snapshot(self.settings.prod_graph, limit=8000)
        lowered = query.lower()

        # Pass 1: explicit ids.
        for node in snapshot.nodes:
            if node["id"].lower() in lowered:
                return node["id"]

        # Pass 2: normalised name matching. Service names in prose rarely match
        # the graph's canonical name exactly ("PaymentService" vs "PaymentAPI"),
        # so both sides are reduced to a suffix-stripped stem before comparing.
        query_stems = _stems(lowered)
        best: tuple[float, str] | None = None
        for node in snapshot.nodes:
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
