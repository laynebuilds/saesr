import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, AlertTriangle } from 'lucide-react'
import { useIncidents } from '../hooks/useIncidents'
import { useCameras } from '../hooks/useCameras'
import { Card, CardHeader, Empty, Dot } from '../components/ui'
import { CameraFeed, CameraTile } from '../components/CameraTile'
import { IncidentRow } from '../components/incident-bits'
import { TYPE_LABEL, ago, cameraLabel, zoneLabel } from '../lib/format'

export function Cameras() {
  const cameras = useCameras(useIncidents())
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {cameras.map((c) => (
        <CameraTile key={c.camera} {...c} />
      ))}
    </div>
  )
}

export function Camera() {
  const { id = '' } = useParams()
  const cameras = useCameras(useIncidents())
  const cam = cameras.find((c) => c.camera === id)
  const incidents = cam?.incidents ?? []
  const open = incidents.filter((i) => !['resolved', 'closed', 'dismissed'].includes(i.status))
  const latest = open[0]

  return (
    <div className="space-y-4">
      <Link to="/cameras" className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" /> Cameras
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight">{cameraLabel(id)}</h1>
        <span className="flex items-center gap-1.5 text-sm text-muted">
          <Dot color={cam?.online === false ? '#ef4444' : '#22c55e'} />
          {cam?.online === false ? 'Offline' : 'Online'}
        </span>
        <span className="ml-auto rounded-lg border border-line px-3 py-1 text-sm">
          Score <span className="font-semibold tabular-nums">{cam ? cam.score.toFixed(0) : '–'}</span>
        </span>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="overflow-hidden lg:col-span-2">
          <div className="relative">
            <CameraFeed camera={id} zone={incidents[0]?.event.zone} incident={incidents[0] ?? null} online={cam?.online !== false} big />
            {latest && (
              <Link
                to={`/incidents/${latest.id}`}
                className="rise flex items-center gap-2 border-t border-line bg-bad-bg px-4 py-2.5 text-sm"
              >
                <AlertTriangle className="size-4 text-sev-4" />
                <span className="font-medium">{TYPE_LABEL[latest.event.type]}</span>
                <span className="text-muted">
                  {zoneLabel(latest.event.zone)} · {ago(latest.event.timestamp)}
                </span>
                <span className="ml-auto text-xs text-muted">Open incident →</span>
              </Link>
            )}
          </div>
        </Card>

        <Card className="self-start overflow-hidden">
          <CardHeader title={`Open incidents (${open.length})`} />
          <div className="divide-y divide-line">
            {incidents.slice(0, 12).map((i) => (
              <IncidentRow key={i.id} inc={i} compact />
            ))}
            {incidents.length === 0 && <Empty>No incidents on this camera.</Empty>}
          </div>
        </Card>
      </div>
    </div>
  )
}
