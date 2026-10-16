# AGENTS.md — Knot repository guide

Persistent context for agents working in this repository. Read this before
making changes; it records the conventions and the non-obvious traps.

## What this project is

Knot (internally also called ChronosGraph) is an engineering-intelligence
engine backed by **FalkorDB**. It answers three questions over one property
graph:

- **Past** — why does this code exist? (decision provenance: code → commit → PR → ticket → Slack → ADR)
- **Present** — why is production broken right now? (multi-hop triage + episodic memory recall)
- **Future** — what breaks if we merge this PR? (shadow-graph fork + blast-radius simulation)

`src/` is the importable package. The distribution is never installed; code runs
from a checkout.

## Build, test, run

```bash
docker compose up -d falkordb          # REQUIRED before tests; they use the real engine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/seed_chronos_graph.py --reset
python -m pytest                       # 81 tests, ~13s
```

Useful entry points:

```bash
python scripts/run_scenario.py --mode future_sim   --pr_id PR-812 --breaking --latency-delta 180
python scripts/run_scenario.py --mode live_incident --alert_id ALT-904
python scripts/run_scenario.py --mode archaeology --query "Why does PaymentService bypass Redis on retry?"
python -m uvicorn src.api.main:app --reload --port 8000
python -m src.mcp_server.server
```

`make help` lists all of these.

## Architecture in one breath

CLI / REST API / MCP server → `SwarmOrchestrator` (4 agents) → graph layer
(`store.py` + `algorithms.py` + `ontology.py`) → FalkorDB.

Tenants are separate graphs in one FalkorDB instance: `prod_org_brain`,
`agent_memory`, and ephemeral `shadow_sim_<pr>-<hash>` graphs that are created
for a simulation and dropped after it.

## Critical conventions

**There is no LLM anywhere in this codebase, and that is deliberate.** No
provider SDK, no prompt, no embeddings. Every conclusion comes from a Cypher
traversal or a graph algorithm, and must ship with a proof certificate naming
the exact nodes and hops behind it. Do not add an LLM dependency to "improve"
reasoning — the auditability is the product. `_resolve_entity` is lexical for
this reason: deterministic, provider-free, and visibly wrong in the trace rather
than silently wrong.

**Never silently drop an edge.** `GraphStore.merge_edges` raises `GraphError`
when a row's endpoint is missing. This is intentional and load-bearing: the
original implementation ignored the count returned by FalkorDB and produced
graphs that *looked* populated while missing whole relationships. If you are
tempted to catch that error, you are re-introducing the bug it was written to
catch.

**Edges are registered per (type, target label) pair, not per type.**
`EDGE_BY_TYPE` uses first-declaration-wins (`setdefault`) because `AUTHORED`
legitimately targets both `PullRequest` and `ADR`. `EDGE_TARGETS` holds the
full set of legal targets for validation. When writing an edge whose type has
more than one declared target, pass `target_label=` explicitly or you will get
the wrong endpoint label. A target of `"*"` marks a polymorphic edge (e.g.
`INSPECTED_NODE`, which can point at any node) and *requires* an explicit
`target_label`.

**Node ids are unique across labels.** The ontology keys every node on `id`,
which is why `store.node_labels(graph, ids)` can resolve a label from an id
alone. `InMemoryStore` stores nodes as a dict keyed by id (not a list) — code
that iterates `_nodes` must use `.items()`/`.values()`.

**Traversals are always bounded.** Multi-hop explosion is the main failure mode
of graph agents. `CHRONOS_MAX_HOPS` and `CHRONOS_TRAVERSAL_BUDGET` exist so a
badly-connected graph cannot hang the swarm.

**Actions default to dry run.** `CHRONOS_ACTIONS_DRY_RUN=1` means the executor
reports what it would do without doing it. Mutating paths must stay behind
`execute_verified_remediation`, which refuses to run without `proof_path_ids`.

## Testing notes

Tests run against a **real FalkorDB**, not a mock, and use throwaway per-test
tenant names (`t_prod_*`, `t_mem_*`, `t_shadow_*`). If FalkorDB is unreachable
the suite skips rather than fails — a green run with skips is not a green run.
Check for `0 skipped` before trusting results.

Two bugs in this repo were only reachable through the real engine: the edge
count returned by FalkorDB's `UNWIND ... MERGE` (which is not the row count) and
`GRAPH.DELETE` semantics. Do not "fix" a failing test by mocking the store.

A fixture asserts that no test leaks a shadow tenant. If you add a code path
that forks a shadow graph, make sure teardown runs on every exit path.

That leak check diffs the full graph list before and after each test, so it is
**global to the FalkorDB instance, not scoped to the test's tenant prefix**. Two
suites running against the same instance at once will interfere: the other
run's shadow graph shows up mid-test and the check fails on a product that is
fine. Run one suite at a time, and do not run the demo scenarios or the API
while the suite is running.

## Gotchas that have already bitten us

- `localhost` can resolve to IPv6 `::1` inside containers while the published
  FalkorDB port is IPv4-only. The default host is `127.0.0.1` for this reason.
- `record_step` writes `INSPECTED_NODE` edges into the **memory** tenant, but
  the nodes it references live in **production**. They must be mirrored across
  tenants first, and grouped by label, or the audit trail silently comes back
  empty.
- Parsed import strings from a diff (`cache.redis_client`) are not node ids.
  `shadow_sim._resolve_import_edges` maps them to real `ASTModule` ids and skips
  anything it cannot resolve rather than injecting a dangling edge.
- `tree_sitter` is optional at runtime. `diff_parser` degrades to heuristics
  when the grammars are missing, so a passing test suite does not prove the AST
  path ran. Check `_TS_AVAILABLE` when debugging parse quality.
