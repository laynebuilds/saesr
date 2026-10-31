// Anything waiting on a human sits at the top of the Overview with its Approve button,
// so the gated action is one click away instead of two pages deep.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import type { IncidentView } from '../contracts/incident'
import type { Action as RelayActionItem } from '../contracts/event'
import { source } from '../data'
import { describeAction } from '../lib/fix'
import { TYPE_LABEL, ago, camShort, zoneLabel } from '../lib/format'
import { Button } from './ui'
import { SimTag, SEV_COLOR } from './chips'

export function AttentionBar({ incidents }: { incidents: IncidentView[] }) {
  const [pending, setPending] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const items = incidents.flatMap((inc) => inc.actions.filter((a) => a.status === 'awaiting_approval').map((a) => ({ inc, a })))
  if (!items.length) return null

  const approve = async (inc: IncidentView, a: RelayActionItem) => {
    if (pending.has(a.action_id)) return
    setPending((p) => new Set(p).add(a.action_id))
    setError(null)
    try {
      await source.approve(a.action_id, inc.id, 'floor manager')
    } catch (e) {
      setError((e as Error).message)
      setPending((p) => {
        const n = new Set(p)
        n.delete(a.action_id)
        return n
      })
    }
  }

  return (
    <div className="rise space-y-2">
      {items.slice(0, 3).map(({ inc, a }) => (
        <div key={a.action_id} className="glass flex flex-wrap items-center gap-3 rounded-xl border-accent/50 px-4 py-3">
          <ShieldAlert className="size-5 shrink-0 text-accent" />
          <span className="h-8 w-1 shrink-0 rounded-full" style={{ background: SEV_COLOR[inc.severity] }} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-semibold">Needs your approval</span>
              <span className="text-sm text-muted">
                {TYPE_LABEL[inc.event.type]} · {camShort(inc.event.camera)} · {zoneLabel(inc.event.zone)} · {ago(inc.event.timestamp)}
              </span>
            </div>
            <div className="mt-0.5 text-sm">
              {describeAction(a)} <SimTag />
            </div>
          </div>
          <Link to={`/incidents/${inc.id}`} className="text-sm text-muted hover:text-fg">
            Details
          </Link>
          <Button variant="primary" disabled={pending.has(a.action_id)} onClick={() => approve(inc, a)}>
            {pending.has(a.action_id) ? 'Approving…' : 'Approve'}
          </Button>
        </div>
      ))}
      {error && <div className="text-sm text-bad">{error}</div>}
    </div>
  )
}
