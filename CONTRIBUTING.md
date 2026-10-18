# Contributing to Knot

Thanks for looking at this. Knot is an engineering-intelligence engine backed by
**FalkorDB**. Before you change anything, it is worth knowing what the project
answers and where the sharp edges are — most of them were found the hard way and
are written down here so you do not have to find them again.

If you only read one section, read [Design decisions that are load-bearing](#design-decisions-that-are-load-bearing).
Several of them look like things you should clean up, and all of them are
deliberate.

## What Knot is

One property graph, three questions:

- **Past** — why does this code exist? Code → commit → PR → ticket → Slack → ADR.
- **Present** — why is production broken right now? Multi-hop triage, then
  episodic memory recall for the fix.
- **Future** — what breaks if we merge this PR? Fork the graph, inject the
  mutation, measure the blast radius.

`src/` is the importable package. The distribution is never installed; everything
runs from a checkout.

## Getting set up

FalkorDB has to be running before the tests, because the tests use the real
engine:

```bash
docker compose up -d falkordb
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/seed_knot.py --reset
python -m pytest                       # 91 tests, ~19s
```

Entry points worth knowing:

```bash
python scripts/run_scenario.py --mode future_sim   --pr_id PR-812 --breaking --latency-delta 180
python scripts/run_scenario.py --mode live_incident --alert_id ALT-904
python scripts/run_scenario.py --mode archaeology --query "Why does PaymentService bypass Redis on retry?"
python -m uvicorn src.api.main:app --reload --port 8000
python -m src.mcp_server.server
```

The frontend needs the API on `:8000`:

```bash
cd frontend && npm install
npm run dev                            # :5173, proxies /api -> :8000
npx tsc --noEmit                       # typecheck; the build runs this too
npm run build                          # production bundle
```

`make help` lists all of these, including `make ui`, `make ui-install` and
`make ui-build`.

## How the pieces fit

```text
CLI / REST API / MCP server
        |
        v
SwarmOrchestrator (4 agents)
        |
        v
MCP tool client  (mcp_server/client.py)
        |
        v
MCP server       (mcp_server/server.py)
        |
        v
graph layer      (store.py + algorithms.py + ontology.py)
        |
        v
FalkorDB
```

**Agents reach the graph only through MCP.** `src/agents/swarm.py` must not
import `src.graph.*` or `src.actions.*` at runtime — annotation-only imports
under `if TYPE_CHECKING:` are fine. `tests/test_mcp_routing.py` enforces this
two ways: it parses the module's imports, and it asserts that a live run records
the tool calls it made. If you need a new graph operation, add an `@mcp.tool` in
`server.py`, add a thin method on `GraphToolClient`, and call it from the agent.
Do not reach under the client, even for something small.

Tenants are separate graphs inside one FalkorDB instance: `prod_org_brain`,
`agent_memory`, and the ephemeral `shadow_sim_<pr>-<hash>` graphs that a
simulation creates and drops.

## Design decisions that are load-bearing

Each of these looks like an oversight and is not. If you want to change one,
open an issue first and explain what breaks.

**There is no LLM in this codebase.** No provider SDK, no prompts, no
embeddings. Every conclusion comes from a Cypher traversal or a graph algorithm
and ships with a proof certificate naming the nodes and hops behind it. The
auditability is the product, not a side effect. `_resolve_entity` is lexical for
the same reason: it is deterministic, provider-free, and when it is wrong you
can see that in the trace instead of guessing.

**Never silently drop an edge.** `GraphStore.merge_edges` raises `GraphError`
when a row's endpoint is missing. That is intentional. An earlier version
ignored the count FalkorDB returned and produced graphs that *looked* populated
while whole relationships were missing. If you are tempted to catch that error,
you are putting the bug back.

**Edges are registered per `(type, target label)` pair, not per type.**
`EDGE_BY_TYPE` uses first-declaration-wins (`setdefault`) because `AUTHORED`
legitimately targets both `PullRequest` and `ADR`. `EDGE_TARGETS` holds the full
set of legal targets for validation. When you write an edge whose type has more
than one declared target, pass `target_label=` explicitly or you will get the
wrong endpoint label. A target of `"*"` marks a polymorphic edge — `INSPECTED_NODE`
can point at any node — and *requires* an explicit `target_label`.

**Node ids are unique across labels.** The ontology keys every node on `id`,
which is what lets `store.node_labels(graph, ids)` resolve a label from an id
alone. `InMemoryStore` keeps nodes in a dict keyed by id rather than a list, so
code that iterates `_nodes` has to use `.items()` or `.values()`.

**Traversals are always bounded.** Multi-hop explosion is the main failure mode
of a graph agent. `KNOT_MAX_HOPS` and `KNOT_TRAVERSAL_BUDGET` exist so a
badly-connected graph cannot hang the swarm.

**Actions default to dry run.** With `KNOT_ACTIONS_DRY_RUN=1` the executor
reports what it would do without doing it. Mutating paths stay behind
`execute_verified_remediation`, which refuses to run without `proof_path_ids`.

**FalkorDB is required.** `open_store()` raises `GraphUnavailable` when the
engine is unreachable. It falls back to the empty `InMemoryStore` only when
`allow_memory_fallback` is set, which the test fixture does explicitly. Do not
restore a silent fallback: the whole product is graph traversal, so degrading to
an empty graph would make it look like it works while answering every query with
nothing.

**The product name is Knot.** The earlier working title survives only as the
legacy `CHRONOS_*` environment prefix (see `_env_str` in `src/config.py`), kept
so existing `.env` files keep working. New code, docs and identifiers use
`KNOT_*`.

## Testing

Tests run against a real FalkorDB, not a mock, and use throwaway per-test tenant
names (`t_prod_*`, `t_mem_*`, `t_shadow_*`). If FalkorDB is unreachable the
suite skips rather than fails — so a green run with skips is not a green run.
Check for `0 skipped` before you trust the result.

Two bugs in this repo were only reachable through the real engine: the edge count
FalkorDB returns from `UNWIND ... MERGE` (which is not the row count) and
`GRAPH.DELETE` semantics. Please do not "fix" a failing test by mocking the store.

A fixture asserts that no test leaks a shadow tenant. If you add a code path that
forks a shadow graph, make sure teardown runs on every exit path.

That leak check diffs the full graph list before and after each test, so it is
**global to the FalkorDB instance, not scoped to a tenant prefix**. Two suites
running against the same instance interfere: the other run's shadow graph shows
up mid-test and the check fails on code that is fine. Run one suite at a time,
and do not run the demo scenarios or the API while the suite is running.

Set `KNOT_MEMORY_BACKEND=1` to run against the in-process backend instead; the
suite then skips rather than fails, since the contract tests only mean something
against the real engine.

## Traps worth knowing about

- `localhost` can resolve to IPv6 `::1` inside containers while the published
  FalkorDB port is IPv4-only. That is why the default host is `127.0.0.1`.
- `record_step` writes `INSPECTED_NODE` edges into the **memory** tenant, but
  the nodes they reference live in **production**. They have to be mirrored
  across tenants first, and grouped by label, or the audit trail silently comes
  back empty.
- Parsed import strings from a diff (`cache.redis_client`) are not node ids.
  `shadow_sim._resolve_import_edges` maps them to real `ASTModule` ids and skips
  anything it cannot resolve rather than injecting a dangling edge.
- `tree_sitter` is optional at runtime. `diff_parser` falls back to heuristics
  when the grammars are missing, so a passing suite does not prove the AST path
  ran. Check `_TS_AVAILABLE` when debugging parse quality.
- `frontend/src/lib/types.ts` is a hand-maintained mirror of the Python result
  dataclasses, and nothing enforces it. A field renamed on the API side will not
  fail the typecheck — it renders as `undefined` and throws inside the component.
  When a shape changes, diff a real response (a `python -c` one-liner against
  `SwarmOrchestrator`) against the interface before trusting it.
- Track values from the API are the full enum (`track_01_present`,
  `track_02_memory`, `track_03_company_brain`), not the short names. Keying the
  legend on `"present"` gives zero counts with no error.
- `ProceduralPlaybook.action_steps_json` is a JSON **string**, not an array. It
  has to be parsed, and the parse guarded, or a malformed value takes the whole
  result panel down.
- Expert records expose `ownership_score` / `signals` / `explanation`; triage
  suspects nest the service under `service` with `score` / `reasons`. Calling
  `.toFixed()` on a field that does not exist blanks the React tree instead of
  showing an error, so verify against a real payload.

## Pull requests

- Keep the change focused. One idea per PR.
- Run `python -m pytest` and `npx tsc --noEmit` before pushing, and say in the PR
  description what you ran and what you saw.
- If you change a result shape, update `frontend/src/lib/types.ts` in the same
  PR and check the rendered panel.
- If you change the ontology, `validate_schema()` has to stay clean, and the
  node and edge counts in `README.md` have to match it.
