/**
 * ForceGraph3D - the live topology.
 *
 * Behaviour that matters for the demo:
 *  - Nodes are coloured by track (present/memory/brain) using the same palette
 *    the API emits, so the 3D view and the JSON agree.
 *  - When a scenario runs, every node the agents inspected is highlighted and
 *    the rest are dimmed. That is the whole point: the judge should be able to
 *    *see* the multi-hop traversal happen rather than read it in a log.
 *  - Clicking a node reports it upward for the inspector.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
import type { GraphEdge, GraphNode } from '../lib/types'

export interface StageGraphNode extends GraphNode {
  x?: number
  y?: number
  z?: number
}

interface Link {
  source: string | StageGraphNode
  target: string | StageGraphNode
  type: string
}

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** Node ids the running agents have inspected; highlighted when non-empty. */
  highlighted: Set<string>
  hiddenTracks: Set<string>
  selectedId: string | null
  onSelect: (id: string | null) => void
}

const DIM_OPACITY = 0.12
const EDGE_DIM = 'rgba(148,163,184,0.06)'
const EDGE_BRIGHT = 'rgba(148,163,184,0.30)'

export function GraphStage({
  nodes,
  edges,
  highlighted,
  hiddenTracks,
  selectedId,
  onSelect,
}: Props) {
  // The force engine mutates node objects in place; keep one stable array.
  const fgRef = useRef<{ cameraPosition?: (p: object, ms?: number) => void } | null>(null)

  const visibleNodes = useMemo(
    () => nodes.filter((n) => !hiddenTracks.has(n.track)),
    [nodes, hiddenTracks],
  )

  const visibleIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes])

  const links = useMemo<Link[]>(
    () =>
      edges
        .filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
        .map((e) => ({ source: e.source, target: e.target, type: e.type })),
    [edges, visibleIds],
  )

  const hasHighlight = highlighted.size > 0

  const nodeColor = useCallback(
    (node: StageGraphNode) => {
      if (hasHighlight && !highlighted.has(node.id)) {
        // Fade non-participants so the traversal path reads instantly.
        return withAlpha(node.color, DIM_OPACITY)
      }
      return node.color
    },
    [hasHighlight, highlighted],
  )

  const nodeVal = useCallback(
    (node: StageGraphNode) => {
      const base = node.tier === 1 ? 9 : node.tier === 2 ? 5 : 3
      return highlighted.has(node.id) ? base * 1.9 : base
    },
    [highlighted],
  )

  const linkColor = useCallback(
    (link: Link) => {
      if (!hasHighlight) return EDGE_BRIGHT
      const s = typeof link.source === 'string' ? link.source : link.source.id
      const t = typeof link.target === 'string' ? link.target : link.target.id
      return highlighted.has(s) && highlighted.has(t) ? 'rgba(56,189,248,0.65)' : EDGE_DIM
    },
    [hasHighlight, highlighted],
  )

  // Frame the camera on first paint so the graph is not a distant speck.
  useEffect(() => {
    if (visibleNodes.length === 0) return
    const timer = window.setTimeout(() => {
      fgRef.current?.cameraPosition?.({ x: 0, y: 0, z: 620 }, 900)
    }, 600)
    return () => window.clearTimeout(timer)
  }, [visibleNodes.length])

  const data = useMemo(() => ({ nodes: visibleNodes, links }), [visibleNodes, links])

  return (
    <ForceGraph3D
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ref={fgRef as any}
      graphData={data}
      backgroundColor="rgba(0,0,0,0)"
      showNavInfo={false}
      nodeLabel={(node: object) => {
        const n = node as StageGraphNode
        return `<div style="font-family:monospace;font-size:11px;padding:2px 4px">${n.name}<br/><span style="opacity:.6">${n.label} · ${n.id}</span></div>`
      }}
      nodeColor={nodeColor as (n: object) => string}
      nodeVal={nodeVal as (n: object) => number}
      nodeOpacity={1}
      nodeResolution={14}
      linkColor={linkColor as (l: object) => string}
      linkWidth={0.4}
      linkDirectionalParticles={0}
      linkOpacity={0.5}
      onNodeClick={(node: object) => {
        const n = node as StageGraphNode
        onSelect(n.id === selectedId ? null : n.id)
        const dist = 90
        const mag = Math.hypot(n.x ?? 1, n.y ?? 1, n.z ?? 1) || 1
        fgRef.current?.cameraPosition?.(
          {
            x: (n.x ?? 0) * (1 + dist / mag),
            y: (n.y ?? 0) * (1 + dist / mag),
            z: (n.z ?? 0) * (1 + dist / mag),
          },
          700,
        )
      }}
      onBackgroundClick={() => onSelect(null)}
      cooldownTicks={140}
      d3AlphaDecay={0.022}
      d3VelocityDecay={0.32}
    />
  )
}

/** Convert "#38bdf8" into an rgba() string at the given alpha. */
function withAlpha(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return hex
  const int = parseInt(m[1], 16)
  const r = (int >> 16) & 255
  const g = (int >> 8) & 255
  const b = int & 255
  return `rgba(${r},${g},${b},${alpha})`
}
