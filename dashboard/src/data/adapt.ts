// Actor state (contracts/event.ts) -> IncidentView. The actor owns status, severity, actions and
// the trace; this only reshapes it and fills in the six-step stepper.
import type { CameraState, Incident, SiteState, TraceEntry } from '../contracts/event'
import type { IncidentView, StepName, TraceStep } from '../contracts/incident'
import type { ActivityItem } from '../contracts/agents'

const STEPS: Exclude<StepName, 'detector'>[] = ['triage', 'coach', 'fixer', 'verifier', 'reporter']

type Out = Record<string, unknown> & { status?: string }
const str = (v: unknown) => (typeof v === 'string' && v ? v : null)
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function ms(a?: string | null, b?: string | null): number | null {
  if (!a || !b) return null
  const d = Date.parse(b) - Date.parse(a)
  return Number.isFinite(d) && d >= 0 ? d : null
}

function summarize(step: string, out: Out | undefined): string | null {
  if (!out) return null
  switch (step) {
    case 'triage': {
      const sev = num(out.severity)
      const head = out.status === 'dismissed' ? 'Not confirmed' : out.status === 'unverified' ? 'Could not verify' : 'Confirmed'
      return [head + (sev ? `, severity ${sev}.` : '.'), str(out.reason)].filter(Boolean).join(' ')
    }
    case 'coach': {
      const s = str(out.summary) ?? str(out.message)
      const fix = str(out.recommended_fix)
      return [s, fix && `Fix: ${fix}`].filter(Boolean).join(' ') || null
    }
    case 'fixer':
      return str(out.summary) ?? (Array.isArray(out.actions) ? `${out.actions.length} actions` : null)
    case 'verifier':
      return str(out.summary) ?? str(out.message)
    case 'reporter':
      return str(out.text) ?? str(out.report) ?? str(out.message) ?? (str(out.channel) ? `Posted to ${out.channel}` : null)
  }
  return null
}

export function adaptIncident(inc: Incident, cam: CameraState, log: ActivityItem[] = []): IncidentView {
  const relay = (inc.relay ?? {}) as Record<string, Out | undefined>
  const byAgent = new Map<string, TraceEntry>()
  for (const t of inc.trace ?? []) byAgent.set(t.agent, t) // newest entry per agent wins
  const dismissed = inc.status === 'dismissed'
  const closed = inc.status === 'closed'

  const detector: TraceStep = {
    agent: 'detector',
    state: 'done',
    model: 'pose + rules (CV)',
    started_at: inc.opened_at,
    ended_at: inc.opened_at,
    duration_ms: null,
    output_summary: str(relay.detector?.detail) ?? `${inc.type.replace(/_/g, ' ')} in ${inc.zone_id.replace(/^zone-/, '').replace(/-/g, ' ')}`,
    error: null,
    tokens: null,
  }
  const trace: TraceStep[] = [detector]
  let prevDone = true
  for (const step of STEPS) {
    const out = relay[step]
    const t = byAgent.get(step)
    const failed = out?.status === 'failed' || !!t?.error
    const reported = !!out || !!t?.ended_at
    let state: TraceStep['state'] = reported ? (failed ? 'failed' : 'done') : 'pending'
    if (!reported && (dismissed || closed) && step !== 'triage') state = 'skipped'
    else if (!reported && prevDone) state = 'running'
    const tokens = (num(out?.input_tokens) ?? 0) + (num(out?.output_tokens) ?? 0)
    trace.push({
      agent: step,
      state,
      model: t?.model ?? str(out?.model),
      started_at: t?.started_at ?? null,
      ended_at: t?.ended_at ?? null,
      duration_ms: ms(t?.started_at, t?.ended_at),
      output_summary: summarize(step, out) ?? t?.output_summary ?? null,
      error: t?.error ?? (failed ? str(out?.error) ?? 'step failed' : null),
      tokens: tokens || null,
    })
    prevDone = state === 'done' || state === 'failed'
  }

  const triage = relay.triage
  const coach = relay.coach
  const verifier = relay.verifier
  const keyframes = inc.keyframes ?? []
  return {
    id: inc.incident_id,
    event: {
      id: inc.event_ids?.[0] ?? inc.incident_id,
      camera: inc.camera_id ?? cam.camera_id,
      type: inc.type,
      zone: inc.zone_id,
      timestamp: inc.opened_at,
      frame_url: keyframes[1] ?? keyframes[0] ?? null,
      clip_url: inc.clip_url ?? null,
      keyframes,
      confidence: num(relay.detector?.confidence),
      detail: str(relay.detector?.detail) ?? str(triage?.reason),
    },
    status: inc.status,
    severity: inc.severity ?? 3,
    severity_from: inc.severity ? 'triage' : 'detector',
    triage_reason: str(triage?.reason),
    coach: coach
      ? {
          summary: str(coach.summary) ?? str(coach.message) ?? '',
          recommended_fix: str(coach.recommended_fix) ?? '',
          standard_tag: str(coach.standard_tag) ?? '',
        }
      : null,
    verify_summary: str(verifier?.summary) ?? str(verifier?.message),
    after_frame_url: str(verifier?.after_frame_url),
    low_confidence: !!inc.low_confidence,
    verify_until: inc.verify_until ?? null,
    actions: inc.actions ?? [],
    trace,
    log,
    acknowledged_by: closed ? 'closed' : null,
    updated_at: inc.updated_at,
  }
}

export function adaptSite(site: SiteState | null, activity: ActivityItem[]): IncidentView[] {
  if (!site) return []
  const byId = new Map<string, ActivityItem[]>()
  for (const it of activity) {
    const ids = new Set([it.event_id, ...(it.action?.event_ids ?? [])].filter((x): x is string => !!x))
    for (const id of ids) byId.set(id, [...(byId.get(id) ?? []), it])
  }
  const out: IncidentView[] = []
  for (const cam of site.cameras) {
    for (const inc of cam.incidents) {
      const log = [inc.incident_id, ...inc.event_ids].flatMap((id) => byId.get(id) ?? [])
      out.push(adaptIncident(inc, cam, [...new Map(log.map((l) => [l.id, l])).values()]))
    }
  }
  return out.sort((a, b) => Date.parse(b.event.timestamp) - Date.parse(a.event.timestamp))
}

/** A version for the whole site: changes whenever any camera changes. */
export const siteVersion = (s: SiteState | null) => (s ? s.cameras.reduce((n, c) => n + c.version, 0) : 0)
