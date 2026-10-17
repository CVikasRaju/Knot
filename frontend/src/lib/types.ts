/**
 * Wire types for the Knot REST/SSE API.
 *
 * These mirror src/api/main.py and src/agents/swarm.py. They are deliberately
 * narrow: only the fields the UI actually renders are typed, and unknown
 * fields are dropped rather than surfaced. Keeping the contract in one file
 * makes it obvious when the backend shape changes.
 */

/** A node as returned by GET /api/graph. */
export interface GraphNode {
  id: string
  label: string
  name: string
  track: string
  color: string
  tier: number | null
  severity: string | null
  status: string | null
  properties: Record<string, unknown>
}

export interface GraphEdge {
  source: string
  target: string
  type: string
  properties: Record<string, unknown>
  traversable: boolean
}

export interface GraphSnapshot {
  graph: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  counts?: Record<string, { nodes: number; edges: number }>
}

/** One node inside an explainable multi-hop path. */
export interface PathNode {
  id: string
  label: string
  name: string
}

export interface PathRecord {
  nodes: PathNode[]
  edges: string[]
  hop_count: number
  chain: string
}

export interface InvestigationResult {
  start_node_id: string
  nodes: PathNode[]
  edges: string[]
  paths: PathRecord[]
  hop_count: number
  chain: string
}

/** A proof certificate - the audit artefact behind every mutating action. */
export interface ProofCertificate {
  certificate_id: string
  claim: string
  verdict: string
  confidence: number
  graph: string
  node_ids: string[]
  edges?: string[]
  cypher: string
  hop_count: number
  evidence?: string[]
  digest: string
  created_at: string
  is_actionable: boolean
}

export interface ImpactedService {
  id: string
  name: string
  tier: number | null
  sla_ms?: number
  hop_distance: number
}

export interface AdrViolation {
  adr: { id: string; code?: string; title?: string } | null
  rule: string
  severity: string
  evidence: string
}

export interface CascadingPath {
  origin?: string
  path?: string[]
  hops?: number
  chain?: string
}

export interface SimulationResult {
  simulation_id: string
  shadow_graph: string
  pr_id: string
  verdict: 'SAFE' | 'AT_RISK' | 'BLOCKED' | string
  recommended_action: string
  confidence: number
  latency_ms: number
  mutation_description: string[]
  impacted_services: ImpactedService[]
  impacted_services_count: number
  tier1_impacted: string[]
  adr_violations: AdrViolation[]
  cascading_path_trace: CascadingPath[]
  certificates: ProofCertificate[]
  narrative: string
  teardown_done: boolean
}

export interface ActionRecord {
  action_id: string
  action_type: string
  target_id: string
  status: string
  summary: string
  dry_run: boolean
  certificate_id?: string
  external_ref?: string
  payload?: Record<string, unknown>
  at?: string
}

export interface ExpertScore {
  handle: string
  name: string
  team: string
  ownership_score: number
  signals: Record<string, unknown>
  explanation: string
  person?: Record<string, unknown>
}

export interface ProvenanceTimelineEntry {
  event: string
  node_id: string
  label: string
  title: string
  timestamp?: string
  detail?: string
  source_url?: string | null
}

export interface Provenance {
  entity_id: string
  entity: Record<string, unknown>
  timeline: ProvenanceTimelineEntry[]
  governing_adrs?: Array<Record<string, unknown>>
  decisions?: Array<Record<string, unknown>>
  narrative?: string
}

/** An episodic memory as returned inside a triage or scenario result. */
export interface Episode {
  incident: {
    id: string
    summary: string
    occurred_at?: string
    root_cause_hash?: string
    mttr_seconds?: number
    severity?: string
  }
  similarity: number
  matched_tokens?: string[]
}

/** A procedural playbook match. ``action_steps_json`` is a JSON *string*. */
export interface PlaybookMatch {
  playbook: {
    id: string
    name: string
    trigger_signature?: string
    action_steps_json: string
    success_rate: number
    uses?: number
  }
  similarity: number
  matched_tokens?: string[]
  rationale?: string
}

export interface Suspect {
  service: { id: string; name: string; tier?: number | null }
  pr?: { id: string; title?: string; state?: string } | null
  author?: { handle: string; name?: string } | null
  hop_distance: number
  causal_chain?: string[]
  score: number
  reasons?: string[]
}

export interface TriageResult {
  alert_id: string
  alert: Record<string, unknown>
  symptom_service?: { id: string; name: string } | null
  suspects: Suspect[]
  signature: string
  blast?: Record<string, unknown>
  trace?: Record<string, unknown>
  narrative?: string
}

export interface SessionStep {
  step_index: number
  agent_name: string
  tool_called: string
  observation: string
  inspected_node_ids?: string[]
}

export interface ScenarioResult {
  session_id: string
  scenario: string
  pr_id?: string
  alert_id?: string
  query?: string
  resolved_entity_id?: string
  simulation?: SimulationResult
  triage?: TriageResult
  provenance?: Provenance
  experts?: ExpertScore[]
  episodes?: Episode[]
  playbook_match?: PlaybookMatch | null
  certificates?: ProofCertificate[]
  actions?: ActionRecord[]
  narrative?: string
  verdict?: string
  steps?: SessionStep[]
}

/** An SSE event emitted while a scenario runs. */
export interface AgentEvent {
  kind: string
  session_id?: string
  scenario?: string
  agent?: string
  action?: string
  observation?: string
  inspected_node_ids?: string[]
  verdict?: string
  result?: ScenarioResult
  error?: string
  [key: string]: unknown
}

export type ScenarioKind = 'future_simulation' | 'live_incident' | 'archaeology'

export interface DemoFixture {
  title: string
  diff: string
  breaking_schema_changes: boolean
  simulated_latency_delta_ms: number
}
