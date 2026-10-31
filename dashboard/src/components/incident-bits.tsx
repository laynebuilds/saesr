import { modelLabel } from '../data/config'
import { Link } from 'react-router-dom'
import { Check, Circle, Loader2, Minus, X } from 'lucide-react'
import type { IncidentView, TraceStep } from '../contracts/incident'
import type { ActivityItem } from '../contracts/agents'
import type { Action as RelayActionItem } from '../contracts/event'
import { ACTION_LABEL, ACTION_TIER, SIMULATED, TYPE_LABEL, ago, camShort, clock, duration, zoneLabel } from '../lib/format'
import { mediaUrl } from '../data/config'
import { cn } from '../lib/cn'
import { SeverityBadge, SimTag, StatusChip, SEV_COLOR } from './chips'
import { Button, Empty, Pill } from './ui'

export function IncidentRow({ inc, compact }: { inc: IncidentView; compact?: boolean }) {
  return (
    <Link
      to={`/incidents/${inc.id}`}
      className={cn('slide-in flex items-center gap-3 px-4 py-3 transition-colors hover:bg-subtle', inc.status === 'dismissed' && 'opacity-60')}
    >
      <span className="h-8 w-1 shrink-0 rounded-full" style={{ background: SEV_COLOR[inc.severity] }} />
      {!compact && inc.event.frame_url && (
        <img src={mediaUrl(inc.event.frame_url) ?? ''} alt="" className="h-10 w-14 shrink-0 rounded-md border border-line object-cover" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{TYPE_LABEL[inc.event.type]}</span>
          <span className="truncate text-xs text-muted">{zoneLabel(inc.event.zone)}</span>
        </div>
        <div className="truncate text-xs text-muted">
          {camShort(inc.event.camera)} · {ago(inc.event.timestamp)}
        </div>
      </div>
      <StatusChip status={inc.status} lowConfidence={inc.low_confidence} />
    </Link>
  )
}

const STEP_LABEL: Record<TraceStep['agent'], string> = {
  detector: 'Detector',
  triage: 'Triage',
  coach: 'Coach',
  fixer: 'Fixer',
  verifier: 'Verifier',
  reporter: 'Reporter',
}
const STEP_ROLE: Record<TraceStep['agent'], string> = {
  detector: 'Computer vision flags the frame',
  triage: 'Vision model checks the frames',
  coach: 'Writes the coaching and fix',
  fixer: 'Policy table picks actions, model fills them',
  verifier: 'Checks the problem stopped',
  reporter: 'Posts the outcome',
}

function StepIcon({ state }: { state: TraceStep['state'] }) {
  const base = 'grid size-6 place-items-center rounded-full border'
  if (state === 'done') return <span className={cn(base, 'border-fg bg-fg text-bg')}><Check className="size-3.5" /></span>
  if (state === 'failed') return <span className={cn(base, 'border-bad bg-bad text-white')}><X className="size-3.5" /></span>
  if (state === 'running') return <span className={cn(base, 'border-accent text-accent')}><Loader2 className="size-3.5 animate-spin" /></span>
  if (state === 'skipped') return <span className={cn(base, 'border-line text-muted')}><Minus className="size-3.5" /></span>
  return <span className={cn(base, 'border-line text-line')}><Circle className="size-2" /></span>
}

export function TraceStepper({ trace }: { trace: TraceStep[] }) {
  return (
    <ol className="relative">
      {trace.map((s, i) => (
        <li key={s.agent} className="relative flex gap-4 pb-5 last:pb-0">
          {i < trace.length - 1 && <span className="absolute left-3 top-7 bottom-0 w-px bg-line" />}
          <StepIcon state={s.state} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className={cn('text-sm font-semibold', (s.state === 'pending' || s.state === 'skipped') && 'text-muted')}>{STEP_LABEL[s.agent]}</span>
              <span className="text-xs text-muted">{STEP_ROLE[s.agent]}</span>
              <span className="ml-auto flex items-center gap-2 font-mono text-[11px] text-muted">
                {s.model && <span>{modelLabel(s.model)}</span>}
                {s.duration_ms !== null && s.agent !== 'detector' && <span className="text-fg">{duration(s.duration_ms)}</span>}
                {s.tokens && <span>{s.tokens} tok</span>}
              </span>
            </div>
            {s.state === 'running' && <p className="mt-1 text-sm text-muted">Working…</p>}
            {s.state === 'skipped' && <p className="mt-1 text-sm text-muted">Skipped, triage dismissed this alert.</p>}
            {s.output_summary && <p className="mt-1 text-sm leading-relaxed">{s.output_summary}</p>}
            {s.error && <p className="mt-1 text-sm text-bad">Failed: {s.error}</p>}
          </div>
        </li>
      ))}
    </ol>
  )
}

export function ActionsPanel({
  actions,
  onApprove,
  pending,
}: {
  actions: RelayActionItem[]
  onApprove: (a: RelayActionItem) => void
  pending: Set<string>
}) {
  if (!actions.length) return <Empty>No actions yet. The fixer runs after triage confirms.</Empty>
  return (
    <ul className="divide-y divide-line">
      {actions.map((a) => {
        const tier = ACTION_TIER[a.kind]
        const waiting = a.status === 'awaiting_approval'
        return (
          <li key={a.action_id} className="flex flex-col gap-2 px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{ACTION_LABEL[a.kind]}</span>
              {SIMULATED.includes(a.kind) && <SimTag />}
              <span className="ml-auto flex items-center gap-2">
                <span className="font-mono text-[11px] text-muted">tier {tier}</span>
                <ActionStatus status={a.status} />
              </span>
            </div>
            <ParamList params={a.params} />
            <div className="flex items-center gap-3 text-xs text-muted">
              <span className="font-mono">{a.action_id}</span>
              {a.approved_by && <span>approved by {a.approved_by}</span>}
              {a.executed_at && <span>executed {clock(a.executed_at)}</span>}
              {waiting && (
                <Button variant="primary" className="ml-auto" disabled={pending.has(a.action_id)} onClick={() => onApprove(a)}>
                  {pending.has(a.action_id) ? 'Approving…' : 'Approve'}
                </Button>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

function ActionStatus({ status }: { status: RelayActionItem['status'] }) {
  const color = { pending: '#9ca3af', approved: 'var(--accent)', done: '#22c55e', failed: '#ef4444', awaiting_approval: 'var(--accent)', rejected: '#6b7280' }[status]
  const label = { pending: 'Pending', approved: 'Approved', done: 'Done', failed: 'Failed', awaiting_approval: 'Needs approval', rejected: 'Rejected' }[status]
  return (
    <Pill>
      <span className="inline-block size-1.5 rounded-full" style={{ background: color }} />
      {label}
    </Pill>
  )
}

function ParamList({ params }: { params: Record<string, unknown> }) {
  const entries = Object.entries(params).filter(([k]) => k !== 'evidence')
  if (!entries.length) return null
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k.replace(/_/g, ' ')}</dt>
          <dd className="truncate">{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

export function AgentLog({ items }: { items: ActivityItem[] }) {
  if (!items.length) return <Empty>No agent messages yet.</Empty>
  return (
    <ul className="max-h-96 divide-y divide-line overflow-y-auto font-mono text-xs">
      {items.map((it) => (
        <li key={it.id} className="px-4 py-2">
          <div className="flex items-center gap-2 text-muted">
            <span className="font-medium text-fg">{it.agent}</span>
            <span>→ {it.to}</span>
            <span className="ml-auto">{clock(it.ts)}</span>
          </div>
          <div className="mt-0.5 whitespace-pre-wrap break-words">{it.text.split('\n').slice(0, 2).join('\n')}</div>
        </li>
      ))}
    </ul>
  )
}

export function KpiCard({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-card p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  )
}

export function IncidentHeader({ inc }: { inc: IncidentView }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <h1 className="mr-2 text-xl font-semibold tracking-tight">{TYPE_LABEL[inc.event.type]}</h1>
      <SeverityBadge severity={inc.severity} from={inc.severity_from} />
      <StatusChip status={inc.status} lowConfidence={inc.low_confidence} />
    </div>
  )
}
