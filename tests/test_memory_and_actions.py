"""Tri-tier memory tests: episodic recall, procedural playbooks, agent state."""

from __future__ import annotations

import pytest

from src.actions.remediation import ActionRefused, RemediationExecutor
from src.memory.memory import MemoryStore, structural_signature
from src.memory.proof import ProofCertificate, certificate_from_paths


def _paths(store, graph, start, hops=3, direction="out"):
    return store.traverse(graph, start, hops, direction=direction, limit=40)


# ---------------------------------------------------------------------------
# Structural signatures
# ---------------------------------------------------------------------------


def test_signature_is_order_independent(seeded):
    """The same evidence in a different order must produce the same signature."""
    store, settings = seeded
    paths = _paths(store, settings.prod_graph, "SVC-AUTH", direction="in")
    assert len(paths) > 1
    assert structural_signature(paths) == structural_signature(list(reversed(paths)))


def test_signature_includes_identity_tokens(seeded):
    """Shape alone cannot separate incidents in a same-shaped estate.

    Every service-to-service call yields the token ``Service:CALLS>Service``, so
    without identity tokens every incident would score identically.
    """
    store, settings = seeded
    paths = _paths(store, settings.prod_graph, "SVC-AUTH", direction="in")
    signature = structural_signature(paths)
    assert "Service@AuthService" in signature or "Service@SVC-AUTH" in signature


def test_different_services_produce_different_signatures(seeded):
    store, settings = seeded
    auth = structural_signature(
        _paths(store, settings.prod_graph, "SVC-AUTH", direction="in")
    )
    pay = structural_signature(
        _paths(store, settings.prod_graph, "SVC-PAY", direction="in")
    )
    assert auth != pay


def test_similarity_is_bounded(seeded):
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    sig = structural_signature(
        _paths(store, settings.prod_graph, "SVC-PAY", direction="in")
    )
    memory.store_episode(
        incident_id="INC-TEST-1",
        summary="test",
        symptom_signature=sig,
        affected_service_ids=["SVC-PAY"],
    )
    matches = memory.retrieve_similar_episodes(sig, top_k=3)
    assert matches
    for match in matches:
        assert 0.0 <= match.similarity <= 1.0
    assert matches[0].similarity == 1.0, "identical signatures must score 1.0"


def test_retrieval_is_ranked_by_similarity(seeded):
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    pay_sig = structural_signature(
        _paths(store, settings.prod_graph, "SVC-PAY", direction="in")
    )
    auth_sig = structural_signature(
        _paths(store, settings.prod_graph, "SVC-AUTH", direction="in")
    )
    memory.store_episode("INC-PAY", "pay", pay_sig, ["SVC-PAY"])
    memory.store_episode("INC-AUTH", "auth", auth_sig, ["SVC-AUTH"])
    matches = memory.retrieve_similar_episodes(pay_sig, top_k=5)
    assert matches[0].incident["id"] == "INC-PAY"
    scores = [m.similarity for m in matches]
    assert scores == sorted(scores, reverse=True)


def test_retrieval_returns_nothing_for_an_empty_signature(seeded):
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    assert memory.retrieve_similar_episodes("", top_k=5) == []


# ---------------------------------------------------------------------------
# Episodic and procedural persistence
# ---------------------------------------------------------------------------


def test_store_and_read_episode(seeded):
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    memory.store_episode(
        incident_id="INC-X",
        summary="something broke",
        symptom_signature="Service|Service#tier1",
        affected_service_ids=["SVC-AUTH"],
        severity="SEV1",
        mttr_seconds=120,
    )
    stored = memory.store.find_nodes(
        settings.memory_graph, "EpisodicIncident", limit=50
    )
    node = next(n for n in stored if n["id"] == "INC-X")
    assert node["summary"] == "something broke"
    assert node["severity"] == "SEV1"


def test_playbook_recall_by_trigger_signature(seeded):
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    sig = "Service@PaymentAPI|Service:CALLS>Service|Service#tier1"
    memory.store_playbook(
        playbook_id="PB-TEST",
        name="restart the thing",
        trigger_signature=sig,
        action_steps=[{"step": 1, "action": "page_domain_expert", "target": "SVC-PAY"}],
        success_rate=0.9,
    )
    match = memory.retrieve_playbook(sig)
    assert match is not None
    assert match.playbook["id"] == "PB-TEST"
    assert match.similarity == 1.0


def test_reinforce_playbook_updates_success_rate(seeded):
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    memory.store_playbook(
        playbook_id="PB-R",
        name="r",
        trigger_signature="Service",
        action_steps=[],
        success_rate=0.5,
        uses=0,
    )
    first = memory.reinforce_playbook("PB-R", succeeded=True)
    assert first["uses"] == 1
    assert first["success_rate"] > 0.5
    second = memory.reinforce_playbook("PB-R", succeeded=False)
    assert second["uses"] == 2
    assert second["success_rate"] < first["success_rate"]


def test_episode_ids_are_not_overwritten(seeded):
    """Two incidents must never collapse into one node."""
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    memory.store_episode("INC-A", "first", "sig1", ["SVC-AUTH"])
    memory.store_episode("INC-B", "second", "sig2", ["SVC-PAY"])
    ids = {
        n["id"]
        for n in memory.store.find_nodes(
            settings.memory_graph, "EpisodicIncident", limit=50
        )
    }
    assert {"INC-A", "INC-B"} <= ids


# ---------------------------------------------------------------------------
# Multi-agent coordination state
# ---------------------------------------------------------------------------


def test_session_and_step_recording(seeded):
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    memory.open_session("SESS-T", active_agent="orchestrator")
    memory.record_step(
        session_id="SESS-T",
        step_index=1,
        agent_name="simulator",
        tool_called="fork_and_simulate_shadow_graph",
        cypher_executed="MATCH (n) RETURN n",
        observation="blocked",
        inspected_node_ids=["SVC-AUTH", "PR-812"],
        handoff_to="commander",
    )
    trace = memory.session_trace("SESS-T")
    # The handoff pre-creates the receiving agent's step, so the trace holds the
    # completed simulator step plus the commander's pending placeholder. The
    # handoff itself is an edge in the graph, asserted separately below.
    assert [t["agent_name"] for t in trace] == ["simulator", "commander"]
    assert trace[0]["tool_called"] == "fork_and_simulate_shadow_graph"
    assert trace[1]["tool_called"] == "pending"

    inspected = memory.store.snapshot(settings.memory_graph, limit=8000)
    inspected_edges = [
        e for e in inspected.edges if e["type"] == "INSPECTED_NODE"
    ]
    assert len(inspected_edges) == 2, "both inspected nodes must be linked"


def test_handoff_links_steps_in_order(seeded):
    store, settings = seeded
    memory = MemoryStore(store, settings.memory_graph)
    memory.open_session("SESS-H", active_agent="orchestrator")
    memory.record_step(
        "SESS-H", 1, "simulator", "t", "", "obs1", handoff_to="commander"
    )
    memory.record_step("SESS-H", 2, "commander", "t", "", "obs2")
    snapshot = memory.store.snapshot(settings.memory_graph, limit=8000)
    handoffs = [e for e in snapshot.edges if e["type"] == "HANDED_OFF_TO"]
    assert handoffs, "the first step must hand off to the second"


# ---------------------------------------------------------------------------
# Proof certificates and the action guard
# ---------------------------------------------------------------------------


def test_certificate_is_insufficient_without_evidence():
    cert = certificate_from_paths("claim", "g", paths=[])
    assert cert.verdict == "INSUFFICIENT_EVIDENCE"
    assert cert.confidence == 0.0


def test_certificate_confidence_rises_with_evidence(seeded):
    store, settings = seeded
    thin = certificate_from_paths(
        "c", settings.prod_graph, _paths(store, settings.prod_graph, "SVC-AUTH", 1)
    )
    thick = certificate_from_paths(
        "c", settings.prod_graph, _paths(store, settings.prod_graph, "SVC-AUTH", 4)
    )
    assert thick.confidence >= thin.confidence


def test_certificate_accepts_explicit_node_ids(seeded):
    cert = certificate_from_paths(
        "claim", "g", paths=[], node_ids=["A", "B"], confidence=0.9
    )
    assert cert.node_ids == ["A", "B"]
    assert cert.verdict == "PROVEN"


def test_executor_refuses_without_proof_paths():
    executor = RemediationExecutor(dry_run=True)
    cert = ProofCertificate(
        claim="c", verdict="PROVEN", confidence=0.99, graph="g", node_ids=[]
    )
    try:
        executor.block_ci_merge(
            target_id="PR-1",
            proof=cert,
            proof_path_ids=[],
            remediation_note="n",
        )
    except ActionRefused as exc:
        assert "proof" in str(exc).lower()
    else:
        raise AssertionError("executor must refuse an action with no proof")


def test_executor_refuses_below_threshold():
    """A PARTIAL certificate is not actionable, however many ids it carries."""
    executor = RemediationExecutor(dry_run=True)
    cert = ProofCertificate(
        claim="c", verdict="PARTIAL", confidence=0.4, graph="g", node_ids=["X"]
    )
    with pytest.raises(ActionRefused) as excinfo:
        executor.block_ci_merge(
            target_id="PR-1",
            proof=cert,
            proof_path_ids=["X"],
            remediation_note="n",
        )
    assert "PARTIAL" in str(excinfo.value)


def test_executor_refuses_ids_absent_from_the_certificate():
    """Proof ids must belong to the certificate, not be invented by the caller."""
    executor = RemediationExecutor(dry_run=True)
    cert = ProofCertificate(
        claim="c", verdict="PROVEN", confidence=0.99, graph="g", node_ids=["X"]
    )
    with pytest.raises(ActionRefused) as excinfo:
        executor.block_ci_merge(
            target_id="PR-1",
            proof=cert,
            proof_path_ids=["X", "NOT-IN-CERT"],
            remediation_note="n",
        )
    assert "NOT-IN-CERT" in str(excinfo.value)


def test_executor_allows_a_proven_action_and_stays_dry():
    executor = RemediationExecutor(dry_run=True)
    cert = ProofCertificate(
        claim="c", verdict="PROVEN", confidence=0.99, graph="g", node_ids=["X"]
    )
    result = executor.block_ci_merge(
        target_id="PR-1",
        proof=cert,
        proof_path_ids=["X"],
        remediation_note="n",
    )
    assert result.status == "DRY_RUN"
    assert result.certificate_id == cert.certificate_id
