/**
 * Knot command center.
 *
 * One screen, three horizons:
 *  - left rail: the scenarios (past / present / future) and the legend
 *  - centre: the live 3D topology, dimming everything the run did not touch
 *  - right panel: the streaming agent trace, then the verdict and evidence
 *
 * The graph is loaded once from /api/graph. Scenarios are POSTed to the SSE
 * stream endpoints; as events arrive the inspected node ids are accumulated
 * into a highlight set that the 3D view reacts to.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AgentTimeline, reduceEvents, type TraceEntry } from './components/AgentTimeline'
import { GraphStage } from './components/GraphStage'
import { NodeInspector } from './components/NodeInspector'
import { ResultPanel } from './components/ResultPanel'
import { fetchFixtures, fetchGraph, fetchHealth, streamScenario } from './lib/api'
import type {
  AgentEvent,
  DemoFixture,
  GraphEdge,
  GraphNode,
  ScenarioKind,
  ScenarioResult,
} from './lib/types'

/**
 * Track keys as the API emits them (see Track in src/graph/ontology.py).
 * The suffix is what the UI keys on so a future track_04 does not silently
 * fall out of the legend.
 */
const TRACK_META: Record<string, { label: string; color: string }> = {
  track_01_present: { label: 'Present — code & infra', color: '#38bdf8' },
  track_02_memory: { label: 'Past — agent memory', color: '#a78bfa' },
  track_03_company_brain: { label: 'Company brain — people & decisions', color: '#34d399' },
}

const TRACK_ORDER = ['track_01_present', 'track_02_memory', 'track_03_company_brain']

export default function App() {
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [backend, setBackend] = useState<string>('')

  const [hiddenTracks, setHiddenTracks] = useState<Set<string>>(new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const [running, setRunning] = useState(false)
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [result, setResult] = useState<ScenarioResult | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [activeScenario, setActiveScenario] = useState<ScenarioKind | null>(null)
  const [fixtures, setFixtures] = useState<Record<string, DemoFixture>>({})
  const [panelTab, setPanelTab] = useState<'trace' | 'result'>('trace')

  const abortRef = useRef<AbortController | null>(null)

  // ---- initial load ------------------------------------------------------
  useEffect(() => {
    let cancelled = false
    Promise.all([fetchGraph(), fetchHealth().catch(() => ({}))])
      .then(([snapshot, health]) => {
        if (cancelled) return
        setNodes(snapshot.nodes)
        setEdges(snapshot.edges)
        setBackend(String((health as { backend?: string }).backend ?? snapshot.graph ?? ''))
      })
      .catch((err: Error) => {
        if (!cancelled) setLoadError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    fetchFixtures()
      .then((f) => {
        if (!cancelled) setFixtures(f)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  // ---- scenario runner ---------------------------------------------------
  const runScenario = useCallback(
    async (scenario: ScenarioKind, options: Partial<Parameters<typeof streamScenario>[0]> = {}) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      setRunning(true)
      setEvents([])
      setResult(null)
      setRunError(null)
      setActiveScenario(scenario)
      setPanelTab('trace')

      try {
        const final = await streamScenario(
          { scenario, ...options },
          (event) => setEvents((prev) => [...prev, event]),
          controller.signal,
        )
        if (final) {
          setResult(final)
          setPanelTab('result')
        }
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          setRunError((err as Error).message)
        }
      } finally {
        setRunning(false)
      }
    },
    [],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
    setRunning(false)
  }, [])

  // ---- derived -----------------------------------------------------------
  const traceEntries: TraceEntry[] = useMemo(() => reduceEvents(events), [events])

  // Every node the agents inspected, across all events, plus the certificate
  // node ids. This is what dims the untouched part of the graph.
  const highlighted = useMemo(() => {
    const set = new Set<string>()
    for (const event of events) {
      const ids = event.inspected_node_ids as string[] | undefined
      if (Array.isArray(ids)) for (const id of ids) set.add(id)
    }
    if (result?.simulation) {
      for (const cert of result.simulation.certificates) {
        for (const id of cert.node_ids) set.add(id)
      }
      for (const svc of result.simulation.impacted_services) set.add(svc.id)
    }
    if (result?.provenance) set.add(result.provenance.entity_id)
    return set
  }, [events, result])

  const trackCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const n of nodes) counts[n.track] = (counts[n.track] ?? 0) + 1
    return counts
  }, [nodes])

  const selectedNode = useMemo(
    () => (selectedId ? (nodes.find((n) => n.id === selectedId) ?? null) : null),
    [selectedId, nodes],
  )

  const toggleTrack = (track: string) => {
    setHiddenTracks((prev) => {
      const next = new Set(prev)
      if (next.has(track)) next.delete(track)
      else next.add(track)
      return next
    })
  }

  const verdict = result?.simulation?.verdict ?? result?.verdict

  return (
    <div className="shell">
      {/* ------------------------------------------------------------ head */}
      <header className="head">
        <div className="brand">
          <h1>Knot</h1>
          <span className="sub">Temporal engineering graph</span>
        </div>
        <span className={`chip ${backend === 'falkordb' ? 'chip-ok' : 'chip-warn'}`}>
          <span className="dot" />
          {backend || 'connecting'}
        </span>
        <span className="chip">{nodes.length} nodes</span>
        <span className="chip">{edges.length} edges</span>
        <div className="head-spacer" />
        {running ? (
          <button type="button" className="chip chip-danger chip-live" onClick={stop}>
            <span className="dot" />
            running — stop
          </button>
        ) : (
          <span className="chip">
            <span className="dot" />
            idle
          </span>
        )}
      </header>

      {/* ------------------------------------------------------------ rail */}
      <aside className="rail">
        <div className="section">
          <h2>Scenarios</h2>

          <button
            type="button"
            className="btn btn-future"
            disabled={running}
            onClick={() =>
              runScenario('future_simulation', {
                prId: 'PR-812',
                diffText: fixtures['PR-812']?.diff ?? '',
                breakingSchemaChanges: fixtures['PR-812']?.breaking_schema_changes ?? true,
                latencyDeltaMs: fixtures['PR-812']?.simulated_latency_delta_ms ?? 180,
              })
            }
          >
            <span className="glyph">◈</span>
            <span>
              Simulate PR-812
              <span className="meta">future · shadow-graph blast radius</span>
            </span>
          </button>

          <button
            type="button"
            className="btn btn-present"
            disabled={running}
            onClick={() => runScenario('live_incident', { alertId: 'ALT-904' })}
          >
            <span className="glyph">⚡</span>
            <span>
              Trigger ALT-904
              <span className="meta">present · multi-hop triage + action</span>
            </span>
          </button>

          <button
            type="button"
            className="btn btn-past"
            disabled={running}
            onClick={() =>
              runScenario('archaeology', {
                query: 'Why does PaymentService bypass Redis on retry?',
              })
            }
          >
            <span className="glyph">◷</span>
            <span>
              Trace decision provenance
              <span className="meta">past · why this code exists</span>
            </span>
          </button>

          {activeScenario ? (
            <p className="dim mono" style={{ fontSize: 10, marginTop: 4 }}>
              last run: {activeScenario}
            </p>
          ) : null}
        </div>

        <div className="section">
          <h2>Layers</h2>
          <div className="legend">
            {TRACK_ORDER.map((track) => {
              const meta = TRACK_META[track]
              if (!meta) return null
              return (
                <div
                  key={track}
                  className={`legend-row ${hiddenTracks.has(track) ? 'off' : ''}`}
                  onClick={() => toggleTrack(track)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') toggleTrack(track)
                  }}
                >
                  <span className="legend-swatch" style={{ background: meta.color }} />
                  <span>{meta.label}</span>
                  <span className="legend-count">{trackCounts[track] ?? 0}</span>
                </div>
              )
            })}
          </div>
        </div>

        <div className="section">
          <h2>How to read this</h2>
          <p className="dim" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
            Run a scenario and watch the agents light up the exact nodes they
            traversed. Every conclusion carries a proof certificate naming the
            nodes and the Cypher that produced it.
          </p>
        </div>
      </aside>

      {/* ----------------------------------------------------------- stage */}
      <main className="stage">
        {loadError ? (
          <div className="error-banner">
            Could not reach the API: {loadError}. Is the backend running on
            :8000?
          </div>
        ) : null}
        {loading ? (
          <div className="stage-empty">
            <div className="skeleton" style={{ width: 220 }} />
            <span>loading graph…</span>
          </div>
        ) : (
          <GraphStage
            nodes={nodes}
            edges={edges}
            highlighted={highlighted}
            hiddenTracks={hiddenTracks}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        )}

        <div className="stage-overlay stage-title">
          {highlighted.size > 0
            ? `traversal highlight · ${highlighted.size} nodes inspected`
            : 'production topology'}
        </div>

        {verdict ? (
          <div className={`stage-overlay stage-verdict verdict-${verdict.toLowerCase()}`}>
            {verdict}
          </div>
        ) : null}

        {!loading && highlighted.size === 0 ? (
          <div className="stage-overlay stage-hint">
            drag to orbit · scroll to zoom · click a node to inspect
          </div>
        ) : null}
      </main>

      {/* ----------------------------------------------------------- panel */}
      <aside className="panel">
        {runError ? <div className="error-banner">{runError}</div> : null}

        <div className="tabs">
          <button
            type="button"
            className={`tab ${panelTab === 'trace' ? 'active' : ''}`}
            onClick={() => setPanelTab('trace')}
          >
            Agent trace {traceEntries.length ? `(${traceEntries.length})` : ''}
          </button>
          <button
            type="button"
            className={`tab ${panelTab === 'result' ? 'active' : ''}`}
            onClick={() => setPanelTab('result')}
          >
            Result
          </button>
        </div>

        {panelTab === 'trace' ? (
          <AgentTimeline entries={traceEntries} onNodeClick={setSelectedId} />
        ) : (
          <ResultPanel result={result} onNodeClick={setSelectedId} />
        )}

        {selectedNode ? (
          <NodeInspector
            node={selectedNode}
            edges={edges}
            onNodeClick={setSelectedId}
            onClose={() => setSelectedId(null)}
          />
        ) : null}
      </aside>
    </div>
  )
}
