import { useDash } from '../data/store'
import { useAnalytics } from '../hooks/useAnalytics'
import { Card, CardHeader, Dot, Readout } from '../components/ui'
import { AgentLog } from '../components/incident-bits'
import { AgentTable } from '../components/analytics'
import { duration } from '../lib/format'

const PIPELINE = [
  ['Camera agents', 'post INCIDENT to #safety-floor'],
  ['Triage', 'vision check on the frame'],
  ['Coach', 'coaching and standard tag'],
  ['Fixer', 'policy table plus params'],
  ['Verifier', 'did the problem stop'],
  ['Reporter', 'Slack and the log'],
] as const

export function Agents() {
  const a = useAnalytics()
  const status = useDash((s) => s.agentStatus)
  const activity = useDash((s) => s.activity)
  const totalTokens = a.agents.reduce((s, x) => s + x.tokens, 0)
  const runs = a.agents.reduce((s, x) => s + x.runs, 0)
  const failed = a.agents.reduce((s, x) => s + x.failed, 0)
  const e2e = a.agents.reduce((s, x) => s + (x.avg_ms ?? 0), 0)

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between border-b border-line pb-4">
        <div>
          <div className="label">Agent Relay</div>
          <h1 className="mt-1 text-2xl font-bold tracking-tight">Agents on #{status?.channel ?? 'safety-floor'}</h1>
        </div>
        <span className="flex items-center gap-2 font-mono text-xs text-muted">
          <Dot color={status?.connected ? 'var(--ready)' : 'var(--critical)'} pulse={!status?.connected} />
          {status?.connected ? 'CONNECTED' : 'DISCONNECTED'} · {status?.agents.length ?? 0} AGENTS
        </span>
      </div>

      <div className="stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Readout label="Agent runs" value={runs} sub={`${failed} failed`} />
        <Readout label="Relay latency" value={duration(Math.round(e2e))} sub="sum of step averages" tone="accent" />
        <Readout label="Tokens" value={totalTokens.toLocaleString()} sub="across all steps" />
        <Readout label="Messages" value={activity.length} sub="on Relay this session" />
      </div>

      <Card>
        <CardHeader title="Pipeline" />
        <div className="grid grid-cols-2 gap-px bg-line md:grid-cols-6">
          {PIPELINE.map(([name, role], i) => (
            <div key={name} className="relative bg-card p-4">
              <div className="font-mono text-[10px] text-dim">0{i + 1}</div>
              <div className="mt-1 font-semibold">{name}</div>
              <div className="mt-0.5 text-xs text-muted">{role}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-5">
        <Card className="overflow-hidden lg:col-span-3">
          <CardHeader title="Per-agent latency and cost" />
          <AgentTable a={a} />
        </Card>
        <Card className="overflow-hidden lg:col-span-2">
          <CardHeader title="Live Relay traffic" />
          <AgentLog items={[...activity].reverse().slice(0, 60)} />
        </Card>
      </div>
    </div>
  )
}
