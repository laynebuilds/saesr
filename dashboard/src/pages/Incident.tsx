import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useIncident } from '../hooks/useIncidents'
import { source } from '../data'
import { mediaUrl } from '../data/config'
import type { Action as RelayActionItem } from '../contracts/event'
import { Button, Card, CardHeader, Empty } from '../components/ui'
import { AgentLog, IncidentHeader, TraceStepper } from '../components/incident-bits'
import { Pill } from '../components/ui'
import { FixReportCard } from '../components/FixReport'
import { ago, camShort, clock, zoneLabel } from '../lib/format'

const USER = 'floor manager'

export function Incident() {
  const { id } = useParams()
  const inc = useIncident(id)
  const [pending, setPending] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [acking, setAcking] = useState(false)

  if (!inc) {
    return (
      <Card>
        <Empty>
          Incident not found yet. It may still be arriving.{' '}
          <Link to="/incidents" className="text-accent hover:underline">
            All incidents
          </Link>
        </Empty>
      </Card>
    )
  }

  const approve = async (a: RelayActionItem) => {
    if (pending.has(a.action_id)) return
    setPending((p) => new Set(p).add(a.action_id))
    setError(null)
    try {
      await source.approve(a.action_id, inc.id, USER)
    } catch (e) {
      setError((e as Error).message)
      setPending((p) => {
        const n = new Set(p)
        n.delete(a.action_id)
        return n
      })
    }
  }

  const ack = async () => {
    setAcking(true)
    try {
      await source.ack(inc.id, USER)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setAcking(false)
    }
  }

  const e = inc.event
  const frame = mediaUrl(e.frame_url)
  const clip = mediaUrl(e.clip_url)

  return (
    <div className="space-y-5">
      <Link to="/incidents" className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" /> Incidents
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1.5">
          <IncidentHeader inc={inc} />
          <p className="text-sm text-muted">
            {camShort(e.camera)} · {zoneLabel(e.zone)} · {clock(e.timestamp)} ({ago(e.timestamp)})
            {e.confidence != null && <> · detector confidence {e.confidence.toFixed(2)}</>}
          </p>
        </div>
        {!['closed', 'dismissed'].includes(inc.status) && (
          <Button onClick={ack} disabled={acking} title="Mark that a person has seen this. It does not run any action.">
            {acking ? 'Acknowledging…' : 'Acknowledge'}
          </Button>
        )}
      </div>

      {inc.status === 'dismissed' && inc.triage_reason && (
        <Card className="p-4 text-sm">
          <span className="font-medium">Dismissed by triage.</span> <span className="text-muted">{inc.triage_reason}</span>
        </Card>
      )}
      {error && <Card className="border-bad/40 p-3 text-sm text-bad">{error}</Card>}

      <div className="grid gap-5 lg:grid-cols-5">
        <div className="space-y-5 lg:col-span-3">
          <Card className="overflow-hidden">
            <CardHeader title="Evidence" />
            <div className="grid gap-px bg-line sm:grid-cols-2">
              <div className="bg-card">
                {frame ? <img src={frame} alt="Detection frame" className="aspect-video w-full object-cover" /> : <Empty>No frame</Empty>}
              </div>
              <div className="bg-card">
                {clip ? (
                  <video src={clip} controls muted loop playsInline className="aspect-video w-full bg-black object-cover" />
                ) : (
                  <Empty>No clip</Empty>
                )}
              </div>
            </div>
            {e.detail && <p className="border-t border-line px-4 py-3 text-sm">{e.detail}</p>}
          </Card>

          <Card>
            <CardHeader title="Agent relay trace" />
            <div className="p-4">
              <TraceStepper trace={inc.trace} />
            </div>
          </Card>
        </div>

        <div className="space-y-5 lg:col-span-2">
          <FixReportCard inc={inc} onApprove={approve} pending={pending} />

          {inc.coach && (
            <Card>
              <CardHeader title="Coaching" right={inc.coach.standard_tag ? <Pill>{inc.coach.standard_tag}</Pill> : undefined} />
              <div className="space-y-2 p-4 text-sm">
                <p>{inc.coach.summary}</p>
                {inc.coach.recommended_fix && (
                  <p>
                    <span className="font-medium">Fix: </span>
                    {inc.coach.recommended_fix}
                  </p>
                )}
              </div>
            </Card>
          )}

          <Card className="overflow-hidden">
            <CardHeader title="Agent Relay messages" />
            <AgentLog items={inc.log} />
          </Card>
        </div>
      </div>
    </div>
  )
}
