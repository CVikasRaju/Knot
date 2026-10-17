/**
 * NodeInspector - the detail view for a single graph entity.
 *
 * Opens when a node is clicked anywhere in the UI (the 3D graph, a trace pill,
 * a certificate node). Neighbours are fetched from /api/investigate so the
 * inspector can show what the node connects to, which is how a viewer explores
 * the graph without writing Cypher.
 */

import { useEffect, useState } from 'react'
import { investigate } from '../lib/api'
import type { GraphEdge, GraphNode, PathNode } from '../lib/types'

interface Props {
  node: GraphNode
  edges: GraphEdge[]
  onNodeClick: (id: string) => void
  onClose: () => void
}

const TRACK_LABEL: Record<string, string> = {
  track_01_present: 'Track 01 · Present — code, infra & telemetry',
  track_02_memory: 'Track 02 · Past — episodic & procedural memory',
  track_03_company_brain: 'Track 03 · Company brain — people & decisions',
}

export function NodeInspector({ node, edges, onNodeClick, onClose }: Props) {
  const [neighbours, setNeighbours] = useState<PathNode[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    investigate(node.id, 2, 'both')
      .then((result) => {
        if (cancelled) return
        // Distinct neighbours, excluding the node itself.
        const seen = new Map<string, PathNode>()
        for (const path of result.paths) {
          for (const n of path.nodes) {
            if (n.id !== node.id && !seen.has(n.id)) seen.set(n.id, n)
          }
        }
        setNeighbours([...seen.values()].slice(0, 24))
      })
      .catch(() => {
        if (!cancelled) setNeighbours([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [node.id])

  // Edges touching this node, resolved to a readable label.
  const touching = edges.filter((e) => e.source === node.id || e.target === node.id)

  return (
    <div className="inspector">
      <div className="inspector-head">
        <span className="legend-swatch" style={{ background: node.color, width: 10, height: 10 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {node.name}
          </div>
          <div className="mono dim" style={{ fontSize: 10.5 }}>
            {node.label} · {node.id}
          </div>
        </div>
        <button
          type="button"
          className="node-pill"
          onClick={onClose}
          aria-label="Close inspector"
        >
          close
        </button>
      </div>

      <div className="inspector-body">
        <div className="card">
          <div className="card-head">
            <span className="card-title">Track</span>
          </div>
          <div className="mono" style={{ fontSize: 12 }}>
            {TRACK_LABEL[node.track] ?? node.track}
          </div>
        </div>

        {Object.keys(node.properties).length > 0 ? (
          <div className="card">
            <div className="card-head">
              <span className="card-title">Properties</span>
            </div>
            <dl className="kv">
              {Object.entries(node.properties)
                .filter(([, v]) => v !== null && v !== undefined && v !== '')
                .slice(0, 14)
                .map(([k, v]) => (
                  <div key={k} style={{ display: 'contents' }}>
                    <dt>{k}</dt>
                    <dd className="mono" style={{ fontSize: 11.5 }}>
                      {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                    </dd>
                  </div>
                ))}
            </dl>
          </div>
        ) : null}

        <div className="card">
          <div className="card-head">
            <span className="card-title">Neighbours</span>
            <span className="chip">{touching.length} edges</span>
          </div>
          {loading ? (
            <>
              <div className="skeleton" />
              <div className="skeleton" />
            </>
          ) : neighbours.length === 0 ? (
            <p className="dim mono" style={{ fontSize: 11 }}>
              No connected nodes found within 2 hops.
            </p>
          ) : (
            <ul className="list">
              {neighbours.map((n) => {
                const via = touching.find(
                  (e) =>
                    (e.source === node.id && e.target === n.id) ||
                    (e.target === node.id && e.source === n.id),
                )
                return (
                  <li key={n.id}>
                    <button
                      type="button"
                      className="node-pill"
                      onClick={() => onNodeClick(n.id)}
                      style={{ width: '100%', textAlign: 'left' }}
                    >
                      <span style={{ color: 'var(--text)' }}>{n.name}</span>
                      <span className="dim" style={{ marginLeft: 6 }}>
                        {n.id}
                      </span>
                      {via ? (
                        <span className="mono dim" style={{ marginLeft: 6, fontSize: 10 }}>
                          {via.type}
                        </span>
                      ) : null}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        <details className="raw">
          <summary>Raw node JSON</summary>
          <pre>{JSON.stringify(node, null, 2)}</pre>
        </details>
      </div>
    </div>
  )
}
