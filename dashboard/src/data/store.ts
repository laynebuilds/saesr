// One small external store for everything live. Sources (mock or live) write the actor's SiteState
// and the Agent Relay activity here; components read through hooks. Incidents are derived.
import { useSyncExternalStore } from 'react'
import type { SiteState } from '../contracts/event'
import type { ActivityItem, AgentStatus } from '../contracts/agents'
import type { IncidentView } from '../contracts/incident'
import { siteVersion } from './adapt'

/** live: actor state streaming. degraded: backend up, actors down. reconnecting: retrying. */
export type Conn = 'connecting' | 'live' | 'degraded' | 'reconnecting'

export interface Recovery {
  from_version: number
  to_version: number
  incidents: number
  down_ms: number
  at: number
}

export interface DashState {
  mode: 'mock' | 'live'
  transport: 'socket' | 'poll' | 'mock'
  conn: Conn
  site: SiteState | null
  activity: ActivityItem[]
  agentStatus: AgentStatus | null
  /** Incidents created in this browser session from uploaded footage. */
  local: IncidentView[]
  recovery: Recovery | null
  lastError: string | null
}

const ACTIVITY_MAX = 2000

let state: DashState = {
  mode: 'mock',
  transport: 'mock',
  conn: 'connecting',
  site: null,
  activity: [],
  agentStatus: null,
  local: [],
  recovery: null,
  lastError: null,
}
const listeners = new Set<() => void>()

export const getState = () => state

export function setState(patch: Partial<DashState> | ((s: DashState) => Partial<DashState>)) {
  const p = typeof patch === 'function' ? patch(state) : patch
  state = { ...state, ...p }
  listeners.forEach((l) => l())
}

export function subscribe(l: () => void) {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function useDash<T>(select: (s: DashState) => T): T {
  return useSyncExternalStore(subscribe, () => select(state))
}

export function addActivity(items: ActivityItem[]) {
  if (!items.length) return
  setState((s) => {
    const seen = new Set(s.activity.map((i) => i.id))
    const fresh = items.filter((i) => !seen.has(i.id) && seen.add(i.id))
    return fresh.length ? { activity: [...s.activity, ...fresh].slice(-ACTIVITY_MAX) } : {}
  })
}

export function addLocalIncident(inc: IncidentView) {
  setState((s) => ({ local: [inc, ...s.local] }))
}

// ---------------------------------------------------------------------------
// Connection health and crash recovery. Prefer the actor's own recovered_from_version; fall back
// to the last site version this dashboard saw before the drop.

let versionAtDrop: number | null = null
let downSince: number | null = null

export function markDown(conn: 'degraded' | 'reconnecting', error?: string) {
  if (downSince === null) {
    downSince = Date.now()
    versionAtDrop = siteVersion(state.site)
  }
  setState({ conn, lastError: error ?? null })
}

export function applySite(site: SiteState) {
  const v = siteVersion(site)
  // Drop a stale full-state message (every camera's version must be >= what we have).
  const prev = state.site
  if (prev && state.conn === 'live' && site.cameras.every((c) => (prev.cameras.find((p) => p.camera_id === c.camera_id)?.version ?? -1) > c.version)) return
  const patch: Partial<DashState> = { site, conn: 'live', lastError: null }
  const actorRecovered = site.cameras.find((c) => c.recovered_from_version != null && c.status === 'recovering')
  if (downSince !== null || actorRecovered) {
    if (downSince !== null || !state.recovery) {
      patch.recovery = {
        from_version: versionAtDrop ?? v - (actorRecovered ? actorRecovered.version - (actorRecovered.recovered_from_version ?? actorRecovered.version) : 0),
        to_version: v,
        incidents: site.cameras.reduce((n, c) => n + c.incidents.length, 0),
        down_ms: downSince ? Date.now() - downSince : 0,
        at: Date.now(),
      }
    }
    downSince = null
    versionAtDrop = null
  }
  setState(patch)
}

export function clearRecovery() {
  setState({ recovery: null })
}
