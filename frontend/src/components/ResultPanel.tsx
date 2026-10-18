/**
 * ResultPanel - what the run concluded, and the evidence for it.
 *
 * Ordered the way a reviewer reads it: verdict first, then the blast radius,
 * then the violated decisions, then the proof certificate. The certificate is
 * the differentiator - it is the machine-checkable justification for the
 * action, so it gets its own section rather than being buried in raw JSON.
 */

import type {
  ActionRecord,
  AdrViolation,
  Episode,
  ExpertScore,
  PlaybookMatch,
  ProofCertificate,
  ScenarioResult,
  SemanticRule,
  Suspect,
} from '../lib/types'

interface Props {
  result: ScenarioResult | null
  onNodeClick: (id: string) => void
}

function verdictClass(verdict: string | undefined): string {
  const v = (verdict ?? '').toLowerCase()
  if (v.includes('block')) return 'chip chip-danger'
  if (v.includes('risk') || v.includes('warn')) return 'chip chip-warn'
  if (v.includes('safe') || v.includes('resolv') || v.includes('proven')) return 'chip chip-ok'
  return 'chip'
}

export function ResultPanel({ result, onNodeClick }: Props) {
  if (!result) {
    return (
      <div className="inspector-body">
        <p className="dim mono" style={{ fontSize: 11.5 }}>
          Awaiting a completed run. Results, the blast radius and the proof
          certificate will appear here.
        </p>
      </div>
    )
  }

  const simulation = result.simulation
  const triage = result.triage
  const provenance = result.provenance

  return (
    <div className="inspector-body">
      {/* Verdict ------------------------------------------------------- */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">Verdict</span>
          <span className={verdictClass(simulation?.verdict ?? result.verdict)}>
            <span className="dot" />
            {simulation?.verdict ?? result.verdict ?? 'COMPLETE'}
          </span>
        </div>
        {simulation ? (
          <>
            <dl className="kv">
              <dt>Action</dt>
              <dd className="mono">{simulation.recommended_action}</dd>
              <dt>Confidence</dt>
              <dd className="mono">{simulation.confidence.toFixed(2)}</dd>
              <dt>Sim time</dt>
              <dd className="mono">{simulation.latency_ms.toFixed(1)} ms</dd>
              <dt>Shadow</dt>
              <dd className="mono dim">{simulation.shadow_graph}</dd>
            </dl>
            <div className="bar" style={{ marginTop: 10 }}>
              <span
                style={{
                  width: `${Math.round(simulation.confidence * 100)}%`,
                  background:
                    simulation.verdict === 'BLOCKED'
                      ? 'var(--danger)'
                      : simulation.verdict === 'AT_RISK'
                        ? 'var(--warn)'
                        : 'var(--ok)',
                }}
              />
            </div>
            {simulation.teardown_done ? (
              <p className="dim mono" style={{ fontSize: 10, marginTop: 8 }}>
                shadow tenant torn down after the run
              </p>
            ) : null}
          </>
        ) : null}
        {result.narrative ? (
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)', marginBottom: 0 }}>
            {result.narrative}
          </p>
        ) : null}
      </div>

      {/* Blast radius -------------------------------------------------- */}
      {simulation && simulation.impacted_services.length > 0 ? (
        <div className="card">
          <div className="card-head">
            <span className="card-title">Blast radius</span>
            <span className="chip">{simulation.impacted_services_count} services</span>
          </div>
          <ul className="list">
            {simulation.impacted_services.map((svc) => (
              <li key={svc.id}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    type="button"
                    className="node-pill"
                    onClick={() => onNodeClick(svc.id)}
                  >
                    {svc.id}
                  </button>
                  <span className="dim" style={{ fontSize: 12 }}>
                    {svc.name}
                  </span>
                  <span className="mono dim" style={{ marginLeft: 'auto', fontSize: 10 }}>
                    {svc.hop_distance} hop{svc.hop_distance === 1 ? '' : 's'}
                    {svc.tier ? ` · tier ${svc.tier}` : ''}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Mutation ------------------------------------------------------ */}
      {simulation && simulation.mutation_description.length > 0 ? (
        <div className="card">
          <div className="card-head">
            <span className="card-title">Applied mutation</span>
          </div>
          <ul className="list">
            {simulation.mutation_description.map((line) => (
              <li key={line} className="mono" style={{ fontSize: 11.5 }}>
                {line}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* ADR violations ------------------------------------------------ */}
      {simulation && simulation.adr_violations.length > 0 ? (
        <div className="card" style={{ borderColor: 'rgba(244,63,94,0.35)' }}>
          <div className="card-head">
            <span className="card-title danger-text">Decision violations</span>
          </div>
          {simulation.adr_violations.map((v, i) => (
            <AdrRow key={`${v.rule}-${i}`} violation={v} onNodeClick={onNodeClick} />
          ))}
        </div>
      ) : null}

      {/* Triage -------------------------------------------------------- */}
      {triage ? <TriageBlock suspects={triage.suspects} onNodeClick={onNodeClick} /> : null}

      {/* Memory recall ------------------------------------------------- */}
      {result.playbook_match ? <PlaybookBlock match={result.playbook_match} /> : null}
      {result.episodes && result.episodes.length > 0 ? (
        <EpisodeBlock episodes={result.episodes} />
      ) : null}
      {result.semantic_rules && result.semantic_rules.length > 0 ? (
        <SemanticBlock
          rules={result.semantic_rules}
          domain={result.semantic_domain}
        />
      ) : null}

      {/* Provenance ---------------------------------------------------- */}
      {provenance ? (
        <div className="card">
          <div className="card-head">
            <span className="card-title">Decision provenance</span>
          </div>
          {provenance.narrative ? (
            <p style={{ fontSize: 12.5, color: 'var(--text-dim)', marginTop: 0 }}>
              {provenance.narrative}
            </p>
          ) : null}
          <dl className="kv">
            <dt>Entity</dt>
            <dd className="mono">{provenance.entity_id}</dd>
          </dl>
          <ul className="list" style={{ marginTop: 8 }}>
            {provenance.timeline.slice(0, 8).map((entry, i) => (
              <li key={`${entry.node_id}-${i}`}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    type="button"
                    className="node-pill"
                    onClick={() => onNodeClick(entry.node_id)}
                  >
                    {entry.node_id}
                  </button>
                  <span className="mono dim" style={{ fontSize: 10 }}>
                    {entry.timestamp ? `${entry.timestamp.slice(0, 10)} · ` : ''}
                    {entry.event}
                  </span>
                </div>
                <div style={{ fontSize: 12.5, marginTop: 4 }}>{entry.title}</div>
                {entry.detail ? (
                  <div className="dim" style={{ fontSize: 11.5 }}>
                    {entry.detail}
                  </div>
                ) : null}
                {entry.source_url ? (
                  <a
                    href={entry.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="mono"
                    style={{ fontSize: 10.5, color: 'var(--present)' }}
                  >
                    source ↗
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Experts ------------------------------------------------------- */}
      {result.experts && result.experts.length > 0 ? (
        <div className="card">
          <div className="card-head">
            <span className="card-title">Domain experts</span>
          </div>
          <ul className="list">
            {result.experts.map((expert) => (
              <ExpertRow key={expert.handle} expert={expert} />
            ))}
          </ul>
        </div>
      ) : null}

      {/* Actions ------------------------------------------------------- */}
      {result.actions && result.actions.length > 0 ? (
        <div className="card">
          <div className="card-head">
            <span className="card-title">Actions executed</span>
          </div>
          <ul className="list">
            {result.actions.map((action, i) => (
              <ActionRow key={`${action.action_type}-${i}`} action={action} />
            ))}
          </ul>
        </div>
      ) : null}

      {/* Proof certificate --------------------------------------------- */}
      {simulation && simulation.certificates.length > 0 ? (
        <CertificateBlock certificate={simulation.certificates[0]} onNodeClick={onNodeClick} />
      ) : null}

      {/* Raw ----------------------------------------------------------- */}
      <details className="raw">
        <summary>Raw result JSON</summary>
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </div>
  )
}

function AdrRow({
  violation,
  onNodeClick,
}: {
  violation: AdrViolation
  onNodeClick: (id: string) => void
}) {
  const adrId = violation.adr?.id
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {adrId ? (
          <button type="button" className="node-pill" onClick={() => onNodeClick(adrId)}>
            {adrId}
          </button>
        ) : null}
        <span className={`chip ${violation.severity === 'critical' ? 'chip-danger' : 'chip-warn'}`}>
          {violation.severity}
        </span>
      </div>
      <div style={{ fontSize: 12.5, marginTop: 6 }}>{violation.rule}</div>
      <div className="dim" style={{ fontSize: 11.5, marginTop: 3 }}>
        {violation.evidence}
      </div>
    </div>
  )
}

function TriageBlock({
  suspects,
  onNodeClick,
}: {
  suspects: Suspect[]
  onNodeClick: (id: string) => void
}) {
  if (!suspects || suspects.length === 0) return null
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Ranked suspects</span>
      </div>
      <ul className="list">
        {suspects.map((s) => (
          <li key={s.service.id}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button
                type="button"
                className="node-pill"
                onClick={() => onNodeClick(s.service.id)}
              >
                {s.service.id}
              </button>
              <span className="dim" style={{ fontSize: 12 }}>
                {s.service.name}
              </span>
              <span className="mono dim" style={{ marginLeft: 'auto', fontSize: 10 }}>
                score {s.score.toFixed(2)} · {s.hop_distance} hop
              </span>
            </div>
            {s.pr ? (
              <div className="dim mono" style={{ fontSize: 10.5, marginTop: 4 }}>
                suspect PR {s.pr.id}
                {s.author ? ` by ${s.author.handle}` : ''}
              </div>
            ) : null}
            {s.reasons && s.reasons.length > 0 ? (
              <div className="dim" style={{ fontSize: 11.5, marginTop: 3 }}>
                {s.reasons.join(' · ')}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}

function PlaybookBlock({ match }: { match: PlaybookMatch }) {
  // action_steps_json is a JSON string, not an array. A malformed value must
  // not take the panel down, so parsing is guarded.
  let steps: Array<{ step?: number; action?: string; target?: string; detail?: string }> = []
  try {
    const parsed = JSON.parse(match.playbook.action_steps_json)
    if (Array.isArray(parsed)) steps = parsed
  } catch {
    steps = []
  }

  return (
    <div className="card" style={{ borderColor: 'rgba(167,139,250,0.35)' }}>
      <div className="card-head">
        <span className="card-title" style={{ color: 'var(--memory)' }}>
          Procedural memory recalled
        </span>
        <span className="chip chip-ok">
          {(match.playbook.success_rate * 100).toFixed(0)}%
        </span>
      </div>
      <div style={{ fontSize: 12.5 }}>{match.playbook.name}</div>
      <div className="dim mono" style={{ fontSize: 10.5, marginTop: 4 }}>
        {match.playbook.id} · overlap {(match.similarity * 100).toFixed(1)}%
        {match.playbook.uses ? ` · used ${match.playbook.uses}×` : ''}
      </div>
      {steps.length > 0 ? (
        <ol style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12 }}>
          {steps.map((step, i) => (
            <li key={`${step.action}-${i}`} style={{ marginBottom: 4 }}>
              <span className="mono" style={{ fontSize: 11.5 }}>
                {step.action}
              </span>
              {step.target ? <span className="dim mono" style={{ fontSize: 10.5 }}> → {step.target}</span> : null}
              {step.detail ? (
                <div className="dim" style={{ fontSize: 11.5 }}>
                  {step.detail}
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  )
}

function EpisodeBlock({ episodes }: { episodes: Episode[] }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Episodic memory</span>
        <span className="chip">{episodes.length} recalled</span>
      </div>
      <ul className="list">
        {episodes.map((ep) => (
          <li key={ep.incident.id}>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--memory)' }}>
              {ep.incident.id} · similarity {ep.similarity.toFixed(2)}
              {ep.incident.severity ? ` · ${ep.incident.severity}` : ''}
            </div>
            <div style={{ fontSize: 12.5 }}>{ep.incident.summary}</div>
            {ep.incident.mttr_seconds ? (
              <div className="dim mono" style={{ fontSize: 10.5 }}>
                MTTR {Math.round(ep.incident.mttr_seconds / 60)} min
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}

function SemanticBlock({
  rules,
  domain,
}: {
  rules: SemanticRule[]
  domain?: string
}) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Semantic memory</span>
        <span className="chip">
          {rules.length} invariant{rules.length === 1 ? '' : 's'}
          {domain ? ` · ${domain}` : ''}
        </span>
      </div>
      <ul className="list">
        {rules.map((rule) => (
          <li key={rule.id}>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--memory)' }}>
              {rule.id}
              {rule.enforcement_level ? ` · ${rule.enforcement_level}` : ''}
            </div>
            <div style={{ fontSize: 12.5 }}>{rule.invariant_statement}</div>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ExpertRow({ expert }: { expert: ExpertScore }) {
  return (
    <li>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="mono" style={{ fontSize: 12 }}>
          {expert.handle}
        </span>
        {expert.team ? (
          <span className="dim mono" style={{ fontSize: 10 }}>
            {expert.team}
          </span>
        ) : null}
        <span className="mono dim" style={{ marginLeft: 'auto', fontSize: 10 }}>
          {expert.ownership_score.toFixed(2)}
        </span>
      </div>
      <div className="bar">
        <span style={{ width: `${Math.round(expert.ownership_score * 100)}%` }} />
      </div>
      {expert.explanation ? (
        <div className="dim" style={{ fontSize: 11, marginTop: 4 }}>
          {expert.explanation}
        </div>
      ) : null}
    </li>
  )
}

function ActionRow({ action }: { action: ActionRecord }) {
  return (
    <li>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="mono" style={{ fontSize: 11.5 }}>
          {action.action_type}
        </span>
        <span className="dim mono" style={{ fontSize: 10 }}>
          {action.target_id}
        </span>
        <span className={`chip ${action.dry_run ? '' : 'chip-ok'}`} style={{ marginLeft: 'auto' }}>
          {action.status}
        </span>
      </div>
      {action.summary ? (
        <div className="dim" style={{ fontSize: 11.5, marginTop: 3 }}>
          {action.summary}
        </div>
      ) : null}
    </li>
  )
}

function CertificateBlock({
  certificate,
  onNodeClick,
}: {
  certificate: ProofCertificate
  onNodeClick: (id: string) => void
}) {
  return (
    <div className="card" style={{ borderColor: 'rgba(52,211,153,0.35)' }}>
      <div className="card-head">
        <span className="card-title ok-text">Proof certificate</span>
        <span className="chip chip-ok">
          <span className="dot" />
          {certificate.verdict}
        </span>
      </div>
      <dl className="kv">
        <dt>ID</dt>
        <dd className="mono">{certificate.certificate_id}</dd>
        <dt>Digest</dt>
        <dd className="mono">{certificate.digest}</dd>
        <dt>Hops</dt>
        <dd className="mono">{certificate.hop_count}</dd>
        <dt>Nodes</dt>
        <dd className="mono">{certificate.node_ids.length}</dd>
        <dt>Actionable</dt>
        <dd className="mono">{certificate.is_actionable ? 'yes' : 'no'}</dd>
      </dl>
      <p style={{ fontSize: 12.5, color: 'var(--text-dim)', marginBottom: 6 }}>
        {certificate.claim}
      </p>
      <div className="event-nodes">
        {certificate.node_ids.slice(0, 12).map((id) => (
          <button key={id} type="button" className="node-pill" onClick={() => onNodeClick(id)}>
            {id}
          </button>
        ))}
      </div>
      <details className="raw" style={{ marginTop: 10, marginBottom: 0 }}>
        <summary>Cypher that produced this</summary>
        <pre>{certificate.cypher}</pre>
      </details>
    </div>
  )
}
