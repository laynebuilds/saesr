import type { EventType } from '../contracts/event'
import type { IncidentStatus } from '../contracts/incident'
import type { ActionKind } from '../contracts/event'

export const TYPE_LABEL: Record<EventType, string> = {
  unsafe_lift: 'Unsafe lift',
  zone_breach: 'Zone breach',
  missing_hairnet: 'Missing hairnet',
  missing_gloves: 'Missing gloves',
  compliant_observation: 'Compliant',
}

export const STATUS_LABEL: Record<IncidentStatus, string> = {
  verifying: 'Verifying',
  confirmed: 'Confirmed',
  dismissed: 'Dismissed',
  unverified: 'Unverified',
  awaiting_approval: 'Awaiting approval',
  actioned: 'Actioned',
  resolved: 'Fixed and verified',
  recurring: 'Recurring',
  closed: 'Closed',
}

export const ACTION_LABEL: Record<ActionKind, string> = {
  assign_coaching: 'Assign coaching',
  open_corrective_record: 'Open corrective record',
  flag_batch_hold: 'Flag batch hold',
  notify_floor_display: 'Notify floor display',
  slow_machine: 'Slow machine',
}

export const ACTION_TIER: Record<ActionKind, 0 | 1 | 2> = {
  assign_coaching: 0,
  open_corrective_record: 0,
  flag_batch_hold: 0,
  notify_floor_display: 1,
  slow_machine: 2,
}

export const SIMULATED: ActionKind[] = ['notify_floor_display', 'slow_machine']

export const zoneLabel = (z: string) => z.replace(/^zone-/, '').replace(/[-_]/g, ' ')
export const cameraLabel = (c: string) => {
  const m = c.match(/^cam-0*(\d+)/)
  const name: Record<string, string> = { 'cam-01': 'warehouse', 'cam-02': 'food line' }
  return m ? `Cam ${m[1]}${name[c] ? ` · ${name[c]}` : ''}` : c
}

/** "Cam 1" from "cam-01". */
export const camShort = (c: string) => {
  const m = c.match(/^cam-0*(\d+)/)
  return m ? `Cam ${m[1]}` : c
}

export function ago(ts: string | null | undefined, now = Date.now()): string {
  if (!ts) return 'never'
  const s = Math.max(0, Math.round((now - Date.parse(ts)) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export const clock = (ts: string | null | undefined) =>
  ts ? new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '–'

export function duration(ms: number | null): string {
  if (ms === null) return '–'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}
