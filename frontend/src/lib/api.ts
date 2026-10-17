/**
 * Knot API client.
 *
 * Two transports:
 *  - plain JSON for graph snapshots and one-shot endpoints
 *  - SSE over fetch() for scenario runs, so the agent trace can be rendered
 *    as it happens rather than after the run completes.
 *
 * The scenario endpoints are POST, which means EventSource cannot be used
 * (it only issues GET). The stream is therefore parsed by hand from the
 * response body, which also lets us cancel a run with AbortController.
 */

import type {
  AgentEvent,
  DemoFixture,
  GraphSnapshot,
  InvestigationResult,
  ScenarioKind,
  ScenarioResult,
  TriageResult,
  Provenance,
} from './types'

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

function url(path: string): string {
  return `${API_BASE}${path}`
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url(path), { signal })
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status} ${response.statusText}`)
  }
  return (await response.json()) as T
}

export function fetchGraph(limit = 2000): Promise<GraphSnapshot> {
  return getJson<GraphSnapshot>(`/api/graph?limit=${limit}`)
}

export function fetchHealth(): Promise<Record<string, unknown>> {
  return getJson('/api/health')
}

export function fetchFixtures(): Promise<Record<string, DemoFixture>> {
  return getJson('/api/demo/fixtures')
}

export function investigate(
  startNodeId: string,
  maxHops = 4,
  direction = 'out',
): Promise<InvestigationResult> {
  const params = new URLSearchParams({
    start_node_id: startNodeId,
    max_hops: String(maxHops),
    direction,
  })
  return getJson<InvestigationResult>(`/api/investigate?${params}`)
}

export function fetchProvenance(entityId: string): Promise<Provenance> {
  return getJson<Provenance>(`/api/provenance/${encodeURIComponent(entityId)}`)
}

export function fetchTriage(alertId: string): Promise<TriageResult> {
  return getJson<TriageResult>(`/api/triage/${encodeURIComponent(alertId)}`)
}

export interface ScenarioRequest {
  scenario: ScenarioKind
  prId?: string
  alertId?: string
  query?: string
  diffText?: string
  breakingSchemaChanges?: boolean
  latencyDeltaMs?: number
  maxHops?: number
}

function bodyFor(request: ScenarioRequest): Record<string, unknown> {
  switch (request.scenario) {
    case 'future_simulation':
      return {
        pr_id: request.prId ?? 'PR-812',
        diff_text: request.diffText ?? '',
        breaking_schema_changes: request.breakingSchemaChanges ?? false,
        simulated_latency_delta_ms: request.latencyDeltaMs ?? 0,
        max_hops: request.maxHops ?? 5,
        block_on_risk: true,
      }
    case 'live_incident':
      return { alert_id: request.alertId ?? 'ALT-904' }
    case 'archaeology':
      return { query: request.query ?? 'Why does PaymentService bypass Redis on retry?' }
  }
}

/**
 * Run a scenario and stream its agent events.
 *
 * SSE frames are separated by a blank line and may arrive split across chunks,
 * so a buffer is retained between reads and only complete frames are parsed.
 * The server sends one `data:` line per event.
 */
export async function streamScenario(
  request: ScenarioRequest,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<ScenarioResult | null> {
  const response = await fetch(url(`/api/stream/${request.scenario}`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bodyFor(request)),
    signal,
  })

  if (!response.ok || !response.body) {
    throw new Error(`stream failed: ${response.status} ${response.statusText}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalResult: ScenarioResult | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // Frames end with a blank line. Keep any trailing partial frame buffered.
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''

    for (const frame of frames) {
      const payload = frame
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (!payload) continue

      let event: AgentEvent
      try {
        event = JSON.parse(payload) as AgentEvent
      } catch {
        continue
      }
      if (event.kind === 'result' && event.result) {
        finalResult = event.result
      }
      if (event.kind === 'error') {
        throw new Error(event.error ?? 'scenario failed')
      }
      onEvent(event)
    }
  }

  return finalResult
}
