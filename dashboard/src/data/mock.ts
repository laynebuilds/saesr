// Mock actor: emits the same SiteState the durable actors send, following the actor's rules
// (durable/README.md: state machine, counters, compliance penalties), so every page can be built
// and rehearsed without the backend. Every 45 s it plays three incidents:
//   A unsafe lift (cam-01)   -> triage -> coach -> fixer (3 auto actions) -> compliant observation -> resolved
//   B missing gloves (cam-02) -> triage dismisses (false positive filtered)
//   C zone breach (cam-01)   -> fixer proposes slow_machine, waits for a human Approve -> resolved
// Footage is Pexels stock (public/footage/CREDITS.md). Model names and latencies are placeholders.
import type { Action, ActionKind, CameraState, EventType, Incident, IncidentStatus, SiteState, TraceEntry } from '../contracts/event'
import type { ActivityItem } from '../contracts/agents'
import { SITE_ID } from '../contracts/event'
import { addActivity, applySite, markDown, setState } from './store'
import type { Source } from './source'
import { asset } from './config'

const HAIKU = 'vision-model-fast'
const SONNET = 'vision-model'
const VERIFY_WINDOW_MS = 20_000

const CLIP: Record<string, string> = {
  unsafe_lift: asset('/footage/unsafe_lift'),
  zone_breach: asset('/footage/restricted_zone'),
  missing_hairnet: asset('/footage/no_hairnet'),
  missing_gloves: asset('/footage/no_gloves'),
}
const DETAIL: Record<string, string> = {
  unsafe_lift: 'back bent with straight legs while lifting',
  zone_breach: 'person inside machine zone A while it is running',
  missing_hairnet: 'hair uncovered at the open product line',
  missing_gloves: 'bare hands near open product',
}
const TOPIC: Record<string, string> = {
  unsafe_lift: 'Lift with the legs, keep the load close',
  zone_breach: 'Stay behind the line while machine A runs',
  missing_hairnet: 'Hair restraint before entering the line',
  missing_gloves: 'Gloves on for any food contact',
}
const STD: Record<string, string> = {
  unsafe_lift: 'OSHA ergonomics',
  zone_breach: 'OSHA PPE',
  missing_hairnet: 'GMP',
  missing_gloves: 'HACCP',
}
const POLICY: Record<string, ActionKind[]> = {
  unsafe_lift: ['assign_coaching', 'notify_floor_display', 'open_corrective_record'],
  zone_breach: ['notify_floor_display', 'open_corrective_record', 'slow_machine'],
  missing_hairnet: ['open_corrective_record', 'flag_batch_hold', 'assign_coaching', 'notify_floor_display'],
  missing_gloves: ['open_corrective_record', 'flag_batch_hold', 'assign_coaching', 'notify_floor_display'],
}

const iso = (t = Date.now()) => new Date(t).toISOString()
let seq = 0
let actSeq = 0

/** Stand-in for the relay's action id (sha1 of incident:kind, 16 hex). */
function actionId(incidentId: string, kind: ActionKind): string {
  let h1 = 0x811c9dc5
  let h2 = 0x01000193
  for (const c of `${incidentId}:${kind}`) {
    h1 = Math.imul(h1 ^ c.charCodeAt(0), 16777619)
    h2 = Math.imul(h2 ^ c.charCodeAt(0), 2246822519)
  }
  return ((h1 >>> 0).toString(16).padStart(8, '0') + (h2 >>> 0).toString(16).padStart(8, '0')).slice(0, 16)
}

// ---------------------------------------------------------------------------
// Camera actors

function newCamera(id: string): CameraState {
  return {
    camera_id: id,
    site_id: SITE_ID,
    status: 'online',
    last_heartbeat: iso(),
    version: 0,
    recovered_from_version: null,
    compliance_score: 100,
    counters: {},
    false_positives_filtered: 0,
    autonomous_fixes: 0,
    incidents: [],
  }
}
const cams: Record<string, CameraState> = { 'cam-01': newCamera('cam-01'), 'cam-02': newCamera('cam-02') }
let crashed = false

const PENALTY: Partial<Record<IncidentStatus, number>> = { verifying: 4, confirmed: 4, unverified: 4, awaiting_approval: 4, recurring: 8, actioned: 2, resolved: 2 }

function score(c: CameraState, now = Date.now()): number {
  let p = 0
  for (const i of c.incidents) {
    const k = PENALTY[i.status] ?? 0
    if (!k) continue
    let w = k * (i.severity ?? 3)
    if (i.status === 'resolved') w *= Math.max(0, 1 - (now - Date.parse(i.closed_at ?? i.updated_at)) / 600_000)
    p += w
  }
  return Math.round(Math.max(0, 100 - p) * 10) / 10
}

function site(): SiteState {
  const cameras = Object.values(cams)
  return {
    site_id: SITE_ID,
    site_score: Math.round((cameras.reduce((s, c) => s + c.compliance_score, 0) / cameras.length) * 10) / 10,
    active_incidents: cameras.reduce((n, c) => n + c.incidents.filter((i) => !['dismissed', 'resolved', 'closed'].includes(i.status)).length, 0),
    autonomous_fixes: cameras.reduce((n, c) => n + c.autonomous_fixes, 0),
    cameras: structuredClone(cameras),
  }
}

function changed(c: CameraState) {
  c.version += 1
  c.compliance_score = score(c)
  c.last_heartbeat = iso()
  if (!crashed) applySite(site())
}

function find(id: string): [CameraState, Incident] {
  for (const c of Object.values(cams)) {
    const i = c.incidents.find((x) => x.incident_id === id)
    if (i) return [c, i]
  }
  throw new Error(`no incident ${id}`)
}

function say(agent: string, text: string, incidentId: string) {
  const item: ActivityItem = { id: `act_mock_${++actSeq}`, ts: iso(), kind: 'message', agent, to: '#safety-floor', text, event_id: incidentId, relay_id: null, action: null }
  addActivity([item])
}

// ---------------------------------------------------------------------------
// The actor's message handlers, simplified

function detection(camera: string, type: EventType, zone: string, at = Date.now()): string {
  const c = cams[camera]
  const eventId = `evt-mock-${at}-${++seq}`
  c.counters[type] = (c.counters[type] ?? 0) + 1
  const id = `inc-${eventId}`
  const base = CLIP[type]
  c.incidents.unshift({
    incident_id: id,
    camera_id: camera,
    type,
    zone_id: zone,
    status: 'verifying',
    severity: null,
    event_ids: [eventId],
    opened_at: iso(at),
    updated_at: iso(at),
    relay: { detector: { detail: DETAIL[type], confidence: Math.round((0.6 + Math.random() * 0.35) * 100) / 100 } } as Incident['relay'],
    actions: [],
    trace: [],
    keyframes: [`${base}.jpg`, `${base}.jpg`, `${base}.jpg`],
    clip_url: `${base}.mp4`,
    verify_until: null,
    low_confidence: false,
    closed_at: null,
  })
  c.incidents = c.incidents.slice(0, 200)
  changed(c)
  say(camera, `INCIDENT ${type} | ${zone} | ${camera}`, id)
  return id
}

function relayUpdate(id: string, step: string, status: string, output: Record<string, unknown>, model: string | null, ms: number, at = Date.now()) {
  const [c, i] = find(id)
  ;(i.relay as Record<string, unknown>)[step] = { status, ...output }
  const t: TraceEntry = { agent: step, model, started_at: iso(at - ms), ended_at: iso(at), output_summary: String(output.reason ?? output.summary ?? status) }
  i.trace.push(t)
  i.updated_at = iso(at)
  if (step === 'triage') {
    if (typeof output.severity === 'number') i.severity = Math.max(1, Math.min(5, output.severity))
    if (i.status === 'verifying') i.status = status === 'failed' ? 'unverified' : (status as IncidentStatus)
    if (i.status === 'dismissed') c.false_positives_filtered += 1
  }
  if (step === 'fixer') maybeActioned(i, at)
  changed(c)
  say(step, `${step.toUpperCase()} ${status}`, id)
}

function actionUpdate(id: string, a: Action, at = Date.now()) {
  const [c, i] = find(id)
  const prev = i.actions.find((x) => x.action_id === a.action_id)
  if (prev && (prev.status === 'done' || prev.status === 'rejected')) return // never re-executed
  const next = { ...prev, ...a }
  if (next.kind === 'slow_machine' && next.status === 'done' && !next.approved_by) next.status = 'awaiting_approval'
  if (next.status === 'done') {
    next.executed_at = next.executed_at ?? iso(at)
    c.autonomous_fixes += 1
  }
  i.actions = prev ? i.actions.map((x) => (x.action_id === a.action_id ? next : x)) : [...i.actions, next]
  if (next.status === 'awaiting_approval') i.status = 'awaiting_approval'
  i.updated_at = iso(at)
  maybeActioned(i, at)
  changed(c)
}

function maybeActioned(i: Incident, at: number) {
  const fixerDone = !!(i.relay as Record<string, unknown>).fixer
  if (fixerDone && i.actions.length && i.actions.every((a) => ['done', 'failed', 'rejected'].includes(a.status)) && !['resolved', 'recurring', 'closed', 'actioned'].includes(i.status)) {
    i.status = 'actioned'
    i.verify_until = iso(at + VERIFY_WINDOW_MS)
  }
}

function compliant(id: string, at = Date.now(), recurring = false) {
  const [c, i] = find(id)
  if (i.status !== 'actioned') return
  if (recurring) {
    i.status = 'recurring'
    c.counters[i.type] = (c.counters[i.type] ?? 0) + 1
  } else {
    c.counters.compliant_observation = (c.counters.compliant_observation ?? 0) + 1
    i.status = 'resolved'
    i.low_confidence = false
    i.closed_at = iso(at)
  }
  i.updated_at = iso(at)
  changed(c)
}

function act(id: string, type: string, kind: ActionKind, zone: string, gated = false): Action {
  const params: Record<string, unknown> =
    kind === 'assign_coaching'
      ? { audience: `${zone.replace('zone-', '').replace(/-/g, ' ')} shift lead and crew`, topic: TOPIC[type], due: 'before end of shift' }
      : kind === 'notify_floor_display'
        ? { display: zone.replace('zone-', ''), message: TOPIC[type] }
        : kind === 'open_corrective_record'
          ? { standard: STD[type], evidence: [`${CLIP[type]}.jpg`, `${CLIP[type]}.mp4`] }
          : kind === 'flag_batch_hold'
            ? { zone: zone.replace('zone-', '').replace(/-/g, ' '), window: '10 min around the lapse' }
            : { machine: 'machine A', target_speed: '50%', duration_s: 60 }
  return { action_id: actionId(id, kind), incident_id: id, kind, status: gated ? 'awaiting_approval' : 'pending', params, approved_by: null, executed_at: null }
}

// ---------------------------------------------------------------------------
// Scripted relay for live scenarios

const timers = new Set<ReturnType<typeof setTimeout>>()
function at(ms: number, fn: () => void) {
  const t = setTimeout(() => {
    timers.delete(t)
    if (crashed) return at(1000, fn) // actor down: work waits, nothing is dropped
    fn()
  }, ms)
  timers.add(t)
}

const approvals = new Map<string, (by: string) => void>()

function runChain(id: string, type: string, zone: string, opts: { severity: number; dismiss?: boolean; gated?: boolean }) {
  at(1500, () =>
    relayUpdate(
      id,
      'triage',
      opts.dismiss ? 'dismissed' : 'confirmed',
      opts.dismiss
        ? { severity: 1, reason: 'Gloves are on both hands; the detector misread light blue nitrile as skin.', input_tokens: 2190, output_tokens: 58 }
        : { severity: opts.severity, reason: `Clearly visible: ${DETAIL[type]}.`, input_tokens: 2240, output_tokens: 61 },
      HAIKU,
      1400,
    ),
  )
  if (opts.dismiss) return
  at(4000, () =>
    relayUpdate(id, 'coach', 'done', { summary: `${DETAIL[type][0].toUpperCase()}${DETAIL[type].slice(1)}.`, recommended_fix: `${TOPIC[type]}.`, standard_tag: STD[type], input_tokens: 410, output_tokens: 112 }, SONNET, 2500),
  )
  const kinds = POLICY[type].filter((k) => k !== 'slow_machine' || opts.gated)
  at(6800, () => {
    for (const k of kinds) actionUpdate(id, act(id, type, k, zone, k === 'slow_machine'))
    relayUpdate(id, 'fixer', 'done', { summary: `${kinds.length} actions: ${kinds.map((k) => k.replace(/_/g, ' ')).join(', ')}`, input_tokens: 690, output_tokens: 235 }, SONNET, 2700)
  })
  kinds
    .filter((k) => k !== 'slow_machine')
    .forEach((k, n) => at(7600 + n * 500, () => actionUpdate(id, { ...act(id, type, k, zone), status: 'done' })))
  const verify = (delay: number) => {
    at(delay, () =>
      relayUpdate(
        id,
        'verifier',
        'done',
        { summary: `No ${DETAIL[type].split(' ').slice(0, 3).join(' ')} seen again in the zone; a compliant observation followed.`, after_frame_url: `${CLIP[type]}_after.jpg`, input_tokens: 180, output_tokens: 36 },
        HAIKU,
        800,
      ),
    )
    at(delay + 400, () => compliant(id))
    at(delay + 1400, () => relayUpdate(id, 'reporter', 'done', { text: `${type.replace(/_/g, ' ')} in ${zone.replace('zone-', '')}: fixed and verified.`, channel: '#safety-alerts' }, null, 220))
  }
  if (opts.gated) {
    approvals.set(actionId(id, 'slow_machine'), (by) => {
      actionUpdate(id, { ...act(id, type, 'slow_machine', zone), status: 'approved', approved_by: by })
      at(600, () => actionUpdate(id, { ...act(id, type, 'slow_machine', zone), status: 'done', approved_by: by }))
      verify(9000)
    })
  } else verify(22_000)
}

function cycle() {
  at(500, () => runChain(detection('cam-01', 'unsafe_lift', 'zone-pack-line'), 'unsafe_lift', 'zone-pack-line', { severity: 4 }))
  at(12_000, () => runChain(detection('cam-02', 'missing_gloves', 'zone-handwash'), 'missing_gloves', 'zone-handwash', { severity: 2, dismiss: true }))
  at(20_000, () => runChain(detection('cam-01', 'zone_breach', 'zone-machine-a'), 'zone_breach', 'zone-machine-a', { severity: 5, gated: true }))
}

// ---------------------------------------------------------------------------
// History: ~6 h of finished incidents, written straight into the actors' state

function seedHistory() {
  const now = Date.now()
  const rnd = (a: number, b: number) => a + Math.random() * (b - a)
  const mix: [EventType, string, string, number][] = [
    ['unsafe_lift', 'cam-01', 'zone-pack-line', 9],
    ['zone_breach', 'cam-01', 'zone-machine-a', 5],
    ['missing_gloves', 'cam-02', 'zone-handwash', 8],
    ['missing_hairnet', 'cam-02', 'zone-handwash', 7],
  ]
  for (const [type, camera, zone, n] of mix) {
    for (let k = 0; k < n; k++) {
      const t0 = now - (Math.random() < 0.45 ? rnd(0.2, 2) : rnd(2, 6)) * 3600_000
      const id = detection(camera, type, zone, t0)
      const [c, i] = find(id)
      let t = t0 + rnd(200, 500)
      const dismiss = Math.random() < (type === 'missing_gloves' ? 0.3 : 0.12)
      const sev = dismiss ? 1 : Math.min(5, (type === 'unsafe_lift' || type === 'zone_breach' ? 4 : 3) - (Math.random() < 0.4 ? 1 : 0) + (Math.random() < 0.2 ? 1 : 0))
      const step = (name: string, status: string, out: Record<string, unknown>, model: string | null, a: number, b: number) => {
        const ms = rnd(a, b)
        t += ms
        relayUpdate(id, name, status, out, model, ms, t)
      }
      step('triage', dismiss ? 'dismissed' : 'confirmed', { severity: sev, reason: dismiss ? 'The required item is present; the detector misread it.' : `Clearly visible: ${DETAIL[type]}.`, input_tokens: 2200, output_tokens: 60 }, HAIKU, 1100, 2600)
      if (dismiss) continue
      step('coach', 'done', { summary: `${DETAIL[type][0].toUpperCase()}${DETAIL[type].slice(1)}.`, recommended_fix: `${TOPIC[type]}.`, standard_tag: STD[type], input_tokens: 410, output_tokens: 115 }, SONNET, 1800, 3400)
      const kinds = POLICY[type].filter((k) => k !== 'slow_machine' || sev >= 5)
      const fxStart = t
      for (const k of kinds) {
        const a = act(id, type, k, zone)
        const approved = k === 'slow_machine'
        actionUpdate(id, { ...a, status: 'done', approved_by: approved ? 'floor manager' : null, executed_at: iso(fxStart + rnd(2500, approved ? 30_000 : 4500)) }, fxStart)
      }
      step('fixer', 'done', { summary: `${kinds.length} actions`, input_tokens: 690, output_tokens: 230 }, SONNET, 2000, 3600)
      const recurring = Math.random() < 0.1
      t += rnd(15_000, 40_000)
      step('verifier', 'done', { summary: recurring ? 'The same violation was seen again in the zone.' : 'A compliant observation followed; no repeat in the zone.', after_frame_url: `${CLIP[type]}_after.jpg`, input_tokens: 180, output_tokens: 36 }, HAIKU, 600, 1100)
      compliant(id, t, recurring)
      step('reporter', 'done', { text: `${type.replace(/_/g, ' ')}: ${recurring ? 'seen again, escalated' : 'fixed and verified'}.`, channel: '#safety-alerts' }, null, 150, 400)
      if (!recurring && Math.random() < 0.3) {
        i.status = 'closed'
        i.closed_at = iso(t + 60_000)
      }
      void c
    }
  }
  for (const c of Object.values(cams)) c.incidents.sort((a, b) => Date.parse(b.opened_at) - Date.parse(a.opened_at))
}

export function createMockSource(): Source {
  let loop: ReturnType<typeof setInterval> | null = null
  let tick: ReturnType<typeof setInterval> | null = null
  return {
    start() {
      setState({ mode: 'mock', transport: 'mock', conn: 'live' })
      setState({
        agentStatus: { enabled: true, connected: true, relay_url: 'mock', channel: 'safety-floor', agents: ['cam-01', 'cam-02', 'triage', 'coach', 'fixer', 'verifier', 'reporter'], error: null },
      })
      seedHistory()
      applySite(site())
      cycle()
      loop = setInterval(cycle, 45_000)
      tick = setInterval(() => !crashed && Object.values(cams).forEach((c) => ((c.compliance_score = score(c)), (c.last_heartbeat = iso()))), 5000)
    },
    stop() {
      if (loop) clearInterval(loop)
      if (tick) clearInterval(tick)
      timers.forEach(clearTimeout)
      timers.clear()
    },
    async approve(actionIdArg) {
      const go = approvals.get(actionIdArg)
      if (!go) return // already approved: the actor ignores a second approve
      approvals.delete(actionIdArg)
      await new Promise((r) => setTimeout(r, 300))
      go('floor manager')
    },
    async ack(incidentId) {
      const [c, i] = find(incidentId)
      if (i.status === 'closed') return
      i.status = 'closed'
      i.closed_at = iso()
      changed(c)
    },
    crash() {
      if (crashed) return
      crashed = true
      markDown('reconnecting', 'actor service stopped (simulated)')
      setTimeout(() => {
        crashed = false
        for (const c of Object.values(cams)) {
          c.recovered_from_version = c.version
          c.status = 'recovering'
          changed(c)
        }
        setTimeout(() => Object.values(cams).forEach((c) => ((c.status = 'online'), changed(c))), 3000)
      }, 4000)
    },
  }
}
