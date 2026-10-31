import { Link } from 'react-router-dom'
import { useDash } from '../data/store'
import { useAnalytics } from '../hooks/useAnalytics'
import { useCameras } from '../hooks/useCameras'
import { ACTIVE_STATUSES } from '../contracts/incident'
import { Card, CardHeader, Empty, Gauge, Readout, Spark } from '../components/ui'
import { CameraTile } from '../components/CameraTile'
import { IncidentRow } from '../components/incident-bits'
import { WhyItMatters } from '../components/WhyItMatters'
import { AttentionBar } from '../components/AttentionBar'
import { Funnel, ZoneHeat } from '../components/analytics'
import { duration } from '../lib/format'

export function Overview() {
  const site = useDash((s) => s.site)
  const a = useAnalytics()
  const cameras = useCameras(a.incidents)
  const active = a.incidents.filter((i) => ACTIVE_STATUSES.includes(i.status))
  const approvals = a.incidents.filter((i) => i.status === 'awaiting_approval').length
  const score = site?.site_score ?? null
  const scoreTone = score === null ? 'muted' : score >= 85 ? 'ok' : score >= 65 ? 'warn' : 'bad'

  return (
    <div className="space-y-5">
      <AttentionBar incidents={a.incidents} />
      <div className="stagger grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Card className="flex items-center gap-4 px-4 py-3.5">
          <Gauge value={(score ?? 0) / 100} center={score === null ? '–' : score.toFixed(0)} tone={scoreTone} />
          <div>
            <div className="label">Site score</div>
            <div className="mt-1 text-xs text-dim">last 24 h</div>
          </div>
        </Card>
        <Readout
          label="Active incidents"
          value={active.length}
          tone={active.length ? 'warn' : undefined}
          sub={`${approvals} awaiting approval`}
          spark={<Spark values={a.perBucket} tone="warn" />}
        />
        <Readout label="Time to verify" value={duration(a.mttv_ms && Math.round(a.mttv_ms))} sub="avg, by AI triage" tone="accent" />
        <Readout label="Time to fix" value={a.mttr_ms ? `${(a.mttr_ms / 60_000).toFixed(1)}m` : '–'} sub="avg, to verified" />
        <Readout
          label="False positives"
          value={a.funnel.dismissed}
          sub={`${a.funnel.detected ? Math.round((a.funnel.dismissed / a.funnel.detected) * 100) : 0}% filtered by triage`}
        />
        <Readout
          label="Autonomous fixes"
          value={a.funnel.verified}
          tone="ok"
          sub={a.autonomy === null ? 'none yet' : `${Math.round(a.autonomy * 100)}% hands-off`}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-12">
        <div className="grid content-start gap-3 sm:grid-cols-2 xl:col-span-8">
          {cameras.map((c) => (
            <CameraTile key={c.camera} {...c} />
          ))}
          {cameras.length === 0 && (
            <Card className="sm:col-span-2">
              <Empty>Waiting for the first camera to report.</Empty>
            </Card>
          )}
        </div>
        <Card className="flex flex-col overflow-hidden xl:col-span-4">
          <CardHeader
            title="Live incidents"
            right={
              <Link to="/incidents" className="hover:text-fg">
                View all
              </Link>
            }
          />
          <div className="max-h-[34rem] flex-1 divide-y divide-line overflow-y-auto">
            {a.incidents.slice(0, 14).map((i) => (
              <IncidentRow key={i.id} inc={i} compact />
            ))}
            {a.incidents.length === 0 && <Empty>No incidents yet. The floor is clear.</Empty>}
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Relay funnel"
            right={
              <Link to="/history" className="hover:text-fg">
                Analytics
              </Link>
            }
          />
          <Funnel a={a} />
        </Card>
        <Card className="lg:col-span-3">
          <CardHeader title="Zone risk over time" />
          <ZoneHeat a={a} />
        </Card>
      </div>

      <WhyItMatters />
    </div>
  )
}
