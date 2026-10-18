# ⏳ Knot: Temporal-Predictive Engineering Graph

> **Built for GraphHacks (WeMakeDevs) | Powered by FalkorDB & Model Context Protocol (MCP)**

## 🏁 Track entered

**Track 01 — Agents That Act on Connected Data.**

Knot is entered in Track 01 alone. It also uses the capabilities of Track 02
(episodic, procedural and semantic agent memory) and Track 03 (a unified company
brain), because the three are not separable here: the memory that makes triage
fast is stored in the same graph as the company brain that answers *why*, and
both are read through the same traversals. Those two tracks are described below
as supporting capability, not as separate entries.

[![FalkorDB](https://img.shields.io/badge/Graph_Engine-FalkorDB-FF5722?style=for-the-badge)](https://www.falkordb.com/)
[![MCP](https://img.shields.io/badge/Protocol-Model_Context_Protocol-00ADD8?style=for-the-badge)](https://modelcontextprotocol.io/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI_%2B_SSE-009688?style=for-the-badge)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Core-Python_3.11_%2B_networkx-3776AB?style=for-the-badge)](https://www.python.org/)

---

## ⛓️ FalkorDB is load-bearing

This matters for judging, so it is stated plainly: **remove FalkorDB and Knot
does not work.** The graph is not a visualisation over ordinary storage, and
there is no non-graph path through the product.

- **The core feature is a graph operation.** Counterfactual simulation is
  `GRAPH.COPY` of the production tenant into a throwaway one, a mutation injected
  into the fork, then a traversal over the fork. There is no relational or
  document equivalent that keeps this cheap and non-destructive.
- **Multi-tenant forking is native.** `prod_org_brain`, `agent_memory` and
  `shadow_sim_<pr>-<hash>` are separate graphs *by name in one instance*.
  Simulation isolation is a property of the engine, not something the app
  emulates.
- **Every conclusion is a traversal or an algorithm.** Blast radius is a bounded
  BFS over the dependency digraph; triage is a typed multi-hop walk; provenance
  is a direction-changing hop chain; criticality is PageRank and betweenness.
  Nothing is derived from a flat table scan.
- **Proof is a graph path.** `execute_verified_remediation` refuses to act
  without a `proof_path_ids` array produced by a prior traversal, and the
  certificate embeds the Cypher that produced it. The evidence *is* the path.

Because of this, a missing FalkorDB is a **hard failure by default**. An earlier
version of `open_store()` silently degraded to an empty in-process graph, which
would have let Knot appear to run — answering every query with nothing — while
the engine was absent. That is exactly the "graph as decoration" failure mode, so
the fallback is now opt-in and documented in
[Configuration](#️-configuration).

---

## 🚀 Overview

Modern software organizations suffer from **structural amnesia** and **blind blast-radius deployments**. Code lives in GitHub, the reasoning behind it is buried in Slack threads and ADRs, ownership hides in tickets, and past incident fixes vanish when engineers leave.

**Knot** ties all of that into a single FalkorDB property graph and reasons over it with graph algorithms and bounded multi-hop Cypher traversals — exposed to any agent as native **MCP tools**. It unifies three time horizons:

| Dimension | What it does |
| :--- | :--- |
| **⏪ The Past** | **Decision provenance.** Walks backward from a service or module through commits, PRs, tickets and Slack threads to the ADR that explains *why* the code is shaped the way it is — then ranks who actually knows the system. |
| **⚡ The Present** | **Live multi-hop triage.** Runs PageRank / betweenness centrality / shortest-path over the live topology to find the real root cause of an alert, recalls a matching remediation playbook, and executes verified actions. |
| **🔮 The Future** | **Counterfactual simulation.** Forks the production graph into an ephemeral shadow tenant, injects an unmerged PR's AST changes, and predicts the cascading blast radius and ADR violations *before* the merge. |

> **Implementation note on determinism.** Knot deliberately contains **no LLM and no provider SDK**. Every conclusion is derived from a Cypher traversal or a graph algorithm, and every action ships with a **proof certificate** naming the exact nodes and hops that justified it. A finding is either backed by a reproducible path through the graph or it is not reported — which is what makes the output auditable rather than merely plausible.

---

## 🏗️ System Architecture

```text
+-------------------------------------------------------------------------------+
|  CLI (scripts/run_scenario.py)      REST API (FastAPI + SSE)   MCP server      |
|  three demo scenarios               /api/*  streaming traces   @mcp.tool       |
+-------------------------------------------+-----------------------------------+
                                            v
+-------------------------------------------------------------------------------+
|                    SWARM ORCHESTRATOR  (src/agents/swarm.py)                  |
|                                                                               |
|   ArchaeologistAgent    SimulatorAgent    TriageAgent    CommanderAgent        |
|   (Track 03)            (Track 01/02)     (Track 01)     (executor, gated)     |
+-------------------------------------------+-----------------------------------+
                                            v
+-------------------------------------------------------------------------------+
|                       GRAPH LAYER  (src/graph/)                               |
|   store.py       FalkorDBStore | InMemoryStore  (identical contract)           |
|   algorithms.py  pagerank_criticality, betweenness_centrality,                 |
|                  shortest_dependency_path, blast_radius, detect_cycles         |
|   ontology.py    3-track node/edge schema + validate_schema()                  |
+-------------------------------------------+-----------------------------------+
                                            v
+-------------------------------------------------------------------------------+
|                          FALKORDB  (multi-tenant by graph name)               |
|   prod_org_brain        agent_memory         shadow_sim_<pr_id>-<hash>         |
|   code, infra, people,  episodic + semantic  ephemeral PR fork, torn down      |
|   decisions, telemetry  + procedural memory  after every simulation            |
+-------------------------------------------------------------------------------+
```

Tenant isolation is the load-bearing idea: FalkorDB separates graphs by name inside one instance, so forking production for a simulation costs a graph copy instead of a second database.

---

## ✨ The Three Scenarios

### 1. `future_sim` — Pre-merge blast radius (Track 01)

A PR is opened. Knot forks the production graph into `shadow_sim_<pr>-<hash>`, parses the diff with `tree-sitter`, injects the changed modules / removed schema fields / new call edges, then:

- reverse-propagates from the modified services up to 5 hops to find every impacted consumer,
- runs **cycle detection** for newly introduced call loops,
- re-runs **PageRank and betweenness centrality** to see how criticality shifts,
- cross-references every `(:ADR)-[:GOVERNS]->` edge to catch policy violations,
- emits a verdict of `APPROVE`, `WARN`, or `BLOCK_AND_PATCH`, then drops the shadow tenant.

Real output for PR-812 (removes the `scope` claim from `AuthPayload`, adds a 180 ms retry path):

```text
=== VERDICT ===
  BLOCKED -> recommended action BLOCK_AND_PATCH (confidence 0.99, 180.67ms)
  shadow tenant shadow_sim_812-33230a (torn down)
  impacted services: 4 | tier-1: ['SVC-ORDER', 'SVC-PAY']
  cascading path trace
    AuthService -> RiskScoringService -> PaymentAPI  [2 hops]
    AuthService -> OrderGateway -> BillingWorker  [2 hops]
  ADR violations
    ADR-019 [critical] PR declares a breaking schema change on a service this ADR governs;
                       PR modifies a module the ADR explicitly froze: MOD-AUTH-TOKEN
    ADR-014 [critical] PR adds 180.0ms of latency, which re-introduces synchronous retry
                       pressure the ADR banned; projected p99 430ms exceeds the 200ms budget
```

### 2. `live_incident` — Multi-hop root cause + tri-tier recall (Track 01 + 02)

An alert fires on `PaymentAPI`. The triage agent traces upstream, ranks suspects by hop distance, change recency, declared breaking risk and tier, then consults **all three memory tiers** — the symptom signature against *episodic* incidents, the matching *procedural* playbook, and the *semantic* invariants standing over the affected domain:

```text
=== EPISODIC MEMORY RECALL ===
  INC-2201 similarity=0.4815 mttr=1680s
  INC-1802 similarity=0.4483 mttr=900s
  PLAYBOOK PB-09 (Drain and revert the payment retry path) overlap=0.4815 success=0.93
      1. quarantine_endpoint -> SVC-PAY: Isolate the charge endpoint behind a circuit breaker.
      2. create_revert_pr -> MOD-PAY-RETRY: Revert the retry-policy change that introduced the leak.
      3. page_domain_expert -> TEAM-FINOPS: Page the FinOps on-call owner of the charge path.
  reinforced {'id': 'PB-09', 'uses': 5, 'success_rate': 0.944}
  wrote new episode INC-06BB0E

=== SEMANTIC MEMORY (standing invariants) ===
  2 rule(s) hold over the payments domain:
      Billing reads order state only through OrderGateway, never directly from the order store.
      Cache misses on the charge path must degrade to the database, never to an error.
```

The resolution is written back as a new `(:EpisodicIncident)` and the playbook's success rate is updated — the memory improves with use.

### 3. `archaeology` — Decision provenance (Track 03)

Ask why something exists and Knot walks back through time to the decision that caused it, then computes who to ask:

```text
=== WHY ===
  ADR-003: Any code path that treats a cache miss as an error state has caused an outage.
  Caches may be bypassed but must never be required, and a retry must never re-enter a
  cache lookup it has already failed.

=== WHO ACTUALLY KNOWS THIS ===
  @daniel    score=0.302 team=FinOps
      authored 1 of 1 PR(s) touching this service's modules, authored ADR(s) ADR-003
  @sofia     score=0.083 team=Platform
  @priya     score=0.050 team=Platform
```

---

## 🛠️ Tech Stack

| Layer | Choice |
| :--- | :--- |
| Graph engine | **FalkorDB** (`FalkorDB==1.8.0`) — sparse-matrix traversal and native multi-graph tenancy |
| Graph algorithms | **networkx** — PageRank, betweenness centrality, shortest path, cycle detection |
| Agent protocol | **Model Context Protocol** via `fastmcp` |
| API | **FastAPI** + Server-Sent Events for live agent traces |
| Diff parsing | **tree-sitter** (Python + JavaScript grammars), with heuristic fallback |
| Command center | **Vite + React 19 + TypeScript**, `react-force-graph-3d` for the topology view |
| Storage model | One property graph, three tracks, bitemporal fields (`valid_from` / `valid_to`) |

---

## ⚡ Quick Start

### 1. Prerequisites

- Docker & Docker Compose (for FalkorDB)
- Python 3.11+
- Node.js 20+ (only for the 3D command center in step 7)

### 2. Start FalkorDB

```bash
git clone https://github.com/CVikasRaju/Knot.git
cd Knot

# FalkorDB on :6379, plus an optional graph browser on :3000
docker compose up -d falkordb
```

### 3. Install and configure

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env             # every value already has a working default
```

### 4. Seed the enterprise graph

```bash
python scripts/seed_knot.py --reset
# [seed] prod_org_brain: 91 nodes / 134 edges
# [seed] agent_memory:   18 nodes / 7 edges
```

### 5. Run a scenario

```bash
python scripts/run_scenario.py --mode future_sim   --pr_id PR-812 --breaking --latency-delta 180
python scripts/run_scenario.py --mode live_incident --alert_id ALT-904
python scripts/run_scenario.py --mode archaeology \
  --query "Why does PaymentService bypass Redis on retry?"
```

Add `--json` for machine-readable output or `--quiet` to suppress the live agent event stream. `make demo` runs all three.

### 6. Start the API and MCP server

```bash
# REST API + SSE trace streaming on :8000
python -m uvicorn src.api.main:app --reload --port 8000

# MCP server (stdio) for Claude Desktop, Cursor, or any MCP client
python -m src.mcp_server.server
```

### 7. Launch the 3D command center

With the API from step 6 still running:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The command center loads the seeded graph, then streams the agent trace as you run
each scenario. The graph dims to only the nodes an agent actually inspected, so the
multi-hop path is visible rather than described. Clicking any node, trace pill, or
certificate entry opens an inspector with its properties and neighbours.

| What you see | Where it comes from |
| :--- | :--- |
| Live 3D topology, coloured by track | `GET /api/graph` |
| Agent trace appearing as the run progresses | `POST /api/stream/*` (SSE) |
| Traversal highlight on the graph | `inspected_node_ids` on each SSE event |
| Verdict, blast radius, ADR violations | `simulation` in the result payload |
| Proof certificate, with the Cypher that produced it | `simulation.certificates` |

The dev server proxies `/api` to `:8000`, so no CORS setup is needed. For a
production build, `npm run build` writes `frontend/dist`; point any static server at
it and reverse-proxy `/api` to the API.

> The scenarios are read-only by default. Actions such as `block_ci_merge` and
> `create_revert_pr` are recorded with `status: "DRY_RUN"` unless you explicitly
> enable live execution, so clicking through the demo cannot mutate a real repo.

---

## 🗄️ Graph Data Model

Everything Knot knows lives in **one FalkorDB instance**, split across three
tenants. Tenants are separate graphs *by name* inside the same instance — that is
the mechanism the whole product rests on, because it makes forking production for
a simulation a `GRAPH.COPY` instead of a second database.

| Tenant | Created by | Lifetime | Contents |
| :--- | :--- | :--- | :--- |
| `prod_org_brain` | `make seed` | permanent | 91 nodes / 134 edges — code, infra, people, decisions, telemetry |
| `agent_memory` | `make seed` | permanent | 18 nodes / 7 edges — episodic incidents and procedural playbooks, grown by every run |
| `shadow_sim_<pr_id>-<hash>` | a simulation | dropped after the run | an ephemeral fork of production with one PR's mutation injected |

The ontology is defined once in `src/graph/ontology.py` and enforced at seed time
by `validate_schema()`, which fails the seed if a node label or edge type is used
that is not declared here. The tables below are generated from that file.

### Node labels (18)

| Track | Label | Key | Properties |
| :--- | :--- | :--- | :--- |
| **01 — Present** | `Service` | `id` | `name`, `tier`, `sla_ms`, `criticality_score`, `status`, `language`, `repo` |
| | `ASTModule` | `id` | `file_path`, `language`, `ast_hash`, `complexity`, `loc` |
| | `APISchema` | `id` | `endpoint`, `version`, `payload_hash`, `fields` |
| | `SchemaField` | `id` | `name`, `type`, `nullable`, `breaking`, `owner_module` |
| | `InfraResource` | `id` | `kind`, `region`, `config_json` |
| | `Alert` | `id` | `severity`, `metric`, `value`, `threshold`, `timestamp`, `status`, `summary` |
| **02 — Memory** | `EpisodicIncident` | `id` | `summary`, `occurred_at`, `root_cause_hash`, `mttr_seconds`, `severity` |
| | `ProceduralPlaybook` | `id` | `name`, `trigger_signature`, `action_steps_json`, `success_rate`, `uses` |
| | `SemanticRule` | `id` | `invariant_statement`, `enforcement_level`, `domain`, `source` |
| | `AgentSession` | `id` | `active_agent`, `status`, `updated_at` |
| | `ReasoningStep` | `id` | `step_index`, `agent_name`, `tool_called`, `cypher_executed`, `observation` |
| **03 — Company brain** | `Person` | `id` | `handle`, `name`, `role`, `timezone`, `on_call` |
| | `Team` | `id` | `name`, `slack_channel` |
| | `PullRequest` | `id` | `number`, `title`, `state`, `diff_summary`, `created_at`, `repo` |
| | `Commit` | `id` | `sha`, `message`, `timestamp`, `additions`, `deletions` |
| | `ADR` | `id` | `code`, `title`, `status`, `rationale`, `created_at`, `constraint_json` |
| | `DiscussionThread` | `id` | `platform`, `channel`, `summary`, `source_url`, `timestamp` |
| | `Ticket` | `id` | `key`, `title`, `priority`, `status` |

### Edge types (26)

`traversable` marks the edges a multi-hop walk may follow; the rest are
metadata hops that would otherwise let a traversal escape the causal graph.

| Track | Type | Source → Target | Traversable |
| :--- | :--- | :--- | :---: |
| **01** | `CALLS` | `Service` → `Service` | ✅ |
| | `EXPOSES` | `Service` → `APISchema` | ✅ |
| | `HAS_FIELD` | `APISchema` → `SchemaField` | ✅ |
| | `IMPLEMENTED_BY` | `Service` → `ASTModule` | ✅ |
| | `IMPORTS` | `ASTModule` → `ASTModule` | ✅ |
| | `DEPLOYED_ON` | `Service` → `InfraResource` | ✅ |
| | `TRIGGERED_ON` | `Alert` → `Service` | ✅ |
| | `MODIFIES` | `PullRequest` → `ASTModule` | ✅ |
| **02** | `AFFECTED` | `EpisodicIncident` → `Service` | ✅ |
| | `RESOLVED_BY` | `EpisodicIncident` → `ProceduralPlaybook` | ✅ |
| | `PRODUCED_STEP` | `AgentSession` → `ReasoningStep` | ✅ |
| | `INSPECTED_NODE` | `ReasoningStep` → `*` | — |
| | `HANDED_OFF_TO` | `ReasoningStep` → `ReasoningStep` | ✅ |
| **03** | `MEMBER_OF` | `Person` → `Team` | — |
| | `OWNS` | `Team` → `Service` | — |
| | `AUTHORED` | `Person` → `PullRequest` \| `ADR` | — |
| | `REVIEWED` | `Person` → `PullRequest` | — |
| | `COMMITTED` | `Person` → `Commit` | — |
| | `INCLUDED_IN` | `Commit` → `PullRequest` | — |
| | `IMPLEMENTS` | `PullRequest` → `Ticket` | — |
| | `DISCUSSED_IN` | `Ticket` → `DiscussionThread` | — |
| | `RESULTED_IN_DECISION` | `DiscussionThread` → `ADR` | — |
| | `GOVERNS` | `ADR` → `Service` | — |
| | `CONSTRAINS` | `ADR` → `APISchema` | — |
| | `VIOLATES` | `PullRequest` → `ADR` | — |

### Why this shape

The topology in `src/seed_data.py` is chosen so the three scenarios are
**emergent rather than hard-coded**:

- `SVC-AUTH` sits behind `SVC-ORDER` and `SVC-RISK`, which feed `SVC-PAY`,
  `SVC-BILL` and `SVC-NOTIF`. A schema change at the auth boundary therefore
  reaches two Tier-1 services **by real traversal**, not by a lookup table.
- `SVC-BILL → SVC-ORDER → SVC-PAY → SVC-BILL` is a genuine reconciliation
  cycle, so cycle detection has something honest to find.
- `ADR-019` carries a machine-checkable constraint over `MOD-AUTH-TOKEN` — the
  exact module `PR-812` touches — so the shadow simulation can cite the policy it
  breaks rather than guessing.

### Indexes

`seed.ensure_indexes()` creates 18 range indexes on `(label, id)` for every node
label. Without them every node-id lookup is a full label scan. Creation is
idempotent, and indexing is treated as an optimisation, never a correctness
requirement.

---

## 🧮 Cypher Queries and Graph Algorithms

These are the queries and algorithms the product actually depends on. The Cypher
below was captured from a live run by instrumenting the store's `_run` method,
so it is what FalkorDB receives — not an idealised version.

### 1. Bounded multi-hop traversal — `graph_multi_hop_investigate`

The workhorse. Every investigative tool is a bounded walk over a **typed**
relationship alternation, which is why traversal cannot escape into unrelated
metadata edges.

```cypher
// Built in src/graph/store.py::FalkorDBStore.traverse
// Direction is out | in | both; the pattern is assembled per direction because
// FalkorDB rejects some typed alternations mixed with an untyped -[r]- form.
MATCH (a {id: $start_id})
MATCH p = (a)-[:CALLS|IMPLEMENTED_BY|MODIFIES*1..6]->(b)
RETURN p LIMIT $limit
```

Concretely, the PR-812 simulation issues:

```cypher
MATCH (a {id: $start_id})
MATCH p = (a)-[:MODIFIES|IMPLEMENTED_BY|CALLS*1..5]->(b)
RETURN p LIMIT $limit
```

The hop cap is `KNOT_MAX_HOPS` (default 6). Multi-hop explosion is the main
failure mode of graph agents, so every traversal is bounded.

### 2. Reverse blast-radius propagation — the counterfactual core

Implemented in `src/graph/algorithms.py::blast_radius`. Propagation runs
**against** the call direction: if `A` calls `B` and `B` breaks, `A` is impacted.
This is a bounded breadth-first sweep over the reversed dependency digraph, which
keeps the shortest depth per node:

```
reversed = reverse(call_graph)
BFS from origin, max_hops, first-wins on depth
  -> impacted = {node: hop_distance}
  -> tier1_impacted, hop_distribution, impact_paths
```

Run it on the **shadow** graph and the hop-distance distribution *is* the
predicted blast radius before the merge. The blast radius reported for `PR-812`
(4 impacted services, 2 of them Tier-1, at a maximum of 2 hops) is the output of
this function, not a constant.

### 3. Counterfactual fork — `GRAPH.COPY`

The shadow-graph primitive, and the reason FalkorDB is load-bearing rather than a
drawing surface:

```python
# src/graph/store.py::FalkorDBStore.fork_graph
if target in self.list_graphs():
    self._client.connection.execute_command("GRAPH.DELETE", target)
self._client.connection.execute_command("GRAPH.COPY", source, target)
```

`GRAPH.DELETE` removes the graph key entirely; `MATCH (n) DETACH DELETE n` would
leave an empty graph still listed by `GRAPH.LIST` and break teardown assertions.
After evaluation the tenant is dropped, so simulations never accumulate.

### 4. Provenance walk — `trace_company_decision_provenance`

Walks backwards from code to the humans and documents that shaped it:

```
entity (Service)
  -[:IMPLEMENTED_BY]-> (ASTModule)
  <-[:MODIFIES]-        (PullRequest)
  <-[:AUTHORED]-        (Person)
  -[:IMPLEMENTS]->      (Ticket)
  -[:DISCUSSED_IN]->    (DiscussionThread)
  -[:RESULTED_IN_DECISION]-> (ADR)
```

This is composed as successive hops rather than one `MATCH p = ...` because the
walk changes direction partway (forward through `IMPLEMENTED_BY`, backward
through `MODIFIES`). The same decision is frequently reachable by more than one
route — an ADR can both govern a service directly and be produced by the
discussion behind a PR — so identical `(event, node)` pairs are collapsed; a
timeline that lists one decision twice reads as two separate decisions.

### 5. Cycle detection — `detect_cycles`

Finds dependency cycles, the classic cause of deploy-time deadlock. It
legitimately finds `SVC-BILL → SVC-ORDER → SVC-PAY → SVC-BILL` in the seeded
topology.

### Graph algorithms

`run_graph_topology_algorithm` exposes these as MCP tools, implemented on
`networkx` over a snapshot of the tenant (`src/graph/algorithms.py`):

| Algorithm | Question it answers | Where it is used |
| :--- | :--- | :--- |
| `pagerank_criticality` | If this node misbehaves, how much traffic-weighted blame reaches it? | Finding critical-but-undocumented modules nobody owns |
| `betweenness_centrality` | Which nodes sit on the most shortest paths? | Single points of failure during triage |
| `shortest_dependency_path` | What is the shortest causal chain between two entities? | Explaining *how* an alert reached a service |
| `blast_radius` | Everything downstream a change can reach | The PR-812 prediction |
| `detect_cycles` | Where are the dependency loops? | Pre-merge topology stress test |

Each returns an `AlgorithmResult` carrying `scores`, the `path`, a `details`
breakdown, and a natural-language `justification` — so a finding can always be
traced back to the traversal that produced it.

### Write path

Writes are batched `UNWIND $rows AS row MERGE ...` statements, one per edge type,
grouped by target label:

```cypher
UNWIND $rows AS row
MATCH (a:ReasoningStep {id: row.src})
MATCH (b:Service {id: row.dst})
MERGE (a)-[e:INSPECTED_NODE]->(b) SET e += row.props
RETURN count(e) AS merged
```

Edges are grouped by target label because `MERGE` needs a concrete label to bind
the destination node.

---

## 🧪 Tests

The suite runs against a **real FalkorDB instance**, not a mock — the bugs worth catching in this project all lived in the boundary with the engine (path edge indices, `GRAPH.DELETE` semantics, duplicate edge-type registries), and a fake store would have hidden every one of them. Start FalkorDB first, then:

```bash
python -m pytest              # 91 tests
```

Tests use throwaway per-test tenant names (`t_prod_*`, `t_mem_*`, `t_shadow_*`), so they never touch your seeded demo graph, and a fixture fails the suite if a test leaks a shadow tenant.

> Run one suite at a time against a given FalkorDB instance. The leak check diffs the instance-wide graph list, so a scenario or API process forking a shadow graph mid-run will make it fail on a false positive.

Set `KNOT_MEMORY_BACKEND=1` to run against the in-process backend instead; the suite then skips rather than failing, since the contract tests are only meaningful against the real engine.

---

## 🔌 MCP Tools

The server exposes eleven tools, all in `src/mcp_server/server.py`:

| Tool | Purpose |
| :--- | :--- |
| `graph_multi_hop_investigate` | Bounded traversal from any entity, with an explainable trace |
| `graph_node_catalog` | Resolve a human name ("PaymentService") to a node id |
| `resolve_dependents` | One-hop reverse lookup: which services own these modules |
| `run_graph_topology_algorithm` | PageRank, betweenness, shortest path, cycle detection |
| `fork_and_simulate_shadow_graph` | Fork production, inject a mutation, predict the blast radius |
| `memory_read_write_coordinator` | Retrieve similar episodes, store playbooks, record handoffs |
| `trace_company_decision_provenance` | Walk from code back to the decision and the expert |
| `triage_live_incident` | Alert → ranked suspects → matched playbook |
| `execute_verified_remediation` | Run an action, gated on a proof certificate |
| `describe_graph_schema` | Introspect the ontology |
| `graph_tenant_status` | List tenants and shadow graphs |

**The agents use these tools too.** The swarm does not import the graph layer and
call it directly — every traversal, algorithm and action goes through
`src/mcp_server/client.py`, which speaks MCP to the same server an external
client would. That is not a stylistic choice: it is what makes the tool
boundary real. If the agents could reach past it, the server would be a
demonstration of an interface nobody uses, and the tools could drift from the
code paths that matter without any test noticing. `tests/test_mcp_routing.py`
enforces both halves — the swarm imports no graph module, and a live run records
the MCP calls it made.

**Proof-gated execution.** `execute_verified_remediation` refuses to act without a `proof_path_ids` array produced by a prior traversal. An action that cannot cite the graph path justifying it does not run.

---

## 📁 Repository Layout

```text
src/
  graph/
    ontology.py     3-track node/edge schema + validate_schema()
    store.py        GraphStore contract, FalkorDBStore, InMemoryStore
    algorithms.py   PageRank, betweenness, shortest path, blast radius, cycles
    investigate.py  multi-hop traversal with an explainable trace
  agents/swarm.py   Archaeologist, Simulator, Triage, Commander + orchestrator
  memory/
    memory.py       tri-tier memory (episodic / semantic / procedural)
    proof.py        proof certificates and reasoning traces
  actions/
    diff_parser.py  tree-sitter PR diff parsing (heuristic fallback)
    shadow_sim.py   shadow-graph forking, mutation injection, teardown
    remediation.py  action execution + dry-run gate
    expertise.py    ownership and expertise scoring
  mcp_server/server.py   eleven MCP tools
  mcp_server/client.py   the swarm's MCP client (agents never touch the graph directly)
  api/main.py            REST + SSE endpoints
  seed.py, seed_data.py  the demo enterprise
  scenario.py            CLI scenario runner
frontend/                3D command center (Vite + React + TypeScript)
  src/App.tsx             layout: scenario rail, graph stage, trace/result panel
  src/lib/api.ts          REST + SSE client, typed against the API
  src/lib/types.ts        response shapes, mirrored from the Python dataclasses
  src/components/         GraphStage, AgentTimeline, ResultPanel, NodeInspector
tests/                   contract + scenario suites (real FalkorDB)
scripts/                 thin CLI shims
```

---

## ⚙️ Configuration

All tunables are environment-driven (`src/config.py`); see `.env.example` for the annotated list. The ones worth knowing:

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `FALKORDB_HOST` / `FALKORDB_PORT` | `127.0.0.1` / `6379` | Engine endpoint |
| `KNOT_PROD_GRAPH` / `KNOT_MEMORY_GRAPH` | `prod_org_brain` / `agent_memory` | Tenant graph names |
| `KNOT_MAX_HOPS` | `6` | Traversal depth cap — multi-hop explosion is the main failure mode |
| `KNOT_CONFIDENCE_THRESHOLD` | `0.85` | Bar a proof certificate must clear before an action executes |
| `KNOT_ACTIONS_DRY_RUN` | `1` | `1` plans and reports actions without executing them |
| `KNOT_MEMORY_BACKEND` | `0` | `1` forces the in-process backend (tests) |
| `KNOT_ALLOW_MEMORY_FALLBACK` | `0` | `1` permits an empty in-process graph when FalkorDB is absent (tests only) |

Actions default to **dry run**: the executor reports exactly what it would do and with which proof, without touching anything real.

> The `CHRONOS_`-prefixed names from the project's earlier working title still
> resolve, so an existing `.env` keeps working; `KNOT_` wins when both are set.

---

## 📊 Data provenance

**All graph data is synthetic.** The demo enterprise in `src/seed_data.py` is
authored for this project: 12 invented services (`SVC-AUTH`, `SVC-PAY`,
`SVC-STRIPE` …), 6 invented people, 4 teams, and fictional ADRs, tickets and
Slack threads. Nothing is scraped, and no real organisation's code, people or
incidents are represented.

- No private, login-protected, paywalled or personal data is loaded.
- The seeder makes **no network calls** — there is no ingestion path that could
  pull in external data.
- Slack URLs in the seed data are inert placeholders under `acme.slack.com`;
  they are display strings for the provenance timeline, not live links.
- `src/seed_data.py` is the single source of truth, so the seeder, the tests and
  this README all describe the same company.

Knot is therefore safe to run against the public repo: seeding cannot reach out
to anything.

---

## 🎬 Demo video

<!-- Replace with your recorded walkthrough before submitting. -->
**Link:** _to be added_ — a single take showing the three scenarios below in
order, run against a live FalkorDB, with the 3D command center open.

What the recording shows:

1. **`future_sim` (Track 01).** Simulate `PR-812`, which changes the token
   timeout in `MOD-AUTH-TOKEN`. The simulator forks production into
   `shadow_sim_812-*`, injects the mutation, and reports a blast radius of 4
   impacted services — 2 of them Tier-1 — plus the `ADR-019` and `ADR-014`
   constraints the change violates. The graph dims to just the inspected nodes
   as the trace streams.
2. **`live_incident` (Track 01 + 02).** Trigger `ALT-904`. Triage runs
   betweenness centrality and a multi-hop walk to isolate the cause, recalls
   playbook `PB-09` from episodic memory, and the Commander executes a
   proof-gated revert.
3. **`archaeology` (Track 03).** Ask why `PaymentService` bypasses Redis on
   retry. The provenance walk returns `ADR-003`, the Slack thread behind it, and
   a ranked list of who actually knows the system.

Reproduce it locally with `make demo`, or the three commands in
[Quick Start step 5](#5-run-a-scenario).

---

## ✅ Submission checklist

| Requirement | Where it is satisfied |
| :--- | :--- |
| Public source-code repository | `github.com/CVikasRaju/Knot` (MIT) |
| README with setup instructions | [Quick Start](#-quick-start) — FalkorDB → seed → API/MCP → UI |
| README with the graph data model | [Graph Data Model](#️-graph-data-model) — 18 node labels, 26 edge types, 3 tenants |
| Cypher queries the product depends on | [Cypher Queries and Graph Algorithms](#-cypher-queries-and-graph-algorithms) — captured from live runs |
| Graph algorithms the product depends on | Same section — PageRank, betweenness, shortest path, blast radius, cycles |
| Demo video | [Demo video](#-demo-video) |
| Live deployment, or complete local setup | [Quick Start](#-quick-start) — fully local, Docker Compose for the engine |
| Track entered | [Track entered](#-track-entered) — **Track 01** |
| FalkorDB is the primary graph database and load-bearing | [FalkorDB is load-bearing](#️-falkordb-is-load-bearing) |
| Data is public / synthetic / own | [Data provenance](#-data-provenance) — fully synthetic, no network ingestion |

---

## 📜 License

MIT License — built for **GraphHacks**.


