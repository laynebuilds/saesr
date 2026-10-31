import type { Analytics } from '../hooks/useAnalytics'
import { TYPE_LABEL, duration, zoneLabel } from '../lib/format'
import { Donut, Meter } from './ui'

const TYPE_COLORS = ['var(--accent)', 'var(--watch)', '#ea7a3f', 'var(--critical)', '#8aa6c4', 'var(--text-dim)']

export function Funnel({ a }: { a: Analytics }) {
  const f = a.funnel
  const rows: [string, number, string][] = [
    ['Detected', f.detected, 'var(--text-muted)'],
    ['AI triaged', f.triaged, 'var(--accent)'],
    ['Confirmed', f.confirmed, 'var(--watch)'],
    ['Actioned', f.actioned, 'var(--accent)'],
    ['Fixed and verified', f.verified, 'var(--ready)'],
  ]
  const max = Math.max(1, f.detected)
  return (
    <div className="space-y-2.5 p-4">
      {rows.map(([label, n, color]) => (
        <div key={label} className="grid grid-cols-[8.5rem_1fr_3rem] items-center gap-3">
          <span className="text-sm text-muted">{label}</span>
          <div className="h-5 overflow-hidden rounded bg-raised">
            <div className="h-full rounded" style={{ width: `${(n / max) * 100}%`, background: color, opacity: 0.85, transition: 'width .6s var(--ease)' }} />
          </div>
          <span className="tnum text-right font-mono text-sm">{n}</span>
        </div>
      ))}
      <div className="flex items-center justify-between border-t border-line pt-2.5 font-mono text-[11px] text-dim">
        <span>{f.dismissed} FALSE POSITIVES FILTERED</span>
        <span>{f.detected ? Math.round((f.dismissed / f.detected) * 100) : 0}% OF DETECTIONS</span>
      </div>
    </div>
  )
}

export function TypeBreakdown({ a }: { a: Analytics }) {
  const segs = a.byType.map((t, i) => ({ label: TYPE_LABEL[t.type], value: t.count, color: TYPE_COLORS[i % TYPE_COLORS.length] }))
  const total = segs.reduce((s, x) => s + x.value, 0)
  return (
    <div className="flex items-center gap-5 p-4">
      <Donut segments={segs} center={total} centerLabel="confirmed" />
      <ul className="min-w-0 flex-1 space-y-1.5">
        {segs.map((s) => (
          <li key={s.label} className="flex items-center gap-2 text-sm">
            <span className="size-2 shrink-0 rounded-sm" style={{ background: s.color }} />
            <span className="truncate text-muted">{s.label}</span>
            <span className="tnum ml-auto font-mono">{s.value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function SeverityBars({ a }: { a: Analytics }) {
  const max = Math.max(1, ...a.bySeverity.slice(1))
  const colors = ['', 'var(--color-sev-1)', 'var(--color-sev-2)', 'var(--color-sev-3)', 'var(--color-sev-4)', 'var(--color-sev-5)']
  return (
    <div className="flex h-28 items-end gap-2 px-4 pb-3 pt-4">
      {[1, 2, 3, 4, 5].map((s) => (
        <div key={s} className="flex flex-1 flex-col items-center gap-1.5">
          <span className="tnum font-mono text-[11px] text-muted">{a.bySeverity[s]}</span>
          <div className="w-full rounded-t" style={{ height: `${Math.max(3, (a.bySeverity[s] / max) * 64)}px`, background: colors[s], transition: 'height .6s var(--ease)' }} />
          <span className="font-mono text-[10px] text-dim">S{s}</span>
        </div>
      ))}
    </div>
  )
}

/** Zones x time: how risk moves across the floor. Cell opacity = incidents in that slot. */
export function ZoneHeat({ a }: { a: Analytics }) {
  const { zones, buckets, cells } = a.zoneHeat
  const max = Math.max(1, ...cells.flat())
  const fmt = (t: number) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  if (!zones.length) return <div className="p-4 text-sm text-dim">No incidents yet.</div>
  return (
    <div className="p-4">
      <div className="grid gap-y-1.5" style={{ gridTemplateColumns: `7rem repeat(${buckets.length}, 1fr)`, columnGap: 3 }}>
        {zones.map((z, zi) => (
          <div key={z} className="contents">
            <span className="truncate pr-2 font-mono text-[11px] uppercase text-muted">{zoneLabel(z)}</span>
            {cells[zi].map((n, bi) => (
              <span
                key={bi}
                title={`${zoneLabel(z)} ${fmt(buckets[bi])}: ${n}`}
                className="h-5 rounded-[3px]"
                style={{ background: n ? 'var(--critical)' : 'var(--surface-3)', opacity: n ? 0.25 + 0.75 * (n / max) : 0.6 }}
              />
            ))}
          </div>
        ))}
        <span />
        <div className="col-span-full -mt-0.5 flex justify-between pl-[7rem] font-mono text-[10px] text-dim">
          <span>{fmt(buckets[0])}</span>
          <span>{fmt(buckets[Math.floor(buckets.length / 2)])}</span>
          <span>now</span>
        </div>
      </div>
    </div>
  )
}

export function Timeline({ a }: { a: Analytics }) {
  const v = a.perBucket
  const max = Math.max(1, ...v)
  const W = 600
  const H = 120
  const x = (i: number) => (i / (v.length - 1)) * W
  const y = (n: number) => H - 6 - (n / max) * (H - 18)
  const line = v.map((n, i) => `${x(i).toFixed(1)},${y(n).toFixed(1)}`).join(' ')
  return (
    <div className="px-4 pb-3 pt-2">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-32 w-full">
        <defs>
          <linearGradient id="tl-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.3" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1="0" x2={W} y1={H * g} y2={H * g} stroke="var(--border)" strokeDasharray="2 4" vectorEffect="non-scaling-stroke" />
        ))}
        <polygon points={`0,${H} ${line} ${W},${H}`} fill="url(#tl-fill)" />
        <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="1.8" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between font-mono text-[10px] text-dim">
        <span>{Math.round((a.bucketMs * v.length) / 60_000)} MIN AGO</span>
        <span>PEAK {max} / {Math.round(a.bucketMs / 60_000)} MIN</span>
        <span>NOW</span>
      </div>
    </div>
  )
}

export function AgentTable({ a, compact }: { a: Analytics; compact?: boolean }) {
  const slowest = Math.max(1, ...a.agents.map((x) => x.avg_ms ?? 0))
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-line text-left">
          {['Agent', 'Model', 'Runs', 'Avg', 'p95', ...(compact ? [] : ['Tokens', 'Failed'])].map((h) => (
            <th key={h} className="label px-4 py-2 font-medium">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {a.agents.map((s) => (
          <tr key={s.agent}>
            <td className="px-4 py-2 font-medium capitalize">{s.agent}</td>
            <td className="px-4 py-2 font-mono text-xs text-muted">{s.model ?? '–'}</td>
            <td className="tnum px-4 py-2 font-mono">{s.runs}</td>
            <td className="px-4 py-2">
              <div className="flex items-center gap-2">
                <span className="tnum w-14 font-mono">{duration(s.avg_ms === null ? null : Math.round(s.avg_ms))}</span>
                {!compact && <Meter value={(s.avg_ms ?? 0) / slowest} className="w-20" />}
              </div>
            </td>
            <td className="tnum px-4 py-2 font-mono text-muted">{duration(s.p95_ms)}</td>
            {!compact && <td className="tnum px-4 py-2 font-mono text-muted">{s.tokens.toLocaleString()}</td>}
            {!compact && <td className={`tnum px-4 py-2 font-mono ${s.failed ? 'text-bad' : 'text-dim'}`}>{s.failed}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
