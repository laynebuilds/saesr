// Everything the analytical panels show, computed from the derived incidents. No stored numbers.
import { useMemo } from 'react'
import type { IncidentView, StepName } from '../contracts/incident'
import type { EventType } from '../contracts/event'
import { useIncidents } from './useIncidents'

export interface AgentStat {
  agent: StepName
  model: string | null
  runs: number
  failed: number
  avg_ms: number | null
  p95_ms: number | null
  last_ms: number | null
  tokens: number
}

export interface Analytics {
  incidents: IncidentView[]
  total: number
  funnel: { detected: number; triaged: number; confirmed: number; actioned: number; verified: number; dismissed: number }
  mttv_ms: number | null // event -> triage done
  mttr_ms: number | null // event -> verified resolved
  autonomy: number | null // resolved without a human approval / resolved
  byType: { type: EventType; count: number }[]
  bySeverity: number[] // index 1..5
  byZone: { zone: string; count: number; active: number }[]
  zoneHeat: { zones: string[]; buckets: number[]; cells: number[][] } // counts per zone per bucket
  perBucket: number[] // all incidents per bucket
  bucketMs: number
  agents: AgentStat[]
}

const BUCKETS = 24

function mean(xs: number[]) {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null
}
function p95(xs: number[]) {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.min(s.length - 1, Math.floor(s.length * 0.95))]
}

export function computeAnalytics(incidents: IncidentView[], now = Date.now()): Analytics {
  const triaged = incidents.filter((i) => i.trace[1].state === 'done' || i.trace[1].state === 'failed')
  const dismissed = incidents.filter((i) => i.status === 'dismissed')
  const confirmed = triaged.filter((i) => i.status !== 'dismissed')
  const actioned = incidents.filter((i) => i.actions.length > 0)
  const verified = incidents.filter((i) => i.status === 'resolved')

  const t0 = (i: IncidentView) => Date.parse(i.event.timestamp)
  const mttv = mean(triaged.map((i) => (i.trace[1].ended_at ? Date.parse(i.trace[1].ended_at) - t0(i) : NaN)).filter((x) => x >= 0))
  const mttr = mean(verified.map((i) => (i.trace[4].ended_at ? Date.parse(i.trace[4].ended_at) - t0(i) : NaN)).filter((x) => x >= 0))
  const humanless = verified.filter((i) => !i.actions.some((a) => a.approved_by)).length

  const typeCount = new Map<EventType, number>()
  const sev = [0, 0, 0, 0, 0, 0]
  const zoneCount = new Map<string, { count: number; active: number }>()
  for (const i of incidents) {
    if (i.status === 'dismissed') continue
    typeCount.set(i.event.type, (typeCount.get(i.event.type) ?? 0) + 1)
    sev[i.severity] += 1
    const z = zoneCount.get(i.event.zone) ?? { count: 0, active: 0 }
    z.count += 1
    if (!['resolved', 'closed'].includes(i.status)) z.active += 1
    zoneCount.set(i.event.zone, z)
  }

  // Time buckets cover the span of data (at least 30 min, at most 12 h).
  const oldest = incidents.length ? Math.min(...incidents.map(t0)) : now - 3600_000
  const span = Math.min(12 * 3600_000, Math.max(30 * 60_000, now - oldest))
  const bucketMs = span / BUCKETS
  const start = now - span
  const zones = [...zoneCount.keys()].sort()
  const cells = zones.map(() => new Array(BUCKETS).fill(0))
  const perBucket = new Array(BUCKETS).fill(0)
  for (const i of incidents) {
    if (i.status === 'dismissed') continue
    const b = Math.floor((t0(i) - start) / bucketMs)
    if (b < 0 || b >= BUCKETS) continue
    perBucket[b] += 1
    cells[zones.indexOf(i.event.zone)][b] += 1
  }

  const steps: StepName[] = ['triage', 'coach', 'fixer', 'verifier', 'reporter']
  const agents: AgentStat[] = steps.map((agent) => {
    const runs = incidents.map((i) => i.trace.find((t) => t.agent === agent)!).filter((t) => t.state === 'done' || t.state === 'failed')
    const ms = runs.map((t) => t.duration_ms).filter((x): x is number => x !== null)
    return {
      agent,
      model: runs.find((r) => r.model)?.model ?? null,
      runs: runs.length,
      failed: runs.filter((r) => r.state === 'failed').length,
      avg_ms: mean(ms),
      p95_ms: p95(ms),
      last_ms: ms.length ? ms[0] : null,
      tokens: runs.reduce((a, r) => a + (r.tokens ?? 0), 0),
    }
  })

  return {
    incidents,
    total: incidents.length,
    funnel: {
      detected: incidents.length,
      triaged: triaged.length,
      confirmed: confirmed.length,
      actioned: actioned.length,
      verified: verified.length,
      dismissed: dismissed.length,
    },
    mttv_ms: mttv,
    mttr_ms: mttr,
    autonomy: verified.length ? humanless / verified.length : null,
    byType: [...typeCount].map(([type, count]) => ({ type, count })).sort((a, b) => b.count - a.count),
    bySeverity: sev,
    byZone: [...zoneCount].map(([zone, v]) => ({ zone, ...v })).sort((a, b) => b.count - a.count),
    zoneHeat: { zones, buckets: perBucket.map((_, i) => start + i * bucketMs), cells },
    perBucket,
    bucketMs,
    agents,
  }
}

export function useAnalytics(): Analytics {
  const incidents = useIncidents()
  // Recompute at most when incidents change; the clock drift inside a minute does not matter here.
  return useMemo(() => computeAnalytics(incidents), [incidents])
}
