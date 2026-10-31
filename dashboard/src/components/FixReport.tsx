import { CheckCircle2, Info, RotateCw, Loader2 } from 'lucide-react'
import type { IncidentView } from '../contracts/incident'
import type { Action as RelayActionItem } from '../contracts/event'
import { mediaUrl } from '../data/config'
import { fixReport, describeAction, VERIFIED_DEFINITION } from '../lib/fix'
import { ACTION_TIER, SIMULATED, clock, duration, zoneLabel, TYPE_LABEL } from '../lib/format'
import { Button, Card, CardHeader } from './ui'
import { SimTag } from './chips'

function Step({ label, at, active }: { label: string; at: string | null; active: boolean }) {
  return (
    <div className="flex flex-1 flex-col items-center gap-1.5 text-center">
      <span className={`size-2.5 rounded-full ${active ? 'bg-accent' : 'bg-raised'}`} />
      <span className={`text-xs ${active ? 'text-fg' : 'text-dim'}`}>{label}</span>
      <span className="tnum font-mono text-[10px] text-dim">{at ? clock(at) : '–'}</span>
    </div>
  )
}

export function FixReportCard({
  inc,
  onApprove,
  pending,
}: {
  inc: IncidentView
  onApprove?: (a: RelayActionItem) => void
  pending?: Set<string>
}) {
  const r = fixReport(inc)
  if (r.outcome === 'not_needed') return null
  const head =
    r.outcome === 'verified'
      ? { icon: CheckCircle2, color: 'var(--ready)', text: `Fixed and verified${r.time_to_fix_ms ? ` in ${duration(r.time_to_fix_ms)}` : ''}` }
      : r.outcome === 'recurring'
        ? { icon: RotateCw, color: 'var(--critical)', text: 'Fix did not hold: the violation was seen again' }
        : { icon: Loader2, color: 'var(--accent)', text: r.pending.some((a) => a.status === 'awaiting_approval') ? 'Waiting for a human approval' : 'Fix in progress' }

  return (
    <Card className="overflow-hidden">
      <CardHeader title="Fix report" right={<span title={VERIFIED_DEFINITION} className="inline-flex cursor-help items-center gap-1"><Info className="size-3.5" /> verified?</span>} />
      <div className="flex items-center gap-3 px-4 pt-4">
        <head.icon className={`size-5 ${r.outcome === 'in_progress' ? 'animate-spin' : ''}`} style={{ color: head.color }} />
        <span className="font-semibold">{head.text}</span>
      </div>

      <div className="relative mx-4 mt-4 flex">
        <span className="absolute left-[12%] right-[12%] top-[4px] h-px bg-line" />
        <Step label="Detected" at={r.detected_at} active />
        <Step label="Confirmed" at={r.confirmed_at} active={!!r.confirmed_at} />
        <Step label="Fix applied" at={r.actions_done_at} active={!!r.actions_done_at} />
        <Step label={r.outcome === 'recurring' ? 'Recurred' : 'Verified'} at={r.verified_at} active={!!r.verified_at} />
      </div>

      <div className="mt-4 border-t border-line px-4 py-3">
        <div className="label mb-2">What was done</div>
        {r.done.length + r.pending.length + r.failed.length === 0 ? (
          <p className="text-sm text-dim">The fixer has not chosen actions yet.</p>
        ) : (
          <ul className="space-y-2">
            {[...r.done, ...r.pending, ...r.failed].map((a) => (
              <li key={a.action_id} className="flex gap-2.5 text-sm" title={`action ${a.action_id}`}>
                <span
                  className="mt-1.5 size-1.5 shrink-0 rounded-full"
                  style={{ background: a.status === 'done' ? 'var(--ready)' : a.status === 'failed' || a.status === 'rejected' ? 'var(--critical)' : 'var(--accent)' }}
                />
                <span className={a.status === 'done' ? '' : 'text-muted'}>
                  {describeAction(a)}
                  {a.status !== 'done' && <span className="text-dim"> ({a.status.replace('_', ' ')})</span>}
                  {SIMULATED.includes(a.kind) && (
                    <span className="ml-1.5">
                      <SimTag />
                    </span>
                  )}
                  {ACTION_TIER[a.kind] === 2 && <span className="ml-1.5 font-mono text-[10px] text-dim">human gate</span>}
                  {a.approved_by && <span className="text-dim"> Approved by {a.approved_by}.</span>}
                  {a.status === 'awaiting_approval' && onApprove && (
                    <Button variant="primary" className="mt-2 flex h-7" disabled={pending?.has(a.action_id)} onClick={() => onApprove(a)}>
                      {pending?.has(a.action_id) ? 'Approving…' : 'Approve'}
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {(r.outcome === 'verified' || r.outcome === 'recurring') && (
        <div className="border-t border-line px-4 py-3">
          <div className="label mb-2">How it was verified</div>
          {inc.after_frame_url && (
            <div className="mb-3 grid grid-cols-2 gap-2">
              {[
                ['Before', inc.event.frame_url],
                [r.outcome === 'recurring' ? 'Seen again' : 'After', inc.after_frame_url],
              ].map(([label, url]) => (
                <figure key={label} className="relative overflow-hidden rounded-md border border-line">
                  <img src={mediaUrl(url) ?? ''} alt={`${label} frame`} className="aspect-video w-full object-cover" />
                  <figcaption className="absolute left-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-white">{label}</figcaption>
                </figure>
              ))}
            </div>
          )}
          <p className="text-sm">
            After the last action, the {zoneLabel(inc.event.zone)} camera kept watching for repeats of {TYPE_LABEL[inc.event.type].toLowerCase()}
            {r.window_ms ? ` for ${duration(r.window_ms)}` : ''}. {inc.verify_summary}
          </p>
          <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[11px]">
            <div className="rounded-md bg-subtle px-2.5 py-1.5">
              <div className="text-dim">TIME TO FIX</div>
              <div className="tnum">{duration(r.time_to_fix_ms)}</div>
            </div>
            <div className="rounded-md bg-subtle px-2.5 py-1.5">
              <div className="text-dim">ACTIONS</div>
              <div className="tnum">
                {r.done.length}/{inc.actions.length}
              </div>
            </div>
            <div className="rounded-md bg-subtle px-2.5 py-1.5">
              <div className="text-dim">HUMAN STEPS</div>
              <div className="tnum">{r.human_steps}</div>
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}
