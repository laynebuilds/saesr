import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { fetchHistory, watchHistory, type HistoryRow } from '../data/supabase'
import { MOCK } from '../data/config'
import { useIncidents } from '../hooks/useIncidents'
import { Card, CardHeader, Empty } from '../components/ui'
import { useAnalytics } from '../hooks/useAnalytics'
import { AgentTable, SeverityBars, Timeline, TypeBreakdown } from '../components/analytics'
import { TYPE_LABEL, cameraLabel, clock, zoneLabel } from '../lib/format'
import type { EventType } from '../contracts/event'

function shiftOf(ts: string): string {
  const h = new Date(ts).getHours()
  return h >= 6 && h < 14 ? 'Day' : h >= 14 && h < 22 ? 'Swing' : 'Night'
}

function countBy<T>(rows: T[], key: (r: T) => string, order?: string[]) {
  const m = new Map<string, number>()
  for (const r of rows) m.set(key(r), (m.get(key(r)) ?? 0) + 1)
  const out = [...m].map(([name, count]) => ({ name, count }))
  return order ? order.map((name) => ({ name, count: m.get(name) ?? 0 })) : out.sort((a, b) => b.count - a.count)
}

function Chart({ data }: { data: { name: string; count: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
        <CartesianGrid vertical={false} stroke="var(--line)" />
        <XAxis dataKey="name" tickLine={false} axisLine={false} fontSize={11} stroke="var(--muted)" />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={11} stroke="var(--muted)" />
        <Tooltip
          cursor={{ fill: 'var(--subtle)' }}
          contentStyle={{ background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 8, fontSize: 12 }}
        />
        <Bar dataKey="count" fill="var(--fg)" radius={[4, 4, 0, 0]} maxBarSize={36} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function History() {
  const [rows, setRows] = useState<HistoryRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [type, setType] = useState('')
  const [zone, setZone] = useState('')
  const incidents = useIncidents()
  const a = useAnalytics()

  useEffect(() => {
    if (MOCK) return
    fetchHistory().then(setRows, (e: Error) => setError(e.message))
    return watchHistory((r) => setRows((prev) => [r, ...prev.filter((p) => p.id !== r.id)]))
  }, [])

  // Mock mode: history from the session's incidents, same shape as the Supabase rows.
  const data: HistoryRow[] = useMemo(() => {
    if (!MOCK) return rows
    return incidents.map((i) => ({
      id: i.id,
      camera: i.event.camera,
      type: i.event.type,
      zone: i.event.zone,
      severity: String(i.severity),
      ts: i.event.timestamp,
      status: i.status,
      detail: i.event.detail,
      frame_url: i.event.frame_url,
      bbox: null,
    }))
  }, [rows, incidents])

  const filtered = data.filter((r) => (!type || r.type === type) && (!zone || r.zone === zone))
  const types = [...new Set(data.map((r) => r.type))]
  const zones = [...new Set(data.map((r) => r.zone))]

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-2xl font-bold tracking-tight">Analytics</h1>
        <select value={type} onChange={(e) => setType(e.target.value)} className="h-8 rounded-lg border border-line bg-card px-2 text-sm">
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t as EventType] ?? t}
            </option>
          ))}
        </select>
        <select value={zone} onChange={(e) => setZone(e.target.value)} className="h-8 rounded-lg border border-line bg-card px-2 text-sm">
          <option value="">All zones</option>
          {zones.map((z) => (
            <option key={z} value={z}>
              {zoneLabel(z)}
            </option>
          ))}
        </select>
      </div>
      {error && <Card className="p-3 text-sm text-bad">Supabase: {error}</Card>}

      <div className="grid gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader title="Violations by type" />
          <TypeBreakdown a={a} />
        </Card>
        <Card>
          <CardHeader title="Severity mix" />
          <SeverityBars a={a} />
        </Card>
        <Card>
          <CardHeader title="Incident rate" />
          <Timeline a={a} />
        </Card>
      </div>
      <Card className="overflow-hidden">
        <CardHeader title="Agent latency" />
        <AgentTable a={a} />
      </Card>

      <div className="grid gap-5 md:grid-cols-2">
        <Card>
          <CardHeader title="By type" />
          <div className="p-3">
            <Chart data={countBy(filtered, (r) => TYPE_LABEL[r.type as EventType] ?? r.type)} />
          </div>
        </Card>
        <Card>
          <CardHeader title="By shift" />
          <div className="p-3">
            <Chart data={countBy(filtered, (r) => shiftOf(r.ts), ['Day', 'Swing', 'Night'])} />
          </div>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <CardHeader title={`Incidents (${filtered.length})`} right={<span className="text-xs text-muted">{MOCK ? 'mock session' : 'Supabase log'}</span>} />
        {filtered.length === 0 ? (
          <Empty>No logged incidents.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-medium">Time</th>
                  <th className="px-4 py-2 font-medium">Type</th>
                  <th className="px-4 py-2 font-medium">Zone</th>
                  <th className="px-4 py-2 font-medium">Camera</th>
                  <th className="px-4 py-2 font-medium">Severity</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {filtered.slice(0, 200).map((r) => (
                  <tr key={r.id} className="hover:bg-subtle">
                    <td className="px-4 py-2 font-mono text-xs">{clock(r.ts)}</td>
                    <td className="px-4 py-2">
                      <Link to={`/incidents/${r.id}`} className="hover:underline">
                        {TYPE_LABEL[r.type as EventType] ?? r.type}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-muted">{zoneLabel(r.zone)}</td>
                    <td className="px-4 py-2 text-muted">{cameraLabel(r.camera)}</td>
                    <td className="px-4 py-2">{r.severity}</td>
                    <td className="px-4 py-2 text-muted">{r.status ?? '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
