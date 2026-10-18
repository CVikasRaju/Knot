# Knot — 3-minute demo script

Everything here is the real output of the real code. Nothing is invented, and no
number below needs to be rounded or apologised for.

---

## 0. Before you hit record (10 minutes, do this once)

```bash
cd /workspace/Knot

sudo dockerd > /tmp/docker.log 2>&1 &     # if Docker is not already up
make graph                                 # FalkorDB
make seed                                  # 91 nodes / 134 edges
make api                                   # API on :8000
VITE_PORT=12000 make ui                    # UI on :12000
```

Then open the UI and **check these three things**, because the whole demo
depends on them:

1. The header shows a green **falkordb** chip, **91 nodes**, **134 edges**.
   If the chip says `connecting`, the API is not up.
2. The 3D graph is visible and slowly moving. Drag it once to make sure it
   responds, then leave it still.
3. Click **Simulate PR-812** once, all the way through, and then click it again
   when the run finishes. The second run must work too — the first run leaves the
   graph in a different state, and you do not want to discover a problem live.

Reload the page before recording so the graph is back to its resting state.

**Recording setup**

- 1920×1080, browser at ~90% zoom so all three panels fit without scrolling.
- Hide bookmarks bar, close other tabs, turn off notifications.
- One monitor for the terminal, one for the UI, if you have them.
- Record the audio first, then the screen to the audio. It is much easier to get
  a clean 3 minutes when you are not also driving the mouse.

---

## 1. The one thing to get right

The pitch is not "we built a graph agent". Everyone at a graph hackathon built a
graph agent. The pitch is:

> **Every other agent answers with text. Knot answers with a path through the
> graph, and refuses to act without one.**

Three sentences carry that:

- **"There is no LLM in this codebase."** Say this early. It is the most
  surprising true thing about the project and it explains everything else.
- **"That verdict came with a proof certificate."** Point at it on screen when
  you say it.
- **"The graph is not a visualisation."** FalkorDB is where the work happens.

Do not try to explain all three tracks. Show three things, land the proof point
three times, stop.

---

## 2. The script (3:00)

### 0:00–0:22 — Cold open, no slides

**On screen:** the UI at rest. 3D topology turning slowly. Do not touch anything.

> "This is Knot. It is one property graph in FalkorDB, and it answers three
> questions about a software organisation.
>
> Past: why does this code exist? Present: why is production broken right now?
> Future: what breaks if we merge this pull request?
>
> The important thing is how it answers. There is no LLM anywhere in this
> codebase. Every answer is a graph traversal, and every answer comes with a
> proof certificate naming the exact nodes and hops behind it."

*Delivery: slow down on "there is no LLM". Let it land.*

---

### 0:22–1:10 — Future: block the PR before it merges

**Click:** **Simulate PR-812**.

**On screen, in this order:** the left rail button greys out and the header flips
to `running — stop`. The agent trace streams into the right panel, line by line.
Nodes on the 3D graph light up as they are inspected, and everything else dims.
The stage title changes to `traversal highlight · N nodes inspected`. When it
finishes, a red **BLOCKED** appears over the graph.

**Then say, reading off the panel:**

> "A developer has opened PR-812. Before it merges, Knot forks the production
> graph into a throwaway tenant — you can see the shadow graph in the trace — and
> injects the PR's actual diff.
>
> The diff is parsed with tree-sitter. It removes a field from the auth payload
> and adds a hundred and eighty milliseconds of latency. Knot then walks the
> dependency graph to find out who downstream cares."

**Point at the impacted list:**

> "Four services are impacted, two of them Tier-1. Here is the actual cascade:
> AuthService to RiskScoringService to PaymentAPI. And AuthService to OrderGateway
> to BillingWorker. Those are two hops each, and BillingWorker is nowhere near
> the file that was changed."

**Point at the ADR violations:**

> "Then it finds the decision that already forbade this. ADR-019 froze the token
> module — the PR modifies it. ADR-014 banned synchronous retry pressure on the
> auth path, and the projected p99 of four hundred and thirty milliseconds blows
> through the two-hundred millisecond budget that ADR set.
>
> So the verdict is BLOCKED, and the agent has set the CI merge gate to failure.
> That is a real action, not a suggestion."

*If you have a spare second, hover the certificate. Otherwise skip.*

---

### 1:10–2:00 — Present: find the root cause and act

**Click:** **Trigger ALT-904**.

> "Now the other direction. A live alert: the payment API is throwing a
> forty-two percent 502 rate."

**On screen:** the trace streams again, then the result panel fills.

> "Knot ranks upstream suspects by graph distance and declared breaking risk.
> OrderGateway comes out on top — one hop upstream, a declared breaking risk of
> forty-five percent, and it is Tier-1."

**Point at the episodic memory block:**

> "This is the part I like. Instead of starting from scratch, it searches its own
> incident memory. It finds INC-2201 — a previous payment 502 spike — at
> forty-eight percent similarity, and it matches playbook PB-09: drain and revert
> the payment retry path, with a ninety-three percent success rate.
>
> So the remediation is three concrete steps: quarantine the charge endpoint
> behind a circuit breaker, open a revert PR, and page the FinOps on-call owner.
> All three are proof-gated — look at the certificate IDs. The executor will not
> run an action that cannot cite the graph path justifying it."

**Point at the actions list:**

> "Every one of those shows DRY_RUN, because the gate is on. Flip one environment
> variable and these are real pull requests and real pages."

*Optional, if you are ahead of time:* click the playbook node to show the
inspector, then close it.

---

### 2:00–2:35 — Past: why does this code exist

**Click:** **Trace decision provenance**.

> "Last one, and it is the question that costs teams the most time: why does this
> code exist?"

**On screen:** a timeline builds in the panel, dated and sorted oldest first.

> "The query resolves to the payment service, and Knot walks backwards — from the
> service, through the modules, through the pull request that touched them,
> through the ticket, into the Slack thread, and out to the architecture decision
> record.
>
> Read the dates. On the eleventh of May, PR-402 bypasses Redis on the retry
> path. The same day, in Slack, Daniel writes: 'Retry storms on the charge path
> are what took out the cache.' The next day that becomes ADR-003 — caches may be
> bypassed but must never be required, and a retry must never re-enter a cache
> lookup it has already failed.
>
> So the code is weird on purpose, and now you know exactly why, with the date,
> the author and the link to the thread."

**Point at the expertise ranking:**

> "And it ranks who actually knows this. Not a wiki page — a score built from who
> wrote the modules, who reviewed them, and who authored the decision. Daniel
> authored the ADR, so Daniel is who you ask."

---

### 2:35–3:00 — Close

**On screen:** back to the full graph, or hold on the BLOCKED verdict.

> "So that is Knot. One graph, three horizons, and every single conclusion is a
> traversal you can inspect — no LLM, no black box.
>
> FalkorDB is doing real work here. The shadow simulation is a `GRAPH.COPY` into
> a separate tenant, the blast radius is a bounded breadth-first search, the
> triage is a typed multi-hop walk, and criticality is PageRank and betweenness
> centrality. Remove the graph and none of this survives.
>
> Everything is in the repo — the MCP server, the agents, the tests. Thanks for
> watching."

---

## 3. What each thing on screen actually is

If a judge asks, these are the real numbers and the real names. Do not guess.

| On screen | What it is |
| :--- | :--- |
| `falkordb` chip, 91 nodes, 134 edges | `prod_org_brain`, seeded by `src/seed_data.py` |
| `running — stop` | an SSE stream is open; the button aborts it |
| `traversal highlight · N nodes` | nodes the run actually inspected, lit on the graph |
| BLOCKED / confidence 0.99 | `BLOCK_AND_PATCH`, from `src/actions/shadow_sim.py` |
| shadow tenant `shadow_sim_812-<hash>` | a real graph in the same FalkorDB instance, dropped after the run |
| `PROOF-<hex>` | certificate id; the full node/hop list is in the panel |
| ADR-019 / ADR-014 | frozen token module; the 200ms auth p99 budget |
| INC-2201, PB-09 | episodic incident; procedural playbook, 93% success |
| ADR-003 | "caches may be bypassed but must never be required" |
| `DRY_RUN` | `KNOT_ACTIONS_DRY_RUN=1`, the default |

---

## 4. Questions you will get

**"Is there really no LLM?"**
Correct, and it is deliberate. A finding is either backed by a reproducible path
or it is not reported. That is what makes it auditable. It also means it is fast
and free to run.

**"So it is not an AI agent?"**
It is a multi-agent system — four agents with distinct roles and handoffs. The
reasoning is graph algorithms rather than a language model. That is a design
choice, not a missing feature.

**"What if the graph is wrong?"**
Then the answer is wrong, and you can see exactly where, because the proof
certificate names every node and hop. With a text answer you cannot tell whether
it is wrong.

**"Why FalkorDB and not Neo4j?"**
Multi-tenancy is native. A shadow simulation is a copy into a second named graph
in the same instance, which is what makes forking production cheap enough to do
on every pull request.

**"Is the data real?"**
Fully synthetic and generated locally. The seeder makes no network calls.

---

## 5. If you have 60 seconds instead

Record only the first act.

> "This is Knot, one property graph in FalkorDB. A developer opened PR-812. Before
> it merges, Knot forks production into a throwaway graph, injects the diff, and
> walks the dependencies. Four services break, two of them Tier-1 — and here is
> the cascade, two hops out. Then it finds ADR-014, which already banned this
> exact change, and sets the CI gate to failure. No LLM, no guessing: that verdict
> came with a proof certificate naming every node and hop."

---

## 6. Common recording mistakes

- **Do not say "as you can see" while the trace is still streaming.** Wait for
  the verdict, then talk.
- **Do not narrate the mouse.** Say what the result means, not what you clicked.
- **Do not explain the whole ontology.** Nobody watching a 3-minute video needs
  18 node labels. Give them four: service, module, ADR, incident.
- **Do not apologise for dry run.** Frame it as a safety property: the executor
  is proof-gated and inert by default.
- **Do not read the panel aloud.** Point at the one number that matters and say
  what it means.
- **Do not rush the last 20 seconds.** Land the close, then stop talking.
