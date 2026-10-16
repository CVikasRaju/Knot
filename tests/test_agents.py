"""End-to-end tests for the three swarm scenarios.

These exercise the whole stack - graph traversal, topology algorithms, shadow
simulation, memory recall, remediation - through the same entry points the API
and the MCP server call. Assertions deliberately target the *behaviour* the
demo claims (blocks an unsafe PR, finds a real upstream cause, cites the right
decision) rather than internal shapes, so they stay meaningful as the internals
evolve.
"""

from __future__ import annotations

import pytest

from src.actions.diff_parser import build_mutation_spec
from src.agents import SwarmOrchestrator
from src.seed_data import PR_812_DIFF


def _orchestrator(store, settings) -> SwarmOrchestrator:
    return SwarmOrchestrator(settings, store)


def _mutation(store, settings, pr_id="PR-812"):
    modules = store.find_nodes(settings.prod_graph, "ASTModule", limit=500)
    fields = store.find_nodes(settings.prod_graph, "SchemaField", limit=500)
    mutation, _ = build_mutation_spec(
        PR_812_DIFF, pr_id, modules, fields, latency_delta_ms=180
    )
    return mutation


# ---------------------------------------------------------------------------
# Future: pre-crime PR simulation
# ---------------------------------------------------------------------------


def test_future_simulation_blocks_pr_812(seeded, shadow_graphs_clean):
    store, settings = seeded
    result = _orchestrator(store, settings).run_future_simulation(
        "PR-812", _mutation(store, settings), "SESS-F1"
    )

    assert result["scenario"] == "future_simulation"
    assert result["simulation"]["verdict"] == "BLOCKED"
    assert result["simulation"]["recommended_action"] == "BLOCK_AND_PATCH"
    assert result["simulation"]["adr_violations"], "PR-812 must surface violations"


def test_future_simulation_emits_ordered_agent_events(seeded, shadow_graphs_clean):
    store, settings = seeded
    events: list[dict] = []
    _orchestrator(store, settings).run_future_simulation(
        "PR-812", _mutation(store, settings), "SESS-F2", on_event=events.append
    )
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "session_start"
    assert kinds[-1] == "session_end"
    started = [e["agent"] for e in events if e["kind"] == "agent_start"]
    assert started == ["simulator", "archaeologist", "commander"]


def test_future_simulation_every_agent_reports_an_observation(
    seeded, shadow_graphs_clean
):
    store, settings = seeded
    result = _orchestrator(store, settings).run_future_simulation(
        "PR-812", _mutation(store, settings), "SESS-F3"
    )
    steps = result["steps"]
    assert {s["agent"] for s in steps} == {"simulator", "archaeologist", "commander"}
    for step in steps:
        assert step["observation"], f"{step['agent']} produced no observation"


def test_future_simulation_tears_down_the_shadow_tenant(seeded, shadow_graphs_clean):
    store, settings = seeded
    result = _orchestrator(store, settings).run_future_simulation(
        "PR-812", _mutation(store, settings), "SESS-F4"
    )
    sim = result["simulation"]
    assert sim["impacted_services"], "a breaking auth change must have casualties"
    assert sim["teardown_done"] is True
    assert sim["shadow_graph"] not in store.list_graphs()


def test_future_simulation_surfaces_ownership_for_the_violated_adr(
    seeded, shadow_graphs_clean
):
    """Blocking a PR must name the humans who own the decision being broken."""
    store, settings = seeded
    result = _orchestrator(store, settings).run_future_simulation(
        "PR-812", _mutation(store, settings), "SESS-F5"
    )
    assert result["experts"], "the blocked PR must be routed to an owner"
    assert all("ownership_score" in e for e in result["experts"])


# ---------------------------------------------------------------------------
# Present: live incident triage and remediation
# ---------------------------------------------------------------------------


def test_live_incident_recalls_episodic_memory(seeded, shadow_graphs_clean):
    store, settings = seeded
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-L1")

    assert result["scenario"] == "live_incident"
    assert result["episodes"], "the seeded memory holds matching incidents"
    assert result["playbook_match"] is not None
    assert result["episodes"][0]["similarity"] > 0


def test_live_incident_triage_produces_a_proven_certificate(
    seeded, shadow_graphs_clean
):
    store, settings = seeded
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-L2")
    triage = next(s for s in result["steps"] if s["agent"] == "triage")
    assert triage["certificates"], "triage must attach proof"
    assert triage["certificates"][0]["verdict"] == "PROVEN"
    assert triage["certificates"][0]["node_ids"]


def test_live_incident_suspects_are_real_upstream_services(
    seeded, shadow_graphs_clean
):
    """The symptom service must never be reported as a cause of itself.

    A cyclic dependency means the upstream walk can return a path that loops
    back to the alerting service. Counting that final node put PaymentAPI in its
    own suspect list with a positive hop distance.
    """
    store, settings = seeded
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-L3")
    symptom_id = result["triage"]["symptom_service"]["id"]
    suspect_ids = {s["service"]["id"] for s in result["triage"]["suspects"]}
    assert symptom_id not in suspect_ids, (
        f"{symptom_id} is the symptom but appears as its own root cause"
    )


def test_live_incident_suspects_have_positive_hop_distance(
    seeded, shadow_graphs_clean
):
    store, settings = seeded
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-L4")
    for suspect in result["triage"]["suspects"]:
        assert suspect["hop_distance"] >= 1
        assert suspect["causal_chain"], "a suspect must carry its causal chain"


def test_live_incident_actions_are_unique(seeded, shadow_graphs_clean):
    """Paging the same target twice for one incident is noise."""
    store, settings = seeded
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-L5")
    pairs = [(a["action_type"], a["target_id"]) for a in result["actions"]]
    assert len(pairs) == len(set(pairs)), f"duplicate actions: {pairs}"


def test_live_incident_writes_a_new_episode(seeded, shadow_graphs_clean):
    store, settings = seeded
    before = {
        n["id"]
        for n in store.find_nodes(settings.memory_graph, "EpisodicIncident", limit=500)
    }
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-L6")
    after = {
        n["id"]
        for n in store.find_nodes(settings.memory_graph, "EpisodicIncident", limit=500)
    }
    new_ids = after - before
    assert new_ids, "resolving an incident must record a new episode"
    assert result["new_episode_id"] in new_ids


def test_live_incident_does_not_overwrite_a_seeded_episode(
    seeded, shadow_graphs_clean
):
    """A fresh episode id must not collide with seeded history.

    The first implementation derived the id from the last six characters of the
    session id, which collides with ``INC-2201`` whenever a session ends in
    those digits - and silently replaced a seeded incident.
    """
    store, settings = seeded
    summaries_before = {
        n["id"]: n.get("summary")
        for n in store.find_nodes(settings.memory_graph, "EpisodicIncident", limit=500)
    }
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-2201")

    assert result["new_episode_id"] not in summaries_before, (
        "the new episode reused a seeded id"
    )
    summaries_after = {
        n["id"]: n.get("summary")
        for n in store.find_nodes(settings.memory_graph, "EpisodicIncident", limit=500)
    }
    for incident_id, summary in summaries_before.items():
        assert summaries_after[incident_id] == summary, (
            f"seeded episode {incident_id} was overwritten"
        )


def test_live_incident_reinforces_the_matched_playbook(seeded, shadow_graphs_clean):
    store, settings = seeded
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-L7")
    assert result["reinforced_playbook"] is not None
    assert result["reinforced_playbook"]["uses"] >= 1


def test_live_incident_actions_all_carry_proof(seeded, shadow_graphs_clean):
    """No mutating action may be planned without node-level proof."""
    store, settings = seeded
    result = _orchestrator(store, settings).run_live_incident("ALT-904", "SESS-L8")
    assert result["actions"]
    for action in result["actions"]:
        assert action["certificate_id"], (
            f"{action['action_type']} was executed with no proof certificate"
        )
    commander = result["commander"]
    assert commander["best_certificate"] is not None
    assert commander["best_certificate"]["node_ids"]


def test_live_incident_unknown_alert_is_reported_not_silently_resolved(
    seeded, shadow_graphs_clean
):
    """An unknown alert must produce a clear answer and write no memory.

    Recording an episode for an alert that does not exist would make a
    non-incident into precedent for the next real one.
    """
    store, settings = seeded
    before = {
        n["id"]
        for n in store.find_nodes(settings.memory_graph, "EpisodicIncident", limit=500)
    }
    result = _orchestrator(store, settings).run_live_incident(
        "ALT-DOES-NOT-EXIST", "SESS-L9"
    )

    assert result["triage"]["alert"] is None
    assert "ALT-DOES-NOT-EXIST" in result["triage"]["narrative"]
    assert result["triage"]["suspects"] == []
    assert result["actions"] == []
    assert result["new_episode_id"] is None

    after = {
        n["id"]
        for n in store.find_nodes(settings.memory_graph, "EpisodicIncident", limit=500)
    }
    assert after == before, "an unresolvable alert must not write an episode"


# ---------------------------------------------------------------------------
# Past: decision archaeology
# ---------------------------------------------------------------------------


def _archaeology(store, settings, query, session_id="SESS-A1"):
    return _orchestrator(store, settings).run_archaeology(query, session_id)


def test_archaeology_resolves_a_natural_language_query(seeded):
    store, settings = seeded
    result = _archaeology(
        store, settings, "Why does PaymentService bypass Redis on retry?"
    )
    assert result["scenario"] == "archaeology"
    assert result["resolved_entity_id"] == "SVC-PAY"
    provenance = result["provenance"]
    assert provenance["entity_id"] == "SVC-PAY"
    assert provenance["timeline"], "a resolved entity must have a timeline"


def test_archaeology_timeline_has_no_duplicate_events(seeded):
    """The same ADR reachable by two routes must still appear once."""
    store, settings = seeded
    result = _archaeology(
        store, settings, "Why does PaymentService bypass Redis on retry?"
    )
    keys = [(e["event"], e["node_id"]) for e in result["provenance"]["timeline"]]
    assert len(keys) == len(set(keys)), f"duplicate timeline entries: {keys}"


def test_archaeology_cites_the_governing_decision(seeded):
    store, settings = seeded
    result = _archaeology(
        store, settings, "Why does PaymentService bypass Redis on retry?"
    )
    adrs = result["provenance"]["governing_adrs"]
    assert adrs, "the answer must cite the ADR that governs the decision"
    assert any(a["code"].startswith("ADR-") for a in adrs)


def test_archaeology_narrative_explains_the_decision(seeded):
    store, settings = seeded
    result = _archaeology(
        store, settings, "Why does PaymentService bypass Redis on retry?"
    )
    narrative = result["provenance"]["narrative"]
    assert narrative
    assert "ADR-" in narrative


def test_archaeology_ranks_the_actual_author_first(seeded):
    store, settings = seeded
    result = _archaeology(
        store, settings, "Why does PaymentService bypass Redis on retry?"
    )
    experts = result["experts"]
    assert experts
    assert experts[0]["handle"] == "@daniel", (
        "the author of the retry-bypass PR must rank first"
    )


def test_archaeology_handles_an_unresolvable_query(seeded):
    """An unknown entity must produce a clear answer, not an exception."""
    store, settings = seeded
    result = _archaeology(
        store, settings, "Why does the zzzz-nonexistent-service do the thing?"
    )
    assert result["resolved_entity_id"] is None
    assert result["provenance"]["narrative"]


def test_archaeology_records_a_session_trace(seeded):
    store, settings = seeded
    result = _archaeology(
        store, settings, "Why does PaymentService bypass Redis on retry?", "SESS-A9"
    )
    trace = result["trace"]
    assert trace["session_id"] == "SESS-A9"
    assert trace["steps"], "the session must record its reasoning steps"


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_scenarios_use_distinct_sessions(seeded, shadow_graphs_clean):
    store, settings = seeded
    orchestrator = _orchestrator(store, settings)
    first = orchestrator.run_live_incident("ALT-904", "SESS-X1")
    second = orchestrator.run_live_incident("ALT-904", "SESS-X2")
    assert first["session_id"] != second["session_id"]


def test_running_a_scenario_twice_is_idempotent(seeded, shadow_graphs_clean):
    """Re-running a demo must not corrupt the graph or the memory store."""
    store, settings = seeded
    orchestrator = _orchestrator(store, settings)
    first = orchestrator.run_live_incident("ALT-904", "SESS-Y1")
    prod_before = store.counts(settings.prod_graph)
    second = orchestrator.run_live_incident("ALT-904", "SESS-Y2")
    assert store.counts(settings.prod_graph) == prod_before
    assert first["new_episode_id"] != second["new_episode_id"]
