# ⏳ Knot: Temporal-Predictive Engineering Graph

> **Built for GraphHacks (WeMakeDevs) | Powered by FalkorDB & Model Context Protocol (MCP)**
> **Primary Entry:** Track 01 (Agents That Act on Connected Data)
> **Integrated Capabilities:** Track 02 (Episodic / Procedural Agent Memory) + Track 03 (Unified Company Brain)

[![FalkorDB](https://img.shields.io/badge/Graph_Engine-FalkorDB-FF5722?style=for-the-badge)](https://www.falkordb.com/)
[![MCP](https://img.shields.io/badge/Protocol-Model_Context_Protocol-00ADD8?style=for-the-badge)](https://modelcontextprotocol.io/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI_%2B_SSE-009688?style=for-the-badge)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Core-Python_3.11_%2B_networkx-3776AB?style=for-the-badge)](https://www.python.org/)

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

### 2. `live_incident` — Multi-hop root cause + episodic recall (Track 01 + 02)

An alert fires on `PaymentAPI`. The triage agent traces upstream, ranks suspects by hop distance, change recency, declared breaking risk and tier, then matches the symptom signature against **episodic memory** to recall a prior incident and its procedural playbook:

```text
=== EPISODIC MEMORY RECALL ===
  INC-2201 similarity=0.4815 mttr=1680s
  PLAYBOOK PB-09 (Drain and revert the payment retry path) overlap=0.4815 success=0.93
      1. quarantine_endpoint -> SVC-PAY: Isolate the charge endpoint behind a circuit breaker.
      2. create_revert_pr -> MOD-PAY-RETRY: Revert the retry-policy change that introduced the leak.
      3. page_domain_expert -> TEAM-FINOPS: Page the FinOps on-call owner of the charge path.
  reinforced {'id': 'PB-09', 'uses': 5, 'success_rate': 0.944}
  wrote new episode INC-E7BC19
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
python scripts/seed_chronos_graph.py --reset
# [seed] prod_org_brain: 87 nodes / 134 edges
# [seed] agent_memory:   11 nodes / 7 edges
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

## 🧪 Tests

The suite runs against a **real FalkorDB instance**, not a mock — the bugs worth catching in this project all lived in the boundary with the engine (path edge indices, `GRAPH.DELETE` semantics, duplicate edge-type registries), and a fake store would have hidden every one of them. Start FalkorDB first, then:

```bash
python -m pytest              # 81 tests
```

Tests use throwaway per-test tenant names (`t_prod_*`, `t_mem_*`, `t_shadow_*`), so they never touch your seeded demo graph, and a fixture fails the suite if a test leaks a shadow tenant.

> Run one suite at a time against a given FalkorDB instance. The leak check diffs the instance-wide graph list, so a scenario or API process forking a shadow graph mid-run will make it fail on a false positive.

Set `CHRONOS_MEMORY_BACKEND=1` to run against the in-process backend instead; the suite then skips rather than failing, since the contract tests are only meaningful against the real engine.

---

## 🔌 MCP Tools

The server exposes nine tools, all in `src/mcp_server/server.py`:

| Tool | Purpose |
| :--- | :--- |
| `graph_multi_hop_investigate` | Bounded traversal from any entity, with an explainable trace |
| `run_graph_topology_algorithm` | PageRank, betweenness, shortest path, cycle detection |
| `fork_and_simulate_shadow_graph` | Fork production, inject a mutation, predict the blast radius |
| `memory_read_write_coordinator` | Retrieve similar episodes, store playbooks, record handoffs |
| `trace_company_decision_provenance` | Walk from code back to the decision and the expert |
| `triage_live_incident` | Alert → ranked suspects → matched playbook |
| `execute_verified_remediation` | Run an action, gated on a proof certificate |
| `describe_graph_schema` | Introspect the ontology |
| `graph_tenant_status` | List tenants and shadow graphs |

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
  mcp_server/server.py   nine MCP tools
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
| `CHRONOS_MAX_HOPS` | `6` | Traversal depth cap — multi-hop explosion is the main failure mode |
| `CHRONOS_CONFIDENCE_THRESHOLD` | `0.85` | Bar a proof certificate must clear before an action executes |
| `CHRONOS_ACTIONS_DRY_RUN` | `1` | `1` plans and reports actions without executing them |
| `CHRONOS_MEMORY_BACKEND` | `0` | `1` forces the in-process backend (tests) |

Actions default to **dry run**: the executor reports exactly what it would do and with which proof, without touching anything real.

---

## 📜 License

MIT License — built for **GraphHacks**.
