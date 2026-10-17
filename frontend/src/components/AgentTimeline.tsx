/**
 * AgentTimeline - the live reasoning trace.
 *
 * Renders the SSE events as they arrive. Each agent start/finish pair becomes
 * one entry so the reader sees "what the agent decided", not two raw frames.
 * Inspected node ids are rendered as clickable pills, which is how the viewer
 * jumps from a claim to the graph node that supports it.
 */

import { useEffect, useRef } from 'react'
import type { AgentEvent } from '../lib/types'

export interface TraceEntry {
  key: string
  agent: string
  action: string
  observation: string
  nodes: string[]
  status: 'running' | 'done' | 'error'
}

interface Props {
  entries: TraceEntry[]
  onNodeClick: (id: string) => void
}

const AGENT_LABEL: Record<string, string> = {
  simulator: 'Simulator',
  archaeologist: 'Archaeologist',
  commander: 'Commander',
  triage: 'Triage',
  dispatcher: 'Dispatcher',
  indexer: 'Indexer',
}

export function AgentTimeline({ entries, onNodeClick }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null)

  // Follow the tail as new events land, the way a log viewer does.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [entries.length])

  if (entries.length === 0) {
    return (
      <div className="timeline">
        <p className="dim mono" style={{ fontSize: 11.5 }}>
          No active session. Run a scenario to watch the swarm reason over the
          graph.
        </p>
      </div>
    )
  }

  return (
    <div className="timeline">
      <div className="events">
        {entries.map((entry) => (
          <div
            key={entry.key}
            className={`event ${entry.status === 'running' ? 'event-running' : ''}`}
          >
            <div className="event-marker">
              {entry.status === 'running' ? '●' : entry.status === 'error' ? '✕' : '✓'}
            </div>
            <div className="event-agent">
              {AGENT_LABEL[entry.agent] ?? entry.agent}
              {entry.action ? <span className="tool"> · {entry.action}</span> : null}
            </div>
            {entry.observation ? (
              <div className="event-observation">{entry.observation}</div>
            ) : null}
            {entry.nodes.length > 0 ? (
              <div className="event-nodes">
                {entry.nodes.slice(0, 14).map((id) => (
                  <button
                    key={id}
                    type="button"
                    className="node-pill"
                    onClick={() => onNodeClick(id)}
                  >
                    {id}
                  </button>
                ))}
                {entry.nodes.length > 14 ? (
                  <span className="node-pill dim">+{entry.nodes.length - 14}</span>
                ) : null}
              </div>
            ) : null}
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  )
}

/**
 * Fold a raw event stream into renderable trace entries.
 *
 * A running entry is created on `agent_start` and completed in place by the
 * matching `agent_finish`, which keeps the list length stable instead of
 * flickering between two rows per agent.
 */
export function reduceEvents(events: AgentEvent[]): TraceEntry[] {
  const entries: TraceEntry[] = []
  let counter = 0

  for (const event of events) {
    if (event.kind === 'agent_start') {
      counter += 1
      entries.push({
        key: `start-${counter}`,
        agent: String(event.agent ?? 'agent'),
        action: String(event.action ?? ''),
        observation: '',
        nodes: [],
        status: 'running',
      })
      continue
    }
    if (event.kind === 'agent_finish') {
      const agent = String(event.agent ?? 'agent')
      // Complete the most recent still-running entry for this agent.
      for (let i = entries.length - 1; i >= 0; i -= 1) {
        if (entries[i].agent === agent && entries[i].status === 'running') {
          entries[i] = {
            ...entries[i],
            observation: String(event.observation ?? ''),
            nodes: (event.inspected_node_ids as string[] | undefined) ?? [],
            status: 'done',
          }
          break
        }
      }
      continue
    }
    if (event.kind === 'error') {
      counter += 1
      entries.push({
        key: `err-${counter}`,
        agent: 'system',
        action: 'error',
        observation: String(event.error ?? 'unknown error'),
        nodes: [],
        status: 'error',
      })
    }
  }

  return entries
}
