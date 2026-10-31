// Plain-English description of what each action did, and what "fixed and verified" rests on.
import type { Action as RelayActionItem } from '../contracts/event'
import type { IncidentView } from '../contracts/incident'

const s = (v: unknown) => (v == null ? '' : String(v).replace(/[.\s]+$/, ''))

export function describeAction(a: RelayActionItem): string {
  const p = a.params
  switch (a.kind) {
    case 'assign_coaching':
      return `Coaching assigned${p.audience ? ` to ${s(p.audience)}` : p.worker ? ` to ${s(p.worker)}` : p.zone_id ? ` for ${s(p.zone_id).replace(/^zone-/, '')}` : ''}${p.topic ? `: "${s(p.topic)}"` : p.module ? `: ${s(p.module).replace(/[-_]/g, ' ')}` : ''}${p.due ? `, due ${s(p.due)}` : ''}.`
    case 'notify_floor_display':
      return `Floor display${p.display ? ` at ${s(p.display)}` : ''} showed${p.message ? ` "${s(p.message)}"` : ' a warning'}.`
    case 'open_corrective_record': {
      const ev = Array.isArray(p.evidence) ? p.evidence.length : 0
      const std = p.standard ?? p.standard_tag
      return `Corrective record opened${std ? ` under ${s(std)}` : ''}${p.description && !ev ? `: ${s(p.description)}` : ''}${ev ? ` with ${ev} evidence file${ev === 1 ? '' : 's'}` : ''}.`
    }
    case 'flag_batch_hold':
      return `Batch hold flagged for product from ${(s(p.zone) || s(p.zone_id).replace(/^zone-/, '')) || 'this line'}${p.window ? ` (${s(p.window)})` : ' during the lapse window'}.`
    default: {
      const speed = p.target_speed ?? (p.pct != null ? `${s(p.pct)}%` : p.speed_pct != null ? `${s(p.speed_pct)}%` : null)
      const machine = s(p.machine) || s(p.machine_id).replace(/-/g, ' ')
      if (a.status === 'awaiting_approval' || a.status === 'pending')
        return `Slow ${machine || 'the machine'}${speed ? ` to ${s(speed)}` : ''}${p.duration_s ? ` for ${s(p.duration_s)} s` : ''}?`
      return `${machine || 'Machine'} slowed${speed ? ` to ${s(speed)}` : ''}${p.duration_s ? ` for ${s(p.duration_s)} s` : ''}${a.approved_by ? `, approved by ${a.approved_by}` : ''}${a.status === 'approved' ? ' (executing)' : ''}.`
    }
  }
}

export interface FixReport {
  outcome: 'verified' | 'recurring' | 'in_progress' | 'not_needed'
  done: RelayActionItem[]
  pending: RelayActionItem[]
  failed: RelayActionItem[]
  detected_at: string
  confirmed_at: string | null
  actions_done_at: string | null
  verified_at: string | null
  window_ms: number | null // last action -> verifier verdict
  time_to_fix_ms: number | null // detection -> verified
  human_steps: number
}

export function fixReport(inc: IncidentView): FixReport {
  const done = inc.actions.filter((a) => a.status === 'done')
  const pending = inc.actions.filter((a) => a.status === 'pending' || a.status === 'awaiting_approval')
  const failed = inc.actions.filter((a) => a.status === 'failed' || a.status === 'rejected')
  const lastDone = done.map((a) => a.executed_at).filter(Boolean).sort().pop() ?? null
  const verifiedAt = inc.trace[4].ended_at
  const t = (x: string | null) => (x ? Date.parse(x) : NaN)
  const outcome =
    inc.status === 'dismissed' ? 'not_needed' : inc.status === 'resolved' ? 'verified' : inc.status === 'recurring' ? 'recurring' : 'in_progress'
  const win = t(verifiedAt) - t(lastDone)
  const ttf = t(verifiedAt) - t(inc.event.timestamp)
  return {
    outcome,
    done,
    pending,
    failed,
    detected_at: inc.event.timestamp,
    confirmed_at: inc.trace[1].ended_at,
    actions_done_at: lastDone,
    verified_at: verifiedAt,
    window_ms: Number.isFinite(win) && win >= 0 ? win : null,
    time_to_fix_ms: Number.isFinite(ttf) && ttf >= 0 && outcome !== 'in_progress' ? ttf : null,
    human_steps: inc.actions.filter((a) => a.approved_by).length,
  }
}

export const VERIFIED_DEFINITION =
  'Fixed and verified means every action the policy required ran successfully, then the camera kept watching the same zone and did not see the violation again during the verification window.'
