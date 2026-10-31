import { ExternalLink, X } from 'lucide-react'
import { useDash } from '../data/store'
import { ACTORS_OBSERVER_URL, BACKEND_URL, MOCK } from '../data/config'
import { ago, cameraLabel } from '../lib/format'
import { Dot } from './ui'

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted">{k}</span>
      <span className="text-right font-mono text-xs">{v}</span>
    </div>
  )
}

export function HealthDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const s = useDash((x) => x)
  if (!open) return null
  const cams = s.site?.cameras ?? []
  return (
    <div className="fixed inset-0 z-50" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <aside
        className="slide-in absolute right-0 top-0 h-full w-full max-w-sm overflow-y-auto border-l border-line bg-card p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-semibold">System health</h2>
          <button onClick={onClose} className="text-muted hover:text-fg" aria-label="Close">
            <X className="size-4" />
          </button>
        </div>

        <h3 className="label mt-2">Site actor (Durable Actors)</h3>
        <div className="divide-y divide-line">
          <Row k="Actor" v={`Site/${s.site?.site_id ?? '–'}`} />
          <Row k="Connection" v={s.conn} />
          <Row k="Transport" v={s.transport} />
          <Row k="Site score" v={s.site?.site_score ?? '–'} />
          <Row k="Active incidents" v={s.site?.active_incidents ?? '–'} />
          <Row k="Autonomous fixes" v={s.site?.autonomous_fixes ?? '–'} />
        </div>

        <h3 className="label mt-5">Camera actors</h3>
        <div className="divide-y divide-line">
          {cams.length === 0 && <Row k="No cameras yet" v="" />}
          {cams.map((c) => (
            <div key={c.camera_id} className="py-2">
              <div className="flex items-center justify-between text-sm">
                <span>{cameraLabel(c.camera_id)}</span>
                <span className="flex items-center gap-1.5 font-mono text-xs">
                  <Dot color={c.status === 'online' ? 'var(--ready)' : c.status === 'recovering' ? 'var(--watch)' : 'var(--critical)'} pulse={c.status !== 'online'} />
                  {c.status}
                </span>
              </div>
              <div className="mt-1 grid grid-cols-3 gap-2 font-mono text-[11px] text-dim">
                <span>v{c.version}</span>
                <span>{c.recovered_from_version != null ? `from v${c.recovered_from_version}` : 'no recovery'}</span>
                <span className="text-right">{ago(c.last_heartbeat)}</span>
              </div>
            </div>
          ))}
        </div>

        <h3 className="label mt-5">Agent Relay</h3>
        <div className="divide-y divide-line">
          <Row k="Connected" v={s.agentStatus ? String(s.agentStatus.connected) : '–'} />
          <Row k="Channel" v={s.agentStatus?.channel ? `#${s.agentStatus.channel}` : '–'} />
          <Row k="Agents" v={s.agentStatus?.agents.length ?? 0} />
          {s.agentStatus?.error && <Row k="Error" v={s.agentStatus.error} />}
        </div>
        {!!s.agentStatus?.agents.length && (
          <div className="mt-2 flex flex-wrap gap-1">
            {s.agentStatus.agents.map((a) => (
              <span key={a} className="rounded border border-line px-1.5 py-0.5 font-mono text-[11px] text-muted">
                {a}
              </span>
            ))}
          </div>
        )}

        <div className="mt-6 space-y-2 text-sm">
          <a className="flex items-center gap-1.5 text-accent hover:underline" href={ACTORS_OBSERVER_URL} target="_blank" rel="noreferrer">
            Durable Actors observer <ExternalLink className="size-3.5" />
          </a>
          {!MOCK && (
            <a className="flex items-center gap-1.5 text-accent hover:underline" href={`${BACKEND_URL}/actors/status`} target="_blank" rel="noreferrer">
              Bridge status <ExternalLink className="size-3.5" />
            </a>
          )}
        </div>
      </aside>
    </div>
  )
}
