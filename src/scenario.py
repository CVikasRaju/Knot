"""Scenario runner - the command-line demo.

Three modes, matching the three hackathon tracks:

* ``future_sim``   - counterfactual shadow-graph simulation of an unmerged PR
* ``live_incident``- multi-hop root cause plus episodic memory recall
* ``archaeology``  - decision provenance for a "why does this exist?" question

Output is written as structured JSON so the same command can feed the UI, a
test assertion, or a judge's terminal.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from .actions.diff_parser import build_mutation_spec
from .agents.swarm import SwarmOrchestrator
from .config import get_settings
from .graph.store import open_store
from .seed_data import PR_402_DIFF, PR_812_DIFF

BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"


def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if _supports_color() else text


def _rule(title: str) -> None:
    print()
    print(_c(f"=== {title} ===", BOLD))


def _agent_line(agent: str, action: str) -> None:
    print(f"  {_c('[' + agent + ']', CYAN)} {action}")


def _print_event(event: dict) -> None:
    """Render a live swarm event to the terminal as it happens."""
    kind = event.get("kind")
    if kind == "session_start":
        print(f"{_c('SESSION', MAGENTA)} {event.get('session_id')} "
              f"({event.get('scenario')})")
    elif kind == "agent_start":
        _agent_line(event.get("agent", "?"), event.get("action", ""))
    elif kind == "agent_finish":
        agent = event.get("agent", "?")
        cert = event.get("certificate") or {}
        if cert:
            verdict = cert.get("verdict", "?")
            colour = {
                "PROVEN": GREEN,
                "PARTIAL": YELLOW,
                "INSUFFICIENT_EVIDENCE": RED,
            }.get(verdict, DIM)
            print(
                f"      {_c('proof', DIM)} {cert.get('certificate_id')} "
                f"{_c(verdict, colour)} conf={cert.get('confidence')} "
                f"hops={cert.get('hop_count')} nodes={len(cert.get('node_ids') or [])}"
            )
        observation = (event.get("observation") or "").strip()
        if observation:
            for line in _wrap(observation, 96):
                print(f"      {line}")
    elif kind == "session_end":
        print(f"{_c('SESSION COMPLETE', MAGENTA)} {event.get('session_id')}")
    elif kind == "error":
        print(_c(f"  ERROR: {event.get('error')}", RED))


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]


# ---------------------------------------------------------------------------
# Mode runners
# ---------------------------------------------------------------------------


def run_future_sim(args: argparse.Namespace, orchestrator: SwarmOrchestrator,
                   store) -> dict:
    settings = orchestrator.settings
    if args.diff_file:
        diff_text = open(args.diff_file, encoding="utf-8").read()
    elif args.pr_id == "PR-402":
        diff_text = PR_402_DIFF
    else:
        diff_text = PR_812_DIFF

    modules = store.find_nodes(settings.prod_graph, "ASTModule", limit=500)
    fields = store.find_nodes(settings.prod_graph, "SchemaField", limit=500)
    mutation, parsed = build_mutation_spec(
        diff_text,
        args.pr_id,
        modules,
        fields,
        declare_breaking=args.breaking,
        latency_delta_ms=args.latency_delta,
    )

    _rule(f"FUTURE SIMULATION - {args.pr_id}")
    print(f"{_c('parsed diff', DIM)} {len(parsed)} file(s): "
          + ", ".join(f.path for f in parsed))
    for line in mutation.describe():
        print(f"  {_c('delta', DIM)} {line}")

    session_id = args.session_id or f"SESS-{uuid.uuid4().hex[:8]}"
    result = orchestrator.run_future_simulation(
        args.pr_id,
        mutation,
        session_id,
        max_hops=args.max_hops,
        block_on_risk=not args.no_block,
        on_event=None if args.quiet else _print_event,
    )

    sim = result["simulation"]
    _rule("VERDICT")
    colour = {
        "BLOCKED": RED,
        "AT_RISK": YELLOW,
        "SAFE": GREEN,
    }.get(sim["verdict"], DIM)
    print(f"  {_c(sim['verdict'], colour)} -> recommended action "
          f"{_c(sim['recommended_action'], BOLD)} "
          f"(confidence {sim['confidence']}, {sim['latency_ms']}ms)")
    print(f"  shadow tenant {sim['shadow_graph']} "
          f"{'(torn down)' if sim['teardown_done'] else '(kept)'}")
    print(f"  impacted services: {sim['impacted_services_count']} "
          f"| tier-1: {sim['tier1_impacted'] or 'none'}")
    if sim["cascading_path_trace"]:
        print(f"  {_c('cascading path trace', DIM)}")
        for trace in sim["cascading_path_trace"]:
            print(f"    {trace}")
    if sim["adr_violations"]:
        print(f"  {_c('ADR violations', DIM)}")
        for violation in sim["adr_violations"]:
            print(f"    {_c(violation['adr']['code'], RED)} "
                  f"[{violation['severity']}] {violation['evidence']}")
    if sim["new_cycles"]:
        print(f"  {_c('new dependency cycles', RED)}: {sim['new_cycles']}")
    if result.get("experts"):
        print(f"  {_c('top ownership', DIM)}")
        for expert in result["experts"][:3]:
            print(f"    {expert['handle']:<10} {expert['ownership_score']:.3f}  "
                  f"{expert['explanation'][:80]}")
    if result.get("actions"):
        print(f"  {_c('actions', DIM)}")
        for action in result["actions"]:
            print(f"    {action['action_type']:<22} {action['status']:<8} "
                  f"{action['summary'][:80]}")
    return result


def run_live_incident(args: argparse.Namespace,
                      orchestrator: SwarmOrchestrator, store) -> dict:
    _rule(f"LIVE INCIDENT - {args.alert_id}")
    session_id = args.session_id or f"SESS-{uuid.uuid4().hex[:8]}"
    result = orchestrator.run_live_incident(
        args.alert_id,
        session_id,
        on_event=None if args.quiet else _print_event,
    )

    triage = result["triage"]
    _rule("ROOT CAUSE")
    if triage.get("alert"):
        print(f"  alert: {triage['alert'].get('summary')}")
    for suspect in triage.get("suspects", [])[:4]:
        service = suspect["service"]
        pr = suspect.get("pr") or {}
        author = (suspect.get("author") or {}).get("handle", "unknown")
        print(
            f"  {_c(service['name'], BOLD)} hop={suspect['hop_distance']} "
            f"score={suspect['score']:.2f} "
            f"pr={pr.get('id')} author={author}"
        )
        print(f"      chain: {' -> '.join(suspect['causal_chain'])}")
        print(f"      why:   {'; '.join(suspect['reasons'])}")
    if not triage.get("suspects"):
        print("  no recently-changed upstream service found")

    _rule("EPISODIC MEMORY RECALL")
    for match in result.get("episodes") or []:
        incident = match["incident"]
        print(f"  {_c(incident['id'], MAGENTA)} similarity={match['similarity']} "
              f"mttr={incident.get('mttr_seconds')}s")
        print(f"      {incident.get('summary')}")
    if not result.get("episodes"):
        print("  no similar past incident")

    match = result.get("playbook_match")
    if match:
        playbook = match["playbook"]
        print(f"  {_c('PLAYBOOK', MAGENTA)} {playbook['id']} "
              f"({playbook['name']}) overlap={match['similarity']} "
              f"success={playbook.get('success_rate')}")
        try:
            for step in json.loads(playbook.get("action_steps_json") or "[]"):
                print(f"      {step.get('step')}. {step.get('action')} "
                      f"-> {step.get('target')}: {step.get('detail')}")
        except (TypeError, ValueError):
            pass
    if result.get("reinforced_playbook"):
        print(f"  reinforced {result['reinforced_playbook']}")
    if result.get("new_episode_id"):
        print(f"  wrote new episode {_c(result['new_episode_id'], MAGENTA)}")

    _rule("SEMANTIC MEMORY (standing invariants)")
    rules = result.get("semantic_rules") or []
    domain = result.get("semantic_domain") or "this"
    if rules:
        print(f"  {len(rules)} rule(s) hold over the {domain} domain:")
        for rule in rules:
            print(f"      {rule['invariant_statement']}")
    else:
        print(f"  no standing invariant found for the {domain} domain")

    _rule("ACTIONS")
    for action in result.get("actions", []):
        print(f"  {action['action_type']:<22} {action['status']:<8} "
              f"cert={action['certificate_id']}")
        print(f"      {action['summary']}")
    return result


def run_archaeology(args: argparse.Namespace,
                    orchestrator: SwarmOrchestrator, store) -> dict:
    _rule("DECISION ARCHAEOLOGY")
    print(f"  query: {args.query}")
    session_id = args.session_id or f"SESS-{uuid.uuid4().hex[:8]}"
    result = orchestrator.run_archaeology(
        args.query,
        session_id,
        on_event=None if args.quiet else _print_event,
    )
    provenance = result.get("provenance") or {}
    print(f"  resolved to: {_c(result['resolved_entity_id'], BOLD)}")

    _rule("DECISION TIMELINE")
    for event in provenance.get("timeline", []):
        stamp = (event.get("timestamp") or "         ")[:10]
        print(f"  {stamp}  {event['event']:<14} {str(event['title'])[:70]}")
        if event.get("source_url"):
            print(f"              {_c(event['source_url'], DIM)}")

    _rule("WHY")
    for decision in provenance.get("decisions", []):
        for line in _wrap(decision, 96):
            print(f"  {line}")
    if not provenance.get("decisions"):
        print("  no governing decision recorded")

    _rule("WHO ACTUALLY KNOWS THIS")
    for expert in result.get("experts", [])[:5]:
        print(f"  {expert['handle']:<10} score={expert['ownership_score']:.3f} "
              f"team={expert.get('team')}")
        print(f"      {expert['explanation'][:110]}")
    return result


MODES = {
    "future_sim": run_future_sim,
    "live_incident": run_live_incident,
    "archaeology": run_archaeology,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a Knot demo scenario.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/run_scenario.py --mode future_sim --pr_id PR-812\n"
            "  python scripts/run_scenario.py --mode live_incident --alert_id ALT-904\n"
            "  python scripts/run_scenario.py --mode archaeology "
            '--query "Why does PaymentService bypass Redis on retry?"\n'
        ),
    )
    parser.add_argument("--mode", choices=sorted(MODES), default="future_sim")
    parser.add_argument("--pr_id", default="PR-812")
    parser.add_argument("--alert_id", default="ALT-904")
    parser.add_argument(
        "--query", default="Why does PaymentService bypass Redis on retry?"
    )
    parser.add_argument("--diff-file", default="")
    parser.add_argument("--breaking", action="store_true", default=False)
    parser.add_argument("--latency-delta", type=float, default=180.0)
    parser.add_argument("--max-hops", type=int, default=5)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--no-block", action="store_true")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress live agent events")
    parser.add_argument("--json", action="store_true",
                        help="emit the full result as JSON")
    args = parser.parse_args(argv)

    settings = get_settings()
    store = open_store(settings)
    orchestrator = SwarmOrchestrator(settings, store)

    if not args.quiet and not args.json:
        print(_c(f"Knot | backend={store.name} "
                 f"prod={settings.prod_graph} memory={settings.memory_graph}", DIM))

    result = MODES[args.mode](args, orchestrator, store)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
