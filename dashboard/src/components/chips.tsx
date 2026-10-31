import { CheckCircle2 } from 'lucide-react'
import type { IncidentStatus } from '../contracts/incident'
import { STATUS_LABEL } from '../lib/format'
import { Dot, Pill } from './ui'
import { cn } from '../lib/cn'

export const STATUS_COLOR: Record<IncidentStatus, string> = {
  verifying: 'var(--text-muted)',
  confirmed: 'var(--watch)',
  dismissed: 'var(--text-dim)',
  unverified: 'var(--watch)',
  awaiting_approval: 'var(--accent)',
  actioned: 'var(--accent)',
  resolved: 'var(--ready)',
  recurring: 'var(--critical)',
  closed: 'var(--text-dim)',
}

export function StatusChip({ status, lowConfidence, className }: { status: IncidentStatus; lowConfidence?: boolean; className?: string }) {
  if (status === 'resolved' && lowConfidence)
    return (
      <Pill className={cn('text-ok', className)} title="No repeat during the window, but no compliant observation either">
        <Dot color="var(--ready)" />
        Resolved, low confidence
      </Pill>
    )
  if (status === 'resolved') return <VerifiedBadge className={className} />
  return (
    <Pill
      className={cn(
        status === 'dismissed' || status === 'closed' ? 'text-dim' : 'text-muted',
        status === 'awaiting_approval' && 'bg-accent-bg text-accent',
        status === 'recurring' && 'bg-bad-bg text-bad',
        className,
      )}
    >
      <Dot color={STATUS_COLOR[status]} pulse={status === 'verifying' || status === 'awaiting_approval'} />
      {STATUS_LABEL[status]}
    </Pill>
  )
}

export function VerifiedBadge({ className }: { className?: string }) {
  return (
    <Pill className={cn('bg-ok-bg text-ok', className)}>
      <CheckCircle2 className="size-3.5" />
      Fixed and verified
    </Pill>
  )
}

export const SEV_COLOR = ['', 'var(--color-sev-1)', 'var(--color-sev-2)', 'var(--color-sev-3)', 'var(--color-sev-4)', 'var(--color-sev-5)']

export function SeverityBadge({ severity, from, className }: { severity: number; from?: 'triage' | 'detector'; className?: string }) {
  const s = Math.max(1, Math.min(5, Math.round(severity)))
  return (
    <Pill className={cn('font-mono', className)} title={from === 'detector' ? 'From the detector, before AI triage' : 'Set by AI triage'}>
      <span className="flex gap-0.5">
        {[1, 2, 3, 4, 5].map((i) => (
          <span key={i} className="h-2.5 w-1 rounded-sm" style={{ background: i <= s ? SEV_COLOR[s] : 'var(--surface-3)' }} />
        ))}
      </span>
      S{s}
    </Pill>
  )
}

export function SimTag() {
  return <span className="font-mono text-[10px] uppercase tracking-wider text-dim">simulated</span>
}
