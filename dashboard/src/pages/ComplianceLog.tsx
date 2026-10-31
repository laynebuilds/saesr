// Evidence log for insurers and auditors: every confirmed hazard, what was done, who approved it,
// and whether the fix was verified. Dollar figures appear only from the viewer's own assumptions.
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Download } from 'lucide-react'
import { useIncidents } from '../hooks/useIncidents'
import { fixReport, describeAction } from '../lib/fix'
import { ACTION_LABEL, TYPE_LABEL, clock, duration, zoneLabel } from '../lib/format'
import { Button, Card, CardHeader, Empty, Label, Readout } from '../components/ui'
import { StatusChip } from '../components/chips'
import type { IncidentView } from '../contracts/incident'

const KEY = 'saesr-cost-assumptions'
interface Assumptions {
  claimCost: string // $ per claim, viewer's own number
  claimRate: string // % of unaddressed hazards that would become a claim
}
function loadAssumptions(): Assumptions {
  try {
    return { claimCost: '', claimRate: '', ...JSON.parse(localStorage.getItem(KEY) ?? '{}') }
  } catch {
    return { claimCost: '', claimRate: '' }
  }
}

const STANDARD: Record<string, string> = {
  unsafe_lift: 'OSHA ergonomics',
  zone_breach: 'OSHA machine guarding',
  missing_hairnet: 'GMP',
  missing_gloves: 'HACCP',
}

function csv(rows: IncidentView[]): string {
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const head = ['incident_id', 'detected_at', 'type', 'zone', 'camera', 'severity', 'standard', 'status', 'actions', 'approved_by', 'time_to_fix_s', 'verified_at', 'verifier_note']
  const lines = rows.map((i) => {
    const r = fixReport(i)
    return [
      i.id,
      i.event.timestamp,
      i.event.type,
      i.event.zone,
      i.event.camera,
      i.severity,
      i.coach?.standard_tag ?? STANDARD[i.event.type],
      i.status,
      i.actions.map(describeAction).join(' | '),
      i.actions.map((a) => a.approved_by).filter(Boolean).join(' | '),
      r.time_to_fix_ms ? Math.round(r.time_to_fix_ms / 1000) : '',
      r.verified_at ?? '',
      i.verify_summary ?? '',
    ]
      .map(esc)
      .join(',')
  })
  return [head.join(','), ...lines].join('\n')
}

export function ComplianceLog() {
  const all = useIncidents()
  const rows = useMemo(() => all.filter((i) => i.status !== 'dismissed' && i.status !== 'verifying'), [all])
  const [a, setA] = useState<Assumptions>(loadAssumptions)
  const save = (next: Assumptions) => {
    setA(next)
    try {
      localStorage.setItem(KEY, JSON.stringify(next))
    } catch {
      /* private mode */
    }
  }

  const verified = rows.filter((i) => i.status === 'resolved')
  const count = (k: string) => rows.reduce((n, i) => n + i.actions.filter((x) => x.kind === k && x.status === 'done').length, 0)
  const ttf = verified.map((i) => fixReport(i).time_to_fix_ms).filter((x): x is number => x !== null).sort((x, y) => x - y)
  const median = ttf.length ? ttf[Math.floor(ttf.length / 2)] : null

  const cost = parseFloat(a.claimCost)
  const rate = parseFloat(a.claimRate) / 100
  const hasModel = cost > 0 && rate > 0
  const exposure = hasModel ? verified.length * cost * rate : null

  const exportCsv = () => {
    const blob = new Blob([csv(rows)], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `saesr-compliance-log-${new Date().toISOString().slice(0, 10)}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between border-b border-line pb-4">
        <div>
          <Label>Compliance and cost log</Label>
          <h1 className="mt-1 text-2xl font-bold tracking-tight">Evidence for your insurer and auditors</h1>
        </div>
        <Button onClick={exportCsv} disabled={!rows.length}>
          <Download className="size-4" /> Export CSV
        </Button>
      </div>

      <div className="stagger grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Readout label="Hazards logged" value={rows.length} sub="confirmed by AI triage" />
        <Readout label="Verified fixes" value={verified.length} tone="ok" sub={`${rows.length ? Math.round((verified.length / rows.length) * 100) : 0}% of hazards`} />
        <Readout label="Median time to fix" value={duration(median)} sub="detection to verified" />
        <Readout label="Corrective records" value={count('open_corrective_record')} sub="opened with evidence" />
        <Readout label="Coaching assigned" value={count('assign_coaching')} />
        <Readout label="Batch holds" value={count('flag_batch_hold')} sub="food safety" />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader title="Cost exposure, your assumptions" />
          <div className="space-y-3 p-4">
            <p className="text-sm text-muted">Uses your numbers, not ours.</p>
            <label className="block">
              <span className="label">Average cost per claim ($)</span>
              <input
                inputMode="decimal"
                value={a.claimCost}
                onChange={(e) => save({ ...a, claimCost: e.target.value.replace(/[^\d.]/g, '') })}
                placeholder="from your claims history"
                className="mt-1 h-9 w-full rounded-md border border-line bg-card px-3 font-mono text-sm placeholder:font-sans placeholder:text-dim focus:border-accent focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="label">Chance an uncorrected hazard becomes a claim (%)</span>
              <input
                inputMode="decimal"
                value={a.claimRate}
                onChange={(e) => save({ ...a, claimRate: e.target.value.replace(/[^\d.]/g, '') })}
                placeholder="e.g. your near-miss to claim ratio"
                className="mt-1 h-9 w-full rounded-md border border-line bg-card px-3 font-mono text-sm placeholder:font-sans placeholder:text-dim focus:border-accent focus:outline-none"
              />
            </label>
            <div className="rounded-lg border border-line bg-subtle p-3">
              <div className="label">Estimated exposure addressed</div>
              <div className="tnum mt-1 text-2xl font-bold">{exposure === null ? '–' : `$${Math.round(exposure).toLocaleString()}`}</div>
              <div className="mt-1 text-xs text-dim">
                {hasModel ? `${verified.length} verified fixes × $${cost.toLocaleString()} × ${(rate * 100).toFixed(1)}%. An estimate from your inputs, not a measured saving.` : 'Fill in both fields to see an estimate.'}
              </div>
            </div>
            <p className="text-[11px] text-dim">
              Context: lifting-related overexertion costs US employers over $13B a year in workers' comp (Liberty Mutual Workplace Safety Index 2026, 2023 data).
            </p>
          </div>
        </Card>

        <Card className="overflow-hidden lg:col-span-2">
          <CardHeader title={`Log (${rows.length})`} />
          {rows.length === 0 ? (
            <Empty>No confirmed hazards yet.</Empty>
          ) : (
            <div className="max-h-[32rem] overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card">
                  <tr className="border-b border-line text-left">
                    {['Time', 'Hazard', 'Standard', 'Corrective action', 'Approved', 'Fix', 'Status'].map((h) => (
                      <th key={h} className="label px-3 py-2 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {rows.slice(0, 200).map((i) => {
                    const r = fixReport(i)
                    return (
                      <tr key={i.id} className="align-top hover:bg-subtle">
                        <td className="tnum whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">{clock(i.event.timestamp)}</td>
                        <td className="px-3 py-2">
                          <Link to={`/incidents/${i.id}`} className="font-medium hover:underline">
                            {TYPE_LABEL[i.event.type]}
                          </Link>
                          <div className="text-xs text-dim">{zoneLabel(i.event.zone)}</div>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-xs text-muted">{i.coach?.standard_tag ?? STANDARD[i.event.type]}</td>
                        <td className="max-w-xs px-3 py-2 text-xs text-muted" title={i.actions.map(describeAction).join(' ')}>
                          {i.actions.length ? i.actions.map((x) => ACTION_LABEL[x.kind]).join(' · ') : '–'}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-xs text-muted">{i.actions.map((x) => x.approved_by).filter(Boolean).join(', ') || 'auto'}</td>
                        <td className="tnum whitespace-nowrap px-3 py-2 font-mono text-xs">{duration(r.time_to_fix_ms)}</td>
                        <td className="px-3 py-2">
                          <StatusChip status={i.status} />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
