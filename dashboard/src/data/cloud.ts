// Cloud source: the real incident log in Supabase, for the public demo at saesr.com/demo.
//   The floor laptop's actors write every incident, action and agent step to Supabase
//   (write-through); this source rebuilds SiteState from those rows, read-only, and refreshes on
//   every Realtime change, so a live run on the floor shows up here within seconds.
//   Approvals and crash need the floor backend, so they are disabled here.
import type { Action, CameraState, Incident, SiteState, TraceEntry } from '../contracts/event'
import type { ActivityItem } from '../contracts/agents'
import { SITE_ID } from '../contracts/event'
import { supabase } from './supabase'
import { applySite, markDown, setState } from './store'
import type { Source } from './source'

const POLL_MS = 5000
const AGENTS = ['triage', 'coach', 'fixer', 'verifier', 'reporter']
const OPEN = new Set(['verifying', 'confirmed', 'unverified', 'awaiting_approval', 'actioned', 'recurring'])

type Row = Record<string, unknown>
const s = (v: unknown) => (v == null ? null : String(v))

async function rows(table: string, order: string, limit: number): Promise<Row[]> {
  const { data, error } = await supabase!.from(table).select('*').order(order, { ascending: false }).limit(limit)
  if (error) throw new Error(`${table}: ${error.message}`)
  return (data ?? []) as Row[]
}

function toIncident(r: Row, actions: Action[], trace: TraceEntry[]): Incident {
  return {
    incident_id: String(r.id),
    camera_id: String(r.camera_id),
    type: r.type as Incident['type'],
    zone_id: String(r.zone_id ?? ''),
    status: r.status as Incident['status'],
    severity: (r.severity as number | null) ?? null,
    event_ids: [],
    opened_at: String(r.opened_at),
    updated_at: String(r.updated_at ?? r.opened_at),
    relay: (r.relay as Incident['relay']) ?? {},
    actions,
    trace,
    keyframes: (r.keyframes as string[] | null) ?? [],
    clip_url: s(r.clip_url),
    verify_until: null,
    low_confidence: !!r.low_confidence,
    closed_at: s(r.closed_at),
  }
}

async function load(): Promise<{ site: SiteState; activity: ActivityItem[] }> {
  const [incRows, actRows, traceRows] = await Promise.all([
    rows('incidents', 'opened_at', 200),
    rows('actions', 'updated_at', 1000),
    rows('relay_trace', 'started_at', 1000),
  ])
  const actionsBy = new Map<string, Action[]>()
  for (const a of actRows) {
    const list = actionsBy.get(String(a.incident_id)) ?? []
    list.push({
      action_id: String(a.action_id),
      incident_id: String(a.incident_id),
      kind: a.kind as Action['kind'],
      status: a.status as Action['status'],
      params: (a.params as Record<string, unknown>) ?? {},
      approved_by: s(a.approved_by),
      executed_at: s(a.executed_at),
    })
    actionsBy.set(String(a.incident_id), list)
  }
  const traceBy = new Map<string, TraceEntry[]>()
  for (const t of [...traceRows].reverse()) {
    const list = traceBy.get(String(t.incident_id)) ?? []
    list.push({
      agent: String(t.agent),
      model: s(t.model),
      started_at: String(t.started_at),
      ended_at: s(t.ended_at),
      output_summary: s(t.output_summary),
      error: s(t.error),
    })
    traceBy.set(String(t.incident_id), list)
  }

  const cams = new Map<string, Incident[]>()
  for (const r of incRows) {
    const inc = toIncident(r, actionsBy.get(String(r.id)) ?? [], traceBy.get(String(r.id)) ?? [])
    cams.set(inc.camera_id, [...(cams.get(inc.camera_id) ?? []), inc])
  }
  if (!cams.size) cams.set('cam-01', [])

  const cameras: CameraState[] = [...cams.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([camera_id, incidents]) => {
      const counters: Record<string, number> = {}
      for (const i of incidents) counters[i.type] = (counters[i.type] ?? 0) + 1
      const open = incidents.filter((i) => OPEN.has(i.status))
      const score = Math.max(0, 100 - open.reduce((n, i) => n + (i.severity ?? 3) * 4, 0))
      return {
        camera_id,
        site_id: SITE_ID,
        status: 'online',
        last_heartbeat: incidents[0]?.updated_at ?? null,
        version: incidents.length,
        recovered_from_version: null,
        compliance_score: score,
        counters,
        false_positives_filtered: incidents.filter((i) => i.status === 'dismissed').length,
        autonomous_fixes: incidents.flatMap((i) => i.actions).filter((a) => a.status === 'done' && a.kind !== 'slow_machine').length,
        incidents,
      }
    })

  const site: SiteState = {
    site_id: SITE_ID,
    site_score: Math.round(cameras.reduce((n, c) => n + c.compliance_score, 0) / cameras.length),
    active_incidents: cameras.reduce((n, c) => n + c.incidents.filter((i) => OPEN.has(i.status)).length, 0),
    autonomous_fixes: cameras.reduce((n, c) => n + c.autonomous_fixes, 0),
    cameras,
  }

  const label = new Map(incRows.map((r) => [String(r.id), `${String(r.type).replace(/_/g, ' ')} in ${String(r.zone_id ?? '').replace(/^zone-/, '').replace(/-/g, ' ')}`]))
  const activity: ActivityItem[] = traceRows
    .filter((t) => AGENTS.includes(String(t.agent)) || String(t.agent).startsWith('verif') || String(t.agent).startsWith('approv'))
    .map((t) => ({
      id: `trace-${t.id}`,
      ts: String(t.ended_at ?? t.started_at),
      kind: 'message' as const,
      agent: String(t.agent),
      to: '#safety-floor',
      text: `${label.get(String(t.incident_id)) ?? 'incident'}: ${s(t.output_summary) ?? 'done'}${t.error ? ` (error: ${t.error})` : ''}`,
      event_id: null,
      relay_id: null,
      action: null,
    }))
    .reverse()
  return { site, activity }
}

export function createCloudSource(): Source {
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let pending: ReturnType<typeof setTimeout> | undefined
  let unsubscribe = () => {}

  const refresh = async () => {
    try {
      const { site, activity } = await load()
      if (stopped) return
      applySite(site)
      setState({
        activity,
        agentStatus: { enabled: true, connected: true, relay_url: 'Supabase incident log', channel: 'safety-floor', agents: AGENTS, error: null },
      })
    } catch (err) {
      markDown('degraded', err instanceof Error ? err.message : String(err))
    }
  }
  const soon = () => {
    clearTimeout(pending)
    pending = setTimeout(refresh, 400)
  }
  const loop = async () => {
    await refresh()
    if (!stopped) timer = setTimeout(loop, POLL_MS)
  }

  return {
    start() {
      if (!supabase) {
        markDown('degraded', 'Supabase is not configured (VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY)')
        return
      }
      loop()
      const ch = supabase.channel('saesr-cloud')
      for (const table of ['incidents', 'actions', 'relay_trace']) ch.on('postgres_changes', { event: '*', schema: 'public', table }, soon)
      ch.subscribe()
      unsubscribe = () => supabase?.removeChannel(ch)
    },
    stop() {
      stopped = true
      clearTimeout(timer)
      clearTimeout(pending)
      unsubscribe()
    },
    async approve() {
      throw new Error('Read-only public view: approvals happen on the floor console.')
    },
    async ack() {
      throw new Error('Read-only public view: incidents are closed on the floor console.')
    },
  }
}
